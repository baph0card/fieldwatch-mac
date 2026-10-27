#!/usr/bin/env bash
# One-time setup: Python venv + bleak, and build the small Wi-Fi helper app.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This setup targets macOS. (The Python app can run elsewhere with --demo.)" >&2
fi

if ! PY="$(bash tools/find_python.sh)"; then
  echo "No Python 3.9 or newer found." >&2
  if command -v python3 >/dev/null 2>&1; then echo "  python3 here is: $(python3 --version 2>&1)" >&2; else echo "  python3 is not installed." >&2; fi
  echo "Install one, then re-run this script:" >&2
  echo "  brew install python        (Homebrew: https://brew.sh)" >&2
  echo "  or the macOS installer:    https://www.python.org/downloads/macos/" >&2
  echo "If it is installed somewhere unusual:  PYTHON=/path/to/python3 bash $(basename "$0")" >&2
  exit 1
fi
echo "==> Using $PY ($("$PY" --version 2>&1))"

echo "==> Creating virtualenv and installing bleak"
"$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [[ "$(uname)" == "Darwin" ]]; then
  if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not found. Install Xcode command line tools:  xcode-select --install" >&2
    echo "Then re-run ./setup.sh. (BLE will work without it; Wi-Fi needs the helper.)" >&2
    exit 1
  fi

  echo "==> Building FieldwatchWiFi.app"
  APP="build/FieldwatchWiFi.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.fieldwatch.wifi</string>
  <key>CFBundleName</key><string>FieldwatchWiFi</string>
  <key>CFBundleDisplayName</key><string>FieldwatchWiFi</string>
  <key>CFBundleExecutable</key><string>FieldwatchWiFi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSLocationUsageDescription</key>
  <string>macOS only reveals nearby Wi-Fi network names and addresses to apps with Location access. Fieldwatch does not read or store your location.</string>
  <key>NSLocationWhenInUseUsageDescription</key>
  <string>macOS only reveals nearby Wi-Fi network names and addresses to apps with Location access. Fieldwatch does not read or store your location.</string>
</dict>
</plist>
PLIST
  ARCH=arm64
  [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]] || ARCH=x86_64
  swiftc -O -swift-version 5 -target "${ARCH}-apple-macos12.0" helper/WifiScanner.swift helper/cli/main.swift \
    -o "$APP/Contents/MacOS/FieldwatchWiFi" \
    -framework AppKit -framework CoreLocation -framework CoreWLAN
  codesign --force --sign - "$APP"

  echo "==> Opening the helper once so macOS can ask for Location access"
  mkdir -p "$HOME/.fieldwatch"
  open -g -a "$PWD/$APP" --args --out "$HOME/.fieldwatch/wifi.json" --interval 6
  echo "    If a Location Services prompt appears, click Allow."
fi

echo
echo "Done. Start it with:  ./fieldwatch"
