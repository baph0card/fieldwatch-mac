"""BLE scanning via bleak (CoreBluetooth). Listen-only: it never connects to anything."""

from __future__ import annotations

import asyncio
import threading

from .models import BLE
from .store import DeviceStore


class BleScanner(threading.Thread):
    def __init__(self, store: DeviceStore):
        super().__init__(name="ble-scanner", daemon=True)
        self.store = store
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:  # pragma: no cover - platform specific
            self.store.set_status(BLE, "error", f"{type(e).__name__}: {e}")

    async def _main(self) -> None:
        try:
            from bleak import BleakScanner
        except ImportError:
            self.store.set_status(BLE, "error", "bleak is not installed (run ./setup.sh)")
            return

        def on_adv(device, adv) -> None:
            self.store.update_ble(
                address=device.address,
                name=adv.local_name or device.name,
                rssi=adv.rssi,
                mfg=dict(adv.manufacturer_data or {}),
                svc_data=dict(adv.service_data or {}),
                svc_uuids=list(adv.service_uuids or []),
                tx_power=adv.tx_power,
            )

        backoff = 2.0
        while not self._halt.is_set():
            try:
                async with BleakScanner(detection_callback=on_adv):
                    self.store.set_status(BLE, "scanning", "")
                    backoff = 2.0
                    ticks = 0
                    while not self._halt.is_set():
                        await asyncio.sleep(1.0)
                        ticks += 1
                        self.store.set_status(BLE, "scanning", "")
                        if ticks % 10 == 0:
                            self.store.log_ble_tick()
            except Exception as e:  # permission denied, adapter off, etc.
                msg = str(e) or type(e).__name__
                hint = ""
                if "denied" in msg.lower() or "authoriz" in msg.lower() or "not allowed" in msg.lower():
                    hint = " Grant Bluetooth access to your terminal app in System Settings > Privacy & Security > Bluetooth."
                self.store.set_status(BLE, "error", msg + hint)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
