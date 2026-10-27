import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fieldwatch_mac import wifi_ie  # noqa: E402
from fieldwatch_mac.demo import _ie  # noqa: E402
from fieldwatch_mac.models import BLE, WIFI, Sighting, uuid_aliases, wifi_oui24_universal  # noqa: E402
from fieldwatch_mac.radiodb import RadioDb  # noqa: E402
from fieldwatch_mac.server import make_server, serve_in_thread  # noqa: E402
from fieldwatch_mac.signatures import SignatureEngine  # noqa: E402
from fieldwatch_mac.store import DeviceStore  # noqa: E402
from fieldwatch_mac.wifi_source import WifiSource  # noqa: E402

ENGINE = SignatureEngine.from_file(ROOT / "data" / "signatures.json")
DB = RadioDb(ROOT / "data" / "radiodb.bin")


def ble(key="b1", name="", mfg=(), uuids=(), sd=()):
    return Sighting(key=key, kind=BLE, name=name, mfg_records=list(mfg), service_uuids=list(uuids), service_data=list(sd))


def wifi(mac, name="", ies=()):
    return Sighting(key="w" + mac, kind=WIFI, mac=mac, name=name, vendor_ie_ouis=list(ies))


def fleets_for(*devs):
    res = ENGINE.match(list(devs))
    return {d.key: res[d.key] for d in devs}


class RadioDbTests(unittest.TestCase):
    def test_oui_company_service(self):
        self.assertIn("Actiontec", DB.vendor_for_mac("000FB3AABBCC") or "")
        self.assertIn("Apple", DB.company(0x004C) or "")
        self.assertTrue(DB.service(0x180D))
        self.assertIsNone(DB.vendor_for_mac("zz"))


class ModelTests(unittest.TestCase):
    def test_uuid_aliases(self):
        self.assertTrue(uuid_aliases("FEED") & uuid_aliases("0000feed-0000-1000-8000-00805f9b34fb"))
        self.assertFalse(uuid_aliases("FEED") & uuid_aliases("FEEC"))

    def test_universal_oui(self):
        self.assertEqual(wifi_oui24_universal("02:0F:B3:00:00:00"), "000FB3")
        self.assertIsNone(wifi_oui24_universal("00:0F:B3:00:00:00"))


class EngineTests(unittest.TestCase):
    def test_findmy_payload_is_airtag(self):
        d = ble(mfg=[(0x004C, "12190001020304")])
        self.assertIn("fleet-airtag", fleets_for(d)[d.key])

    def test_airtag_dropped_for_continuity_device(self):
        d = ble(mfg=[(0x004C, "10050A1C2D3E4F"), (0x004C, "12190001")])
        self.assertNotIn("fleet-airtag", fleets_for(d)[d.key])

    def test_airtag_kept_when_named(self):
        d = ble(name="AirTag", mfg=[(0x004C, "10050A1C")])
        self.assertIn("fleet-airtag", fleets_for(d)[d.key])

    def test_tile_by_service_uuid_any_spelling(self):
        d = ble(uuids=["0000FEED-0000-1000-8000-00805F9B34FB"])
        self.assertIn("fleet-tile", fleets_for(d)[d.key])

    def test_polar_beats_gopro(self):
        d = ble(name="Polar H10 1234", uuids=["FEA5"], mfg=[(0x006B, "00")])
        got = fleets_for(d)[d.key]
        self.assertIn("fleet-polar", got)
        self.assertNotIn("fleet-gopro", got)

    def test_wifi_oui_and_ssid_glob(self):
        d = wifi("00:0F:B3:AA:BB:01", "Actiontec-7C2F")
        self.assertIn("fleet-actiontec", fleets_for(d)[d.key])

    def test_virtual_bssid_recovers_oui(self):
        d = wifi("02:0F:B3:AA:BB:01")  # local bit set on an Actiontec prefix
        self.assertIn("fleet-actiontec", fleets_for(d)[d.key])

    def test_wifi_rule_does_not_hit_ble(self):
        d = ble(name="Actiontec-1")
        self.assertNotIn("fleet-actiontec", fleets_for(d)[d.key])

    def test_meraki_suppresses_cisco(self):
        d = wifi("00:18:0A:00:00:01", ies=["00:00:0C"])
        got = fleets_for(d)[d.key]
        self.assertIn("fleet-meraki", got)
        self.assertNotIn("fleet-cisco", got)

    def test_plain_device_matches_nothing(self):
        d = ble(name="Some Speaker")
        self.assertEqual(fleets_for(d)[d.key], set())


class IeTests(unittest.TestCase):
    def test_parse_wpa2_with_vendor_ie(self):
        raw = base64.b64decode(_ie(b"Home", 36, [bytes.fromhex("000FB3")], he=True))
        info = wifi_ie.parse(raw)
        self.assertEqual(info.ssid_bytes, b"Home")
        self.assertEqual(info.channel, 36)
        self.assertEqual(info.security, "WPA2")
        self.assertEqual(info.standards, "Wi-Fi 6")
        self.assertEqual(info.vendor_ouis, ["00:0F:B3"])

    def test_garbage_does_not_raise(self):
        wifi_ie.parse(b"\x30\xff\x01")
        wifi_ie.parse(b"")
        wifi_ie.parse(None)


