// WifiScanner: scans Wi-Fi with CoreWLAN on a timer and writes one JSON file for the Python app.
//
// macOS 14+ hides SSIDs and BSSIDs from any process that lacks Location Services permission, and
// only a real app bundle can hold that permission. It never joins a network and never sends data
// anywhere; it writes one local JSON file.

import AppKit
import CoreLocation
import CoreWLAN
import Foundation

final class WifiScanner: NSObject, CLLocationManagerDelegate {
    private let locationManager = CLLocationManager()
    private let queue = DispatchQueue(label: "fieldwatch.wifi.scan")
    private var timer: DispatchSourceTimer?
    private let outURL: URL
    private let interval: TimeInterval
    private var authorization: CLAuthorizationStatus = .notDetermined

    init(out: URL, interval: TimeInterval) {
        self.outURL = out
        self.interval = max(3, interval)
        super.init()
    }

    /// Call on the main thread after NSApplication has started.
    func start() {
        locationManager.delegate = self
        authorization = locationManager.authorizationStatus
        if authorization == .notDetermined {
            // Bring the permission prompt in front of other windows.
            NSApp.activate(ignoringOtherApps: true)
            locationManager.requestWhenInUseAuthorization()
        }
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 0.5, repeating: interval)
        t.setEventHandler { [weak self] in self?.scanOnce() }
        t.resume()
        timer = t
    }

    func stop() {
        timer?.cancel()
        timer = nil
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorization = manager.authorizationStatus
    }

    private func write(status: String, detail: String = "", networks: [[String: Any]] = []) {
        let payload: [String: Any] = [
            "updated": Date().timeIntervalSince1970,
            "status": status,
            "detail": detail,
            "networks": networks,
        ]
        do {
            try FileManager.default.createDirectory(
                at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: payload)
            try data.write(to: outURL, options: .atomic)
        } catch {
            // Nothing sensible to do; the Python side reports a stale file.
        }
    }

    private func scanOnce() {
        switch authorization {
        case .denied, .restricted:
            write(
                status: "needs_location",
                detail: "Location Services is off for Fieldwatch. Enable it in System Settings > Privacy & Security > Location Services.")
            return
        case .notDetermined:
            write(status: "needs_location", detail: "Waiting for you to answer the Location Services prompt.")
            return
        default:
            break
        }

        guard let iface = CWWiFiClient.shared().interface() else {
            write(status: "error", detail: "No Wi-Fi interface found.")
            return
        }
        if !iface.powerOn() {
            write(status: "error", detail: "Wi-Fi is turned off.")
            return
        }

        do {
            let found = try iface.scanForNetworks(withName: nil)
            var out: [[String: Any]] = []
            var withBssid = 0
            for n in found {
                var d: [String: Any] = [
                    "bssid": n.bssid ?? "",
                    "ssid": n.ssid ?? "",
                    "rssi": n.rssiValue,
                    "noise": n.noiseMeasurement,
                    "country": n.countryCode ?? "",
                ]
                if n.bssid != nil { withBssid += 1 }
                if let ch = n.wlanChannel {
                    d["channel"] = ch.channelNumber
                    // Raw values (1 = 2.4 GHz, 2 = 5 GHz, 3 = 6 GHz) so this compiles for older deployment targets.
                    switch ch.channelBand.rawValue {
                    case 1: d["band"] = "2.4 GHz"
                    case 2: d["band"] = "5 GHz"
                    case 3: d["band"] = "6 GHz"
                    default: break
                    }
                }
                if let ie = n.informationElementData {
                    d["ie"] = ie.base64EncodedString()
                }
                out.append(d)
            }
            if !found.isEmpty && withBssid == 0 {
                write(
                    status: "needs_location",
                    detail: "macOS is hiding network names and addresses. Allow Location Services for Fieldwatch, then restart the app.")
            } else {
                write(status: "ok", networks: out)
            }
        } catch {
            write(status: "error", detail: "Scan failed: \(error.localizedDescription)")
        }
    }
}
