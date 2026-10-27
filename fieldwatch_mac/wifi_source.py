"""Wi-Fi source: launches the FieldwatchWiFi helper app and reads the JSON snapshot it keeps writing.

Why a helper app: since macOS 14, CoreWLAN only returns SSIDs/BSSIDs to a process with Location Services
permission, and that permission can only be granted to a real app bundle, not to a Python script.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .models import WIFI
from .store import DeviceStore

STATE_DIR = Path.home() / ".fieldwatch"
WIFI_JSON = STATE_DIR / "wifi.json"
APP_NAME = "FieldwatchWiFi.app"


def find_helper(root: Path) -> Path | None:
    for p in (root / "build" / APP_NAME, STATE_DIR / APP_NAME):
        if p.exists():
            return p
    return None


class WifiSource(threading.Thread):
    def __init__(self, store: DeviceStore, root: Path, interval: float = 6.0, json_path: Path | None = None):
        super().__init__(name="wifi-source", daemon=True)
        self.store = store
        self.root = root
        self.interval = interval
        self.json_path = json_path or WIFI_JSON
        # Fieldwatch.app scans Wi-Fi itself and writes the same JSON file; then there is no helper to launch.
        self.embedded = os.environ.get("FIELDWATCH_WIFI_EMBEDDED") == "1"
        self._halt = threading.Event()
        self._last_stamp = 0.0

    def stop(self) -> None:
        self._halt.set()

    def _launch_helper(self, app: Path) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        # -g: don't steal focus. -n: new instance only if none runs. --args: passed to the helper.
        subprocess.run(["open", "-g", "-a", str(app), "--args", "--out", str(self.json_path), "--interval", str(self.interval)],
                       check=False, capture_output=True)

    def run(self) -> None:
        app = None
        if not self.embedded:
            app = find_helper(self.root)
            if app is None:
                self.store.set_status(WIFI, "error", "Wi-Fi helper not built. Run ./setup.sh (needs Xcode command line tools).")
                return
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        if app is not None:
            self._launch_helper(app)
        launched = time.time()
        while not self._halt.is_set():
            time.sleep(1.0)
            try:
                data = json.loads(self.json_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                if time.time() - launched > 20:
                    hint = "The Wi-Fi scanner in the app is not writing results." if self.embedded else \
                        "Helper is not writing results. Try: open -a build/FieldwatchWiFi.app"
                    self.store.set_status(WIFI, "error", hint)
                continue
            stamp = float(data.get("updated", 0))
            status = data.get("status", "unknown")
            detail = data.get("detail", "")
            if time.time() - stamp > self.interval * 4 + 10:
                # Stale file: helper died. Relaunch at most every 30 s.
                self.store.set_status(WIFI, "error", "Wi-Fi scanner stopped responding" + ("" if self.embedded else "; relaunching"))
                if app is not None and time.time() - launched > 30:
                    self._launch_helper(app)
                    launched = time.time()
                continue
            if status == "ok":
                self.store.set_status(WIFI, "scanning", f"{len(data.get('networks', []))} networks")
                if stamp != self._last_stamp:
                    self._last_stamp = stamp
                    self.store.update_wifi(data.get("networks", []), now=stamp)
            elif status == "needs_location":
                self.store.set_status(WIFI, "needs_location", detail or "Allow Location Services for FieldwatchWiFi.")
            else:
                self.store.set_status(WIFI, "error", detail or status)
