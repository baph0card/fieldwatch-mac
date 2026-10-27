"""Signature matching. A Python port of Fieldwatch's SignatureEngine (Kotlin, MIT).

A fleet is a named signature (e.g. "Apple AirTags") made of rules. With matchAny=True any rule
hitting is enough; with matchAny=False every rule must hit. Fleets with minPeers / clusterByOui /
sequentialMac only fire when enough similar radios are in range at once.

macOS does not expose BLE addresses, so OUI / MAC_PREFIX rules can only match Wi-Fi BSSIDs here.
Name, service UUID, manufacturer and service-data rules work for both radios.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .models import BLE, WIFI, Sighting, hex_only, uuid_aliases, wifi_oui24_universal

PROTOCOL_IE_OUIS = {"0050F2", "000FAC"}  # WPA / RSN tags: protocol markers, not a product
TESLA_IBEACON_MFG_PREFIX = "021574278BDAB64445208F0C720EAF059935"
APPLE_CONTINUITY_PREFIXES = {"05", "07", "08", "09", "0A", "0B", "0C", "0D", "0E", "0F", "10"}
APPLE = 0x004C


def _glob_to_regex(pattern: str) -> re.Pattern:
    body = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.compile(f"^{body}$", re.IGNORECASE | re.DOTALL)


def _mfg(d: Sighting) -> list[tuple[int, str]]:
    return d.mfg_records


def _service_data_hits(d: Sighting, aliases: set[str], prefix: str, contains: bool) -> bool:
    needles = [prefix]
    if contains:
        h = hex_only(prefix)
        if len(h) >= 2 and len(h) % 2 == 0:
            rev = "".join(h[i : i + 2] for i in range(len(h) - 2, -2, -2))
            if rev != prefix:
                needles.append(rev)
    for uuid, data_hex in d.service_data:
        if aliases and not (uuid_aliases(uuid) & aliases):
            continue
        data = hex_only(data_hex)
        if contains:
            if any(n in data for n in needles):
                return True
        elif data.startswith(prefix):
            return True
    return False


def _have_uuids(d: Sighting) -> list[str]:
    return list(d.service_uuids) + [u for u, _ in d.service_data]


@dataclass
class Rule:
    kind: str
    text: str
    company_id: int
    prefix: str
    radio: str | None  # WIFI | BLE | None
    regex: re.Pattern | None = None
    aliases: frozenset = frozenset()
    contains: bool = False

    def scope(self) -> str | None:
        if self.kind in ("VENDOR_IE_OUI", "HIDDEN_SSID"):
            return WIFI
        if self.kind == "RADIO_KIND":
            return self.radio
        if self.kind in ("SERVICE_UUID", "SERVICE_DATA", "MANUFACTURER_ID", "MANUFACTURER_DATA"):
            return self.radio or BLE
        return self.radio


def _compile_rule(raw: dict) -> Rule:
    kind = raw["kind"]
    text = raw.get("text") or ""
    r = Rule(kind, text, int(raw.get("companyId") or 0), hex_only(raw.get("dataPrefixHex") or ""), raw.get("radio"))
    if kind == "NAME_GLOB" and text:
        r.regex = _glob_to_regex(text)
    elif kind == "SERVICE_UUID" and text:
        r.aliases = frozenset(uuid_aliases(text))
    elif kind == "SERVICE_DATA":
        r.aliases = frozenset(a for a in uuid_aliases(text) if a) if text else frozenset()
        r.contains = not text and bool(r.prefix)
    return r


class _Plan:
    __slots__ = ("id", "fleet", "rules", "other", "cluster", "match_any", "idx")

    def __init__(self, idx: int, fleet: dict, rules: list[Rule]):
        self.idx = idx
        self.id = fleet["id"]
        self.fleet = fleet
        self.rules = rules
        self.match_any = fleet.get("matchAny", True)
        self.other: list[Rule] = []
        self.cluster = bool(fleet.get("minPeers", 0) > 0 or fleet.get("clusterByOui") or fleet.get("sequentialMac"))


class SignatureEngine:
    def __init__(self, fleets: list[dict]):
        self.fleets = [f for f in fleets if f.get("enabled", True)]
        self.plans: list[_Plan] = []
        self.by_id: dict[str, dict] = {f["id"]: f for f in self.fleets}
        self._bssid_wifi: dict[str, list[int]] = {}
        self._bssid_ble: dict[str, list[int]] = {}
        self._vendor_ie: dict[str, list[int]] = {}
        self._long: list[tuple[str, str | None, bool, bool, int]] = []
        self._wifi_plans: list[int] = []
        self._ble_plans: list[int] = []
        self._compile()

    @classmethod
    def from_file(cls, path: Path) -> "SignatureEngine":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["fleets"] if isinstance(data, dict) else data)

    def _index(self, text: str, radio: str | None, idx: int, bssid: bool, vendor: bool) -> None:
        h = hex_only(text)
        if not h:
            return
        if len(h) != 6:
            self._long.append((h, radio, bssid, vendor, idx))
            return
        if bssid:
            if radio != BLE:
                self._bssid_wifi.setdefault(h, []).append(idx)
            if radio != WIFI:
                self._bssid_ble.setdefault(h, []).append(idx)
        if vendor and radio != BLE and h not in PROTOCOL_IE_OUIS:
            self._vendor_ie.setdefault(h, []).append(idx)

    def _compile(self) -> None:
        for idx, fleet in enumerate(self.fleets):
            active = [_compile_rule(r) for r in fleet.get("rules", []) if r.get("enabled", True)]
            plan = _Plan(idx, fleet, active)
            for r in active:
                if r.kind == "OUI":
                    self._index(r.text, r.radio, idx, True, True)
                elif r.kind == "MAC_PREFIX":
                    self._index(r.text, r.radio, idx, True, False)
                elif r.kind == "VENDOR_IE_OUI":
                    self._index(r.text, r.radio, idx, False, True)
                else:
                    plan.other.append(r)
            self.plans.append(plan)
            scopes = {r.scope() for r in active}
            if scopes == {WIFI}:
                self._wifi_plans.append(idx)
            elif scopes == {BLE}:
                self._ble_plans.append(idx)
            else:
                self._wifi_plans.append(idx)
                self._ble_plans.append(idx)

    # ---- matching -------------------------------------------------------------------------

    def _oui_hits(self, d: Sighting) -> set[int]:
        hits: set[int] = set()
        mac = hex_only(d.mac)
        bssid_map = self._bssid_wifi if d.kind == WIFI else self._bssid_ble
        if len(mac) >= 6:
            hits.update(bssid_map.get(mac[:6], ()))
        if d.kind == WIFI:
            univ = wifi_oui24_universal(d.mac)
            if univ:
                hits.update(bssid_map.get(univ, ()))
            for ie in d.vendor_ie_ouis:
                h = hex_only(ie)
                if len(h) >= 6 and h[:6] not in PROTOCOL_IE_OUIS:
                    hits.update(self._vendor_ie.get(h[:6], ()))
        if self._long and mac:
            for hx, radio, bssid, vendor, idx in self._long:
                if radio is not None and radio != d.kind:
                    continue
                if bssid and mac.startswith(hx):
                    hits.add(idx)
                if vendor and d.kind == WIFI:
                    for ie in d.vendor_ie_ouis:
                        h = hex_only(ie)
                        if h[:6] not in PROTOCOL_IE_OUIS and h.startswith(hx):
                            hits.add(idx)
        return hits

    def _rule_hits(self, d: Sighting, r: Rule) -> bool:
        if r.kind != "RADIO_KIND" and r.radio is not None and d.kind != r.radio:
            return False
        k = r.kind
        if k in ("OUI", "MAC_PREFIX"):
            mac = hex_only(d.mac)
            if mac and r.text and mac.startswith(hex_only(r.text)):
                return True
            if k == "OUI" and d.kind == WIFI:
                want = hex_only(r.text)
                if want and want[:6] not in PROTOCOL_IE_OUIS:
                    if any(hex_only(ie).startswith(want) for ie in d.vendor_ie_ouis if hex_only(ie)[:6] not in PROTOCOL_IE_OUIS):
                        return True
                univ = wifi_oui24_universal(d.mac)
                if univ and len(want) == 6 and univ == want:
                    return True
            return False
        if k == "NAME_CONTAINS":
            return bool(d.name) and bool(r.text) and r.text.lower() in d.name.lower()
        if k == "NAME_GLOB":
            return bool(d.name) and r.regex is not None and bool(r.regex.match(d.name))
        if k == "SERVICE_UUID":
            return bool(r.aliases) and any(uuid_aliases(u) & r.aliases for u in _have_uuids(d))
        if k == "MANUFACTURER_ID":
            return any(cid == r.company_id for cid, _ in _mfg(d))
        if k == "MANUFACTURER_DATA":
            return bool(r.prefix) and any(
                (r.company_id == 0 or cid == r.company_id) and hex_only(data).startswith(r.prefix) for cid, data in _mfg(d)
            )
        if k == "SERVICE_DATA":
            if not r.prefix and not r.aliases:
                return False
            return _service_data_hits(d, set(r.aliases), r.prefix, r.contains)
        if k == "RADIO_KIND":
            return r.radio is None or d.kind == r.radio
        if k == "HIDDEN_SSID":
            return d.hidden_ssid
        if k == "VENDOR_IE_OUI":
            if d.kind != WIFI:
                return False
            want = hex_only(r.text)
            if not want or want[:6] in PROTOCOL_IE_OUIS:
                return False
            return any(hex_only(ie).startswith(want) for ie in d.vendor_ie_ouis if hex_only(ie)[:6] not in PROTOCOL_IE_OUIS)
        return False

    def _fleet_hits(self, d: Sighting, plan: _Plan, oui_hit: bool) -> bool:
        if plan.id == "fleet-ibeacon" and _is_tesla_phone_key(d):
            return False
        if not plan.rules:
            return False
        if not plan.match_any:
            return all(self._rule_hits(d, r) for r in plan.rules)
        if oui_hit:
            return True
        return any(self._rule_hits(d, r) for r in plan.other)

    def match(self, devices: list[Sighting], now: float | None = None) -> dict[str, set[str]]:
        now = time.time() if now is None else now
        out: dict[str, set[str]] = {}
        oui_cache: dict[str, set[int]] = {}
        for d in devices:
            oh = self._oui_hits(d)
            oui_cache[d.key] = oh
            hits: set[str] = set()
            for idx in self._wifi_plans if d.kind == WIFI else self._ble_plans:
                plan = self.plans[idx]
                if not plan.cluster and self._fleet_hits(d, plan, idx in oh):
                    hits.add(plan.id)
            self._drop_overlaps(hits, d)
            out[d.key] = hits
        self._apply_clusters(devices, oui_cache, out, now)
        return out

    # ---- overlap rules (same precedence as the Android app) -------------------------------

    def _drop_overlaps(self, hits: set[str], d: Sighting) -> None:
        if "fleet-ibeacon" in hits:
            beacon_class = lambda fid: self.by_id.get(fid, {}).get("kind") == "BEACON"
            if any(fid != "fleet-ibeacon" and not beacon_class(fid) for fid in hits):
                hits.discard("fleet-ibeacon")
        if "fleet-osmo" in hits or "fleet-dji-power" in hits:
            hits.discard("fleet-dji")
        if "fleet-polar" in hits:
            hits.discard("fleet-gopro")
        if "fleet-meraki" in hits:
            hits.discard("fleet-cisco")
        if "fleet-airtag" in hits and "airtag" not in d.name.lower():
            fd44 = uuid_aliases("FD44")
            if not any(uuid_aliases(u) & fd44 for u in _have_uuids(d)):
                if "fleet-apple-device" in hits or "fleet-apple-audio" in hits or is_apple_continuity(d):
                    hits.discard("fleet-airtag")

    def _apply_clusters(self, devices, oui_cache, out, now):
        for plan in self.plans:
            if not plan.cluster:
                continue
            f = plan.fleet
            window = (f.get("peerWindowSec") or 60)
            live = [d for d in devices if now - d.last_seen <= window]
            need = f.get("minPeers") or 3
            by_oui = bool(f.get("clusterByOui"))
            seq = bool(f.get("sequentialMac"))
            for a in live:
                eligible = (
                    not plan.rules
                    or self._fleet_hits(a, plan, plan.idx in oui_cache.get(a.key, ()))
                    or (by_oui and (not a.name or a.randomized))
                )
                if not eligible:
                    continue
                peers = 1
                for b in live:
                    if b.key == a.key:
                        continue
                    if by_oui and a.oui != b.oui:
                        continue
                    if seq and abs(_last16(a.mac) - _last16(b.mac)) > 64:
                        continue
                    if plan.rules and not by_oui and not self._fleet_hits(b, plan, plan.idx in oui_cache.get(b.key, ())):
                        continue
                    peers += 1
                if peers >= need:
                    out.setdefault(a.key, set()).add(plan.id)


def _last16(mac: str) -> int:
    h = hex_only(mac)
    return int(h[-4:], 16) if len(h) >= 4 else 0


def is_apple_continuity(d: Sighting) -> bool:
    return any(cid == APPLE and hex_only(data)[:2] in APPLE_CONTINUITY_PREFIXES for cid, data in d.mfg_records)


def _is_tesla_phone_key(d: Sighting) -> bool:
    return any(cid == APPLE and hex_only(data).startswith(TESLA_IBEACON_MFG_PREFIX) for cid, data in d.mfg_records)