class StoreAndServerTests(unittest.TestCase):
    def setUp(self):
        self.store = DeviceStore(ENGINE, DB)
        evil = "<script>alert(1)</script>"
        self.store.update_wifi([
            {"bssid": "00:0f:b3:aa:bb:01", "ssid": "Actiontec-7C2F", "rssi": -60, "channel": 6,
             "ie": _ie(b"Actiontec-7C2F", 6, [bytes.fromhex("000FB3")])},
            {"bssid": "AA:BB:CC:00:11:22", "ssid": evil, "rssi": -80, "channel": 11, "ie": _ie(evil.encode(), 11, [])},
            {"bssid": "", "ssid": "no bssid (location denied)", "rssi": -50},
        ])
        self.store.update_ble("U-1", None, -55, {0x004C: bytes.fromhex("12190001")}, {}, [], None)
        # Name arrives later in a scan response; must merge, not replace.
        self.store.update_ble("U-1", "Thing", -57, {}, {}, [], None)

    def test_snapshot(self):
        snap = self.store.snapshot()
        self.assertEqual(len(snap["devices"]), 3)  # blank-BSSID network dropped
        by = {d["key"]: d for d in snap["devices"]}
        w = by["wifi:00:0F:B3:AA:BB:01"]
        self.assertEqual(w["vendor"][:9], "Actiontec")
        self.assertTrue(any(f["id"] == "fleet-actiontec" for f in w["fleets"]))
        b = by["ble:U-1"]
        self.assertEqual(b["name"], "Thing")
        self.assertEqual(b["mfg"][0]["id"], 0x004C)
        self.assertTrue(any(f["id"] == "fleet-airtag" for f in b["fleets"]))
        self.assertEqual(b["count"], 2)
        self.assertEqual(b["rssi_max"], -55)

    def test_csv_export(self):
        csv_text = self.store.export_csv()
        self.assertTrue(csv_text.startswith("radio,name,bssid"))
        self.assertIn("Actiontec-7C2F", csv_text)

    def test_server_host_and_origin_checks(self):
        srv = make_server(self.store, 0)
        port = srv.server_address[1]
        serve_in_thread(srv)
        try:
            def req(method, path, headers=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request(method, path, headers=headers or {})
                r = c.getresponse()
                return r.status, r.read()

            status, body = req("GET", "/api/snapshot")
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(body)["devices"]), 3)
            self.assertEqual(req("GET", "/api/snapshot", {"Host": "evil.example"})[0], 403)
            self.assertEqual(req("POST", "/api/clear", {"Origin": "http://evil.example"})[0], 403)
            self.assertEqual(req("GET", "/")[0], 200)
            self.assertEqual(req("GET", "/api/export.csv")[0], 200)
            self.assertEqual(req("POST", "/api/clear", {"Origin": f"http://127.0.0.1:{port}"})[0], 200)
            self.assertEqual(len(json.loads(req("GET", "/api/snapshot")[1])["devices"]), 0)
        finally:
            srv.shutdown(); srv.server_close()


class EmbeddedWifiTests(unittest.TestCase):
    """Fieldwatch.app writes ~/.fieldwatch/wifi.json itself; WifiSource only reads it."""

    def _run_source(self, payload):
        store = DeviceStore(ENGINE, DB)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wifi.json"
            path.write_text(json.dumps(payload))
            os.environ["FIELDWATCH_WIFI_EMBEDDED"] = "1"
            try:
                src = WifiSource(store, ROOT, interval=1.0, json_path=path)
            finally:
                del os.environ["FIELDWATCH_WIFI_EMBEDDED"]
            self.assertTrue(src.embedded)
            src.start()
            time.sleep(1.8)
            src.stop()
        return store

    def test_reads_networks_without_launching_anything(self):
        net = {"bssid": "00:0F:B3:AA:BB:01", "ssid": "Actiontec-7C2F", "rssi": -60, "channel": 6}
        store = self._run_source({"updated": time.time(), "status": "ok", "detail": "", "networks": [net]})
        self.assertEqual(store.wifi_status["state"], "scanning")
        self.assertEqual(len(store.snapshot()["devices"]), 1)

    def test_needs_location_is_reported_to_the_ui(self):
        store = self._run_source({"updated": time.time(), "status": "needs_location", "detail": "Allow Location", "networks": []})
        self.assertEqual(store.wifi_status["state"], "needs_location")
        self.assertIn("Allow Location", store.wifi_status["detail"])

    def test_stale_file_is_an_error(self):
        store = self._run_source({"updated": time.time() - 3600, "status": "ok", "networks": []})
        self.assertEqual(store.wifi_status["state"], "error")


class ParentWatchdogTests(unittest.TestCase):
    def test_backend_exits_when_parent_is_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = dict(os.environ, FIELDWATCH_PARENT_PID=str(dead.pid), PYTHONPATH=str(ROOT))
        p = subprocess.Popen([sys.executable, "-m", "fieldwatch_mac", "--demo", "--no-open", "--port", str(port)],
                             cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            self.fail("backend kept running after its parent died")
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
