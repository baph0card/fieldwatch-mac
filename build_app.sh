#!/usr/bin/env bash
# Builds build/Fieldwatch.app: a double-clickable Mac app with its own window.
# Re-run it after updating the project. Needs macOS 12+, Python 3.9+ and Xcode command line tools.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "build_app.sh builds a macOS app and must run on a Mac." >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Install Xcode command line tools:  xcode-select --install" >&2
  exit 1
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

SUPPORT="$HOME/Library/Application Support/Fieldwatch"
APP="build/Fieldwatch.app"

echo "==> Python environment (outside the app, so the app can be moved): $SUPPORT/venv"
mkdir -p "$SUPPORT"
"$PY" -m venv "$SUPPORT/venv"
"$SUPPORT/venv/bin/pip" install --quiet --upgrade pip
"$SUPPORT/venv/bin/pip" install --quiet -r requirements.txt

echo "==> Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R fieldwatch_mac "$APP/Contents/Resources/fieldwatch_mac"
cp -R data "$APP/Contents/Resources/data"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +

echo "==> Icon"
ICON_KEY=""
TMP="$(mktemp -d)"
if "$PY" tools/make_icon.py "$TMP/icon.png" && mkdir "$TMP/Fieldwatch.iconset"; then
  for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon.png" --out "$TMP/Fieldwatch.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$TMP/icon.png" --out "$TMP/Fieldwatch.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  if iconutil -c icns "$TMP/Fieldwatch.iconset" -o "$APP/Contents/Resources/Fieldwatch.icns"; then
    ICON_KEY="<key>CFBundleIconFile</key><string>Fieldwatch</string>"
  fi
fi
rm -rf "$TMP"
[[ -n "$ICON_KEY" ]] || echo "    (icon step failed; continuing without an icon)"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.fieldwatch.app</string>
  <key>CFBundleName</key><string>Fieldwatch</string>
  <key>CFBundleDisplayName</key><string>Fieldwatch</string>
  <key>CFBundleExecutable</key><string>Fieldwatch</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  $ICON_KEY
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>NSLocationUsageDescription</key>
  <string>macOS only reveals nearby Wi-Fi network names and addresses to apps with Location access. Fieldwatch does not read or store your location.</string>
  <key>NSLocationWhenInUseUsageDescription</key>
  <string>macOS only reveals nearby Wi-Fi network names and addresses to apps with Location access. Fieldwatch does not read or store your location.</string>
  <key>NSBluetoothAlwaysUsageDescription</key>
  <string>Fieldwatch listens for nearby Bluetooth LE advertisements. It never connects to or pairs with devices.</string>
  <key>NSBluetoothPeripheralUsageDescription</key>
  <string>Fieldwatch listens for nearby Bluetooth LE advertisements. It never connects to or pairs with devices.</string>
</dict>
</plist>
PLIST

echo "==> Compiling the app shell"
# Pin the minimum macOS explicitly. Without this the toolchain can stamp the app with the SDK's version,
# which may be newer than the macOS you are running ("can't use this version of the application").
MIN_MACOS=12.0
ARCH=arm64
[[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]] || ARCH=x86_64
swiftc -O -swift-version 5 -target "${ARCH}-apple-macos${MIN_MACOS}" \
  helper/WifiScanner.swift helper/app/main.swift \
  -o "$APP/Contents/MacOS/Fieldwatch" \
  -framework AppKit -framework WebKit -framework CoreLocation -framework CoreWLAN

echo "    macOS here: $(sw_vers -productVersion); app built for ${ARCH}, runs on macOS ${MIN_MACOS}+"
if command -v vtool >/dev/null 2>&1; then
  vtool -show-build "$APP/Contents/MacOS/Fieldwatch" 2>/dev/null | grep -E "minos|sdk" | sed 's/^/    /' || true
fi

codesign --force --deep --sign - "$APP"

echo
echo "Built $APP"
echo "Open it:            open $APP"
echo "Install it:         cp -R $APP /Applications/"
echo "First launch asks for Location (Wi-Fi) and Bluetooth access. Allow both."
echo "Log if it misbehaves: ~/Library/Logs/Fieldwatch.log"
