// FieldwatchWiFi: background-only variant used by the command-line version (./fieldwatch).
// The double-clickable Fieldwatch.app scans Wi-Fi itself and does not need this.
//
// Usage: FieldwatchWiFi --out /path/to/wifi.json --interval 6

import AppKit
import Foundation

final class CliDelegate: NSObject, NSApplicationDelegate {
    let scanner: WifiScanner

    init(out: String, interval: TimeInterval) {
        scanner = WifiScanner(out: URL(fileURLWithPath: out), interval: interval)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        scanner.start()
    }
}

var outPath = NSString(string: "~/.fieldwatch/wifi.json").expandingTildeInPath
var interval: TimeInterval = 6
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    if a == "--out", !args.isEmpty {
        outPath = args.removeFirst()
    } else if a == "--interval", !args.isEmpty {
        interval = TimeInterval(args.removeFirst()) ?? 6
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = CliDelegate(out: outPath, interval: interval)
app.delegate = delegate
app.run()
