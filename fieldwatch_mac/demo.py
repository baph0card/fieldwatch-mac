"""Synthetic devices so the UI and matcher can be exercised without radios (`--demo`)."""

from __future__ import annotations

import base64
import random
import threading
import time

from .models import BLE, WIFI
from .store import DeviceStore


def _ie(ssid: bytes, channel: int, vendor_ouis: list[bytes], rsn: bool = True, he: bool = False) -> str:
    b = bytes([0, len(ssid)]) + ssid + bytes([3, 1, channel])
    if rsn:
        # RSN: v1, CCMP group, 1 pairwise CCMP, 1 AKM PSK
        body = b"\x01\x00" + b"\x00\x0f\xac\x04" + b"\x01\x00" + b"\x00\x0f\xac\x04" + b"\x01\x00" + b"\x00\x0f\xac\x02" + b"\x00\x00"
        b += bytes([48, len(body)]) + body
    if he:
        b += bytes([255, 3, 35, 0, 0])
    for oui in vendor_ouis:
        b += bytes([221, 4]) + oui + b"\x01"
    return base64.b64encode(b).decode()


WIFI_FIXTURES = [
    # bssid, ssid, channel, band, base rssi, vendor-IE OUIs
    ("DC:A6:32:11:22:33", "HomeNet-5G", 36, "5 GHz", -48, [], True),
    ("00:0F:B3:AA:BB:01", "Actiontec-7C2F", 6, "2.4 GHz", -62, [bytes.fromhex("000FB3")], False),
    ("F0:9F:C2:01:02:03", "", 149, "5 GHz", -71, [], False),
    ("3C:84:6A:55:66:77", "TP-Link_EF01", 1, "2.4 GHz", -77, [], False),
    ("AA:BB:CC:00:11:22", "<script>alert(1)</script>", 11, "2.4 GHz", -80, [], False),
]

BLE_FIXTURES = [
    # id, name, base rssi, mfg {cid: bytes}, service uuids, service data
    ("AAAA-0001", None, -58, {0x004C: bytes.fromhex("12190001020304050607080910111213")}, [], {}),
    ("AAAA-0002", "Tile", -66, {}, ["0000feed-0000-1000-8000-00805f9b34fb"], {}),
    ("AAAA-0003", None, -72, {0x004C: bytes.fromhex("10050a1c2d3e4f")}, [], {}),
    ("AAAA-0004", "Polar H10 8E2A1C", -83, {0x006B: b"\x00"}, ["0000180d-0000-1000-8000-00805f9b34fb"], {}),
    ("AAAA-0005", "Unknown", -90, {}, [], {}),
]


class DemoFeed(threading.Thread):
    def __init__(self, store: DeviceStore):
        super().__init__(name="demo", daemon=True)
        self.store = store
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        self.store.set_status(WIFI, "scanning", "demo data")
        self.store.set_status(BLE, "scanning", "demo data")
        rnd = random.Random(7)
        while not self._halt.is_set():
            nets = []
            for bssid, ssid, ch, band, base, ouis, he in WIFI_FIXTURES:
                nets.append({"bssid": bssid, "ssid": ssid, "rssi": base + rnd.randint(-3, 3), "channel": ch, "band": band,
                             "ie": _ie(ssid.encode(), ch, ouis, he=he)})
            self.store.update_wifi(nets)
            for addr, name, base, mfg, uuids, sd in BLE_FIXTURES:
                self.store.update_ble(addr, name, base + rnd.randint(-4, 4), mfg, sd, uuids, None)
            time.sleep(1.5)
