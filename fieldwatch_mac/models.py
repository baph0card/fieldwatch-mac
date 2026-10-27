from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

WIFI = "WIFI"
BLE = "BLE"


def hex_only(raw: str) -> str:
    """Uppercase hex/alphanumerics only. Mirrors the Android app's hexOnly()."""
    return re.sub(r"[^0-9A-Za-z]", "", raw or "").upper()


def normalize_mac(raw: str) -> str:
    h = hex_only(raw)
    if len(h) < 2:
        return (raw or "").upper()
    return ":".join(h[i : i + 2] for i in range(0, len(h) - len(h) % 2, 2))


def is_randomized(mac: str) -> bool:
    """Locally administered, unicast."""
    h = hex_only(mac)
    if len(h) < 2:
        return False
    first = int(h[:2], 16)
    return bool(first & 0x02) and not (first & 0x01)


def wifi_oui24_universal(mac: str) -> str | None:
    """OUI with the locally-administered bit cleared (virtual BSSIDs on mesh/guest SSIDs)."""
    h = hex_only(mac)
    if len(h) < 6:
        return None
    first = int(h[:2], 16)
    if not (first & 0x02) or (first & 0x01):
        return None
    return "%02X" % (first & 0xFD) + h[2:6]


def uuid_aliases(raw: str) -> set[str]:
    """All spellings of a Bluetooth UUID that should compare equal (16-bit short form <-> full)."""
    hx = hex_only(raw)
    out = {(raw or "").upper(), hx}
    if len(hx) == 4:
        out |= {"0x" + hx, f"0000{hx}-0000-1000-8000-00805F9B34FB", f"0000{hx}00001000800000805F9B34FB"}
        return out
    if len(hx) == 32 and hx.startswith("0000") and hx.endswith("00001000800000805F9B34FB"):
        short = hx[4:8]
        out |= {short, "0x" + short, f"0000{short}-0000-1000-8000-00805F9B34FB"}
    return out


@dataclass
class Sighting:
    """One radio the scanner has heard (a Wi-Fi BSSID or a BLE advertiser)."""

    key: str
    kind: str  # WIFI | BLE
    mac: str = ""  # BSSID for Wi-Fi. Empty for BLE: macOS never exposes BLE addresses.
    name: str = ""
    rssi: int = -127
    rssi_max: int = -127
    rssi_min: int = 0
    rssi_avg: float = 0.0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    count: int = 0

    # Wi-Fi
    channel: int | None = None
    band: str = ""
    security: str = ""
    standards: str = ""
    country: str = ""
    hidden_ssid: bool = False
    vendor_ie_ouis: list[str] = field(default_factory=list)

    # BLE
    service_uuids: list[str] = field(default_factory=list)
    mfg_records: list[tuple[int, str]] = field(default_factory=list)  # (company_id, data hex)
    service_data: list[tuple[str, str]] = field(default_factory=list)  # (uuid, data hex)
    tx_power: int | None = None

    # Derived
    vendor: str | None = None
    randomized: bool = False
    fleets: set[str] = field(default_factory=set)

    @property
    def oui(self) -> str:
        h = hex_only(self.mac)
        return ":".join(h[i : i + 2] for i in (0, 2, 4)) if len(h) >= 6 else ""

    def observe_rssi(self, rssi: int, now: float) -> None:
        self.rssi = rssi
        self.last_seen = now
        self.count += 1
        if self.count == 1:
            self.rssi_max = rssi
            self.rssi_min = rssi
            self.rssi_avg = float(rssi)
        else:
            self.rssi_max = max(self.rssi_max, rssi)
            self.rssi_min = min(self.rssi_min, rssi)
            self.rssi_avg += 0.2 * (rssi - self.rssi_avg)
