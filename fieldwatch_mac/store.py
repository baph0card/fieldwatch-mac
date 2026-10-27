"""Thread-safe device table that scanners feed and the web UI reads."""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from pathlib import Path

from . import wifi_ie
from .models import BLE, WIFI, Sighting, hex_only, is_randomized, normalize_mac
from .radiodb import RadioDb
from .signatures import SignatureEngine

LOG_INTERVAL_S = 10.0


def _band(channel: int | None, freq_mhz: int | None = None) -> str:
    if freq_mhz:
        return "2.4 GHz" if freq_mhz < 3000 else "5 GHz" if freq_mhz < 5925 else "6 GHz"
    if channel is None:
        return ""
    return "2.4 GHz" if channel <= 14 else "5/6 GHz"


class DeviceStore:
    def __init__(self, engine: SignatureEngine, radiodb: RadioDb | None, log_path: Path | None = None):
        self.engine = engine
        self.radiodb = radiodb
        self.lock = threading.RLock()
        self.devices: dict[str, Sighting] = {}
        self.started = time.time()
        self.wifi_status = {"state": "starting", "detail": "", "updated": 0.0}
        self.ble_status = {"state": "starting", "detail": "", "updated": 0.0}
        self._matched: dict[str, set[str]] = {}
        self._matched_at = 0.0
        self._dirty = True
        self._log_path = log_path
        self._log_fh = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None
        self._last_logged: dict[str, float] = {}

    # ---- feeding --------------------------------------------------------------------------

    def set_status(self, radio: str, state: str, detail: str = "") -> None:
        with self.lock:
            tgt = self.wifi_status if radio == WIFI else self.ble_status
            tgt.update(state=state, detail=detail, updated=time.time())

    def update_wifi(self, networks: list[dict], now: float | None = None) -> None:
        """networks: dicts from the Swift helper: bssid, ssid, rssi, channel, freq, noise, ie (base64)."""
        import base64

        now = time.time() if now is None else now
        with self.lock:
            for n in networks:
                bssid = normalize_mac(n.get("bssid") or "")
                if not bssid or len(bssid) < 17:
                    continue
                key = f"wifi:{bssid}"
                d = self.devices.get(key)
                if d is None:
                    d = Sighting(key=key, kind=WIFI, mac=bssid, first_seen=now, last_seen=now)
                    d.randomized = is_randomized(bssid)
                    d.vendor = self._vendor(bssid)
                    self.devices[key] = d
                ie_raw = n.get("ie")
                info = wifi_ie.parse(base64.b64decode(ie_raw) if ie_raw else None)
                ssid = n.get("ssid") or ""
                if not ssid and info.ssid_bytes:
                    ssid = info.ssid_bytes.decode("utf-8", "replace")
                ssid_bytes_blank = info.ssid_bytes is not None and (len(info.ssid_bytes) == 0 or set(info.ssid_bytes) == {0})
                d.name = ssid
                d.hidden_ssid = (not ssid) and (ssid_bytes_blank or info.ssid_bytes is None)
                d.channel = n.get("channel") or info.channel
                d.band = _band(d.channel, n.get("freq"))
                if n.get("band"):
                    d.band = n["band"]
                d.security = info.security if ie_raw else d.security
                d.standards = info.standards or d.standards
                d.country = info.country or n.get("country") or d.country
                if info.vendor_ouis:
                    d.vendor_ie_ouis = info.vendor_ouis
                d.observe_rssi(int(n.get("rssi", -127)), now)
            self._dirty = True
        self._log_batch(WIFI, now)

    def update_ble(self, address: str, name: str | None, rssi: int, mfg: dict[int, bytes], svc_data: dict[str, bytes],
                   svc_uuids: list[str], tx_power: int | None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        key = f"ble:{address}"
        with self.lock:
            d = self.devices.get(key)
            if d is None:
                d = Sighting(key=key, kind=BLE, first_seen=now, last_seen=now)
                self.devices[key] = d
            # BLE ads arrive in pieces (name in a scan response, data in the ad itself): merge, never erase.
            if name:
                d.name = name
            if mfg:
                known = dict(d.mfg_records)
                for cid, data in mfg.items():
                    known[cid] = data.hex().upper()
                d.mfg_records = list(known.items())
            if svc_data:
                known_sd = dict(d.service_data)
                for uuid, data in svc_data.items():
                    known_sd[uuid.upper()] = data.hex().upper()
                d.service_data = list(known_sd.items())
            if svc_uuids:
                merged = list(dict.fromkeys(list(d.service_uuids) + [u.upper() for u in svc_uuids]))
                d.service_uuids = merged
            if tx_power is not None:
                d.tx_power = tx_power
            if d.mfg_records and not d.vendor:
                d.vendor = self._company(d.mfg_records[0][0])
            d.observe_rssi(int(rssi), now)
            self._dirty = True

    def _vendor(self, mac: str) -> str | None:
        return self.radiodb.vendor_for_mac(hex_only(mac)) if self.radiodb else None

    def _company(self, cid: int) -> str | None:
        return self.radiodb.company(cid) if self.radiodb else None

    # ---- reading --------------------------------------------------------------------------

    def _rematch(self, now: float) -> None:
        if not self._dirty and now - self._matched_at < 5:
            return
        devs = list(self.devices.values())
        self._matched = self.engine.match(devs, now)
        for d in devs:
            d.fleets = self._matched.get(d.key, set())
        self._matched_at = now
        self._dirty = False

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            self._rematch(now)
            rows = []
            for d in self.devices.values():
                fleets = []
                for fid in sorted(d.fleets):
                    f = self.engine.by_id.get(fid, {})
                    fleets.append({"id": fid, "name": f.get("name", fid), "kind": f.get("kind") or "OTHER",
                                   "color": f.get("colorIndex", 0), "note": f.get("attentionNote") or ""})
                rows.append({
                    "key": d.key, "radio": d.kind, "mac": d.mac, "name": d.name, "hidden": d.hidden_ssid,
                    "vendor": d.vendor or "", "rssi": d.rssi, "rssi_max": d.rssi_max, "rssi_avg": round(d.rssi_avg, 1),
                    "first_seen": d.first_seen, "last_seen": d.last_seen, "age": round(now - d.last_seen, 1),
                    "count": d.count, "channel": d.channel, "band": d.band, "security": d.security,
                    "standards": d.standards, "country": d.country, "randomized": d.randomized,
                    "services": [self._svc_label(u) for u in d.service_uuids],
                    "mfg": [{"id": c, "name": self._company(c) or "", "data": h[:48]} for c, h in d.mfg_records],
                    "tx_power": d.tx_power, "vendor_ies": d.vendor_ie_ouis, "fleets": fleets,
                })
            return {
                "now": now, "uptime": round(now - self.started, 1),
                "wifi": dict(self.wifi_status), "ble": dict(self.ble_status),
                "catalog": len(self.engine.fleets), "devices": rows,
            }

    def _svc_label(self, uuid: str) -> str:
        h = hex_only(uuid)
        if self.radiodb and (len(h) == 4 or (len(h) == 32 and h.startswith("0000") and h.endswith("00001000800000805F9B34FB"))):
            short = h if len(h) == 4 else h[4:8]
            name = self.radiodb.service(int(short, 16))
            return f"{short} {name}" if name else short
        return uuid

    # ---- export / log ---------------------------------------------------------------------

    def export_csv(self) -> str:
        snap = self.snapshot()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["radio", "name", "bssid", "vendor", "rssi", "rssi_max", "channel", "band", "security", "matched",
                    "services", "manufacturer", "first_seen", "last_seen", "count"])
        for r in sorted(snap["devices"], key=lambda r: -r["rssi"]):
            w.writerow([
                r["radio"], r["name"], r["mac"], r["vendor"], r["rssi"], r["rssi_max"], r["channel"] or "", r["band"],
                r["security"], "; ".join(f["name"] for f in r["fleets"]), "; ".join(r["services"]),
                "; ".join(f"{m['id']:04X} {m['name']}".strip() for m in r["mfg"]),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["first_seen"])),
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["last_seen"])), r["count"],
            ])
        return out.getvalue()

    def _log_batch(self, radio: str, now: float) -> None:
        if not self._log_fh:
            return
        snap = self.snapshot(now)
        for r in snap["devices"]:
            if r["radio"] != radio:
                continue
            if now - self._last_logged.get(r["key"], 0) < LOG_INTERVAL_S:
                continue
            self._last_logged[r["key"]] = now
            self._log_fh.write(json.dumps({"t": round(now, 1), **{k: r[k] for k in (
                "radio", "name", "mac", "vendor", "rssi", "channel", "security")}, "matched": [f["name"] for f in r["fleets"]]}) + "\n")

    def log_ble_tick(self) -> None:
        self._log_batch(BLE, time.time())

    def close(self) -> None:
        if self._log_fh:
            self._log_fh.close()
