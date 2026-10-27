from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .ble_scanner import BleScanner
from .models import BLE, WIFI
from .radiodb import RadioDb
from .server import make_server, serve_in_thread
from .signatures import SignatureEngine
from .store import DeviceStore
from .wifi_source import WifiSource

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fieldwatch", description="Receive-only Wi-Fi and Bluetooth LE observer for macOS.")
    ap.add_argument("--port", type=int, default=8765, help="local web UI port (default 8765)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--no-wifi", action="store_true", help="skip Wi-Fi scanning")
    ap.add_argument("--no-ble", action="store_true", help="skip Bluetooth LE scanning")
    ap.add_argument("--wifi-interval", type=float, default=6.0, help="seconds between Wi-Fi scans (default 6)")
    ap.add_argument("--catalog", type=Path, default=ROOT / "data" / "signatures.json",
                    help="signature file (Fieldwatch export format). An export from the Android app works.")
    ap.add_argument("--log", type=Path, help="append sightings to this JSONL file (written every ~10 s per radio)")
    ap.add_argument("--demo", action="store_true", help="feed synthetic devices instead of scanning (UI testing)")
    ap.add_argument("--version", action="version", version=f"fieldwatch-mac {__version__}")
    args = ap.parse_args(argv)

    engine = SignatureEngine.from_file(args.catalog)
    try:
        radiodb = RadioDb(ROOT / "data" / "radiodb.bin")
    except Exception as e:
        print(f"warning: vendor lookup unavailable ({e})", file=sys.stderr)
        radiodb = None
    store = DeviceStore(engine, radiodb, args.log)

    workers = []
    if args.demo:
        from .demo import DemoFeed
        workers.append(DemoFeed(store))
    else:
        if not args.no_wifi:
            if sys.platform == "darwin":
                workers.append(WifiSource(store, ROOT, args.wifi_interval))
            else:
                store.set_status(WIFI, "error", "Wi-Fi scanning needs macOS (CoreWLAN).")
        else:
            store.set_status(WIFI, "off", "disabled")
        if not args.no_ble:
            workers.append(BleScanner(store))
        else:
            store.set_status(BLE, "off", "disabled")
    for w in workers:
        w.start()

    srv = make_server(store, args.port)
    serve_in_thread(srv)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Fieldwatch {__version__}: {len(engine.fleets)} signatures loaded. UI at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)

    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())

    # Started by Fieldwatch.app: exit if the app disappears (force-quit, crash) so the port is not left held.
    parent = os.environ.get("FIELDWATCH_PARENT_PID")
    if parent and parent.isdigit():
        def watch_parent(pid: int = int(parent)) -> None:
            while not done.is_set():
                time.sleep(2)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    done.set()
                except PermissionError:
                    pass
        threading.Thread(target=watch_parent, name="parent-watch", daemon=True).start()
    done.wait()

    print("\nStopping...")
    for w in workers:
        if hasattr(w, "stop"):
            w.stop()
    srv.shutdown()
    if sys.platform == "darwin" and not args.demo and not args.no_wifi and os.environ.get("FIELDWATCH_WIFI_EMBEDDED") != "1":
        subprocess.run(["pkill", "-x", "FieldwatchWiFi"], check=False)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
