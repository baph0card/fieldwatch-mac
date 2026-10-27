# Fieldwatch for Mac

A receive-only Wi-Fi and Bluetooth LE observer for macOS, ported from
[OffGridPete/Fieldwatch](https://github.com/OffGridPete/Fieldwatch) (Android, MIT).
It lists the access points and BLE advertisers around you, labels them against the upstream
signature catalog (254 signatures: finder tags, cameras, drones, vehicles, ISP gear, and so on),
and shows everything in a local web page. Nothing leaves your Mac: no account, no cloud service.

> **Status: untested on a real Mac.** The matching engine, vendor lookups, web UI, local server,
> embedded Wi-Fi mode and shutdown watchdog were built and tested (22 unit tests plus a headless UI
> check). The Swift app shell, the Wi-Fi scanner and the CoreBluetooth scanner could not be compiled
> or run in the environment this was written in. Expect to debug the first build or launch; see
> Troubleshooting.

## Mac app (double-click, own window)

Needs macOS 12+, Python 3.9+ and Xcode command line tools (`xcode-select --install`). The scripts search for a suitable Python themselves; if none is found, `brew install python` or use the python.org installer, then re-run. Use `PYTHON=/path/to/python3 bash build_app.sh` to pick one explicitly. If `./build_app.sh` says "permission denied", run `bash build_app.sh`.

```bash
./build_app.sh                 # builds build/Fieldwatch.app (about a minute)
open build/Fieldwatch.app      # or drag it to /Applications and double-click
```

The app is a native window around the same UI. It scans Wi-Fi itself, runs the Python backend as a child
process, and stops everything when you close the window or press Cmd+Q. The Python environment lives in
`~/Library/Application Support/Fieldwatch/venv`, so you can move the app anywhere. Re-run `./build_app.sh`
after changing the project.

Two permission prompts on first launch, once each, both under the name **Fieldwatch**:

1. **Location Services** (for Wi-Fi). Since macOS 14, Wi-Fi scans return blank names and addresses unless an
   app holds Location permission. The app only uses it to unlock scan results; it never reads your location.
2. **Bluetooth** (on the first BLE scan).

Export CSV / JSON opens a save dialog. The backend's log is `~/Library/Logs/Fieldwatch.log`.

## Command-line mode (browser instead of a window)

```bash
./setup.sh      # venv + bleak, builds a small background Wi-Fi helper
./fieldwatch    # starts the scanners and opens http://127.0.0.1:8765
```

Run it from **Terminal.app or iTerm**: macOS ties Bluetooth permission to the terminal app, and some other
terminals (editor-embedded ones in particular) may be killed by macOS on first use. The app avoids that
because the permission belongs to Fieldwatch itself.

Options: `--port N`, `--no-wifi`, `--no-ble`, `--wifi-interval SECONDS`, `--log sightings.jsonl`,
`--catalog file.json`, `--demo` (fake devices, works on any OS).

`--catalog` accepts the signature export format, so a catalog you edited in the Android app
(Settings → Export signatures) loads here. The app always uses the bundled catalog; to change it, replace
`data/signatures.json` and re-run `./build_app.sh`.

## What is ported, and what is not

| Ported | Not ported |
|---|---|
| Signature engine: OUI, vendor-IE OUI, name contains/glob, service UUID, manufacturer ID/data, service data, hidden SSID, all-rules (AND) fleets, cluster fleets, overlap rules (AirTag vs Apple device, Polar vs GoPro, Meraki vs Cisco, iBeacon) | GPS stamps, "moving with you" co-travel, maps |
| Vendor names from IEEE OUI / MA-M / MA-S / CID and Bluetooth SIG company + service names | Sit/diff views, Debrief and PDF reports, AI Export |
| Wi-Fi detail from raw IEs: security (WPA2/WPA3/OWE/Enterprise), Wi-Fi 4–7, country, vendor IEs | Payload decoders (Open Drone ID, CTA-2063, field decoders), GNSS interference, TAK publishing |
| Live table, filters, sort, per-device detail, CSV/JSON export, optional JSONL log | Alerts, hunt mode, bookmarks, filter presets, in-app signature editor |

## Differences you will notice

- **No BLE addresses.** CoreBluetooth hands apps a per-Mac UUID, never the real address. BLE rules based on
  OUI or MAC prefix cannot match; name, service UUID, manufacturer and service-data rules do. Randomized
  addresses make BLE devices flicker anyway, so treat BLE counts as approximate.
- **Wi-Fi is polled.** CoreWLAN does a scan roughly every 6 s (`--wifi-interval`). That is a normal
  active scan, the same one the Wi-Fi menu performs, so it sends standard probe requests. It never joins
  a network or transmits data.
- **Matches are hypotheses**, as upstream says: radios that are off, asleep or randomized will not appear,
  and a name or OUI match is not an identification.

## Privacy and safety

- The web server binds to `127.0.0.1` only and rejects foreign `Host`/`Origin` headers.
- SSIDs and device names are chosen by whoever owns the radio; the UI renders them as plain text only.
- `--log` and the exports contain BSSIDs and device names of everything near you. Treat those files accordingly,
  and follow local law on how you collect and share them.

## Troubleshooting

- **App shows "Fieldwatch can't start: Python environment is missing".** Run `./build_app.sh` again.
- **"You can't use this version of the application with this version of macOS".** The build now pins the minimum to
  macOS 12 and prints the version it stamped. If you still see it, run `sw_vers -productVersion` and
  `vtool -show-build build/Fieldwatch.app/Contents/MacOS/Fieldwatch` and send me both, plus the exact dialog text.
- **App shows "Fieldwatch hit a problem".** The log tail is on screen; the full log is `~/Library/Logs/Fieldwatch.log`.
- **`build_app.sh` fails at "Compiling the app shell".** Send me the compiler error. It is the least-tested part.
- **Wi-Fi: "needs_location" or all names blank.** System Settings → Privacy & Security → Location Services →
  enable Fieldwatch (or FieldwatchWiFi in command-line mode), then quit and reopen.
- **No Location prompt appeared.** Quit the app, then `open build/Fieldwatch.app` from Terminal and look for the prompt.
- **Bluetooth error mentioning authorization.** System Settings → Privacy & Security → Bluetooth → enable Fieldwatch
  (app) or your terminal app (command-line mode).
- **Nothing at all.** `./fieldwatch --demo` confirms the UI works; then try `--no-wifi` / `--no-ble` one at a time.
- Command-line mode only: stop the helper with `pkill -x FieldwatchWiFi`. Its output file is `~/.fieldwatch/wifi.json`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Licence and attribution

This is an unofficial port of [OffGridPete/Fieldwatch](https://github.com/OffGridPete/Fieldwatch) by Off Grid Pete LLC,
which is MIT-licensed. Parts of this repository are derived from it and **remain under that MIT licence**,
Copyright (c) 2026 Off Grid Pete LLC. Keep the notice in `data/LICENSE-Fieldwatch` when you redistribute them:

- `data/signatures.json`, the signature catalog (catalog 95), and `data/radiodb.bin`
- the matching logic and overlap rules in `fieldwatch_mac/signatures.py`, ported from the Kotlin `SignatureEngine`

The rest (Wi-Fi/BLE scanning, the Swift app shell, the web UI, build scripts) is original to this repository and
covered by the repository's `LICENSE`. IEEE and Bluetooth SIG tables inside `radiodb.bin` are subject to those
organizations' terms; see `data/NOTICE-Fieldwatch`. Not made or reviewed by Off Grid Pete LLC.
