// Fieldwatch.app: a native window around the local Fieldwatch web UI.
//
// What it does:
//   1. Scans Wi-Fi itself (WifiScanner) so Location permission belongs to this app.
//   2. Starts the Python backend (python -m fieldwatch_mac) as a child process on a free local port.
//   3. Shows the backend's page in a WKWebView and stops the backend when the window closes.
//
// The backend lives in a virtualenv at ~/Library/Application Support/Fieldwatch/venv, created by build_app.sh.

import AppKit
import Darwin
import Foundation
import WebKit

// MARK: - Helpers

func findFreePort() -> Int {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return 8765 }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let bound = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    if bound != 0 { return 8765 }
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    let named = withUnsafeMutablePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            getsockname(fd, $0, &len)
        }
    }
    if named != 0 { return 8765 }
    return Int(UInt16(bigEndian: addr.sin_port))
}

func htmlEscape(_ s: String) -> String {
    s.replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

func statusPage(title: String, body: String, isError: Bool = false) -> String {
    let accent = isError ? "#ff3d5a" : "#3dff9a"
    return """
    <!doctype html><meta charset="utf-8">
    <style>
      body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
           background:#0f1316;color:#e6edf1;font:15px -apple-system,system-ui,sans-serif}
      main{max-width:640px;padding:24px}
      h1{font-size:18px;letter-spacing:.04em;color:\(accent);margin:0 0 12px}
      pre{white-space:pre-wrap;background:#171d21;border:1px solid #252d33;border-radius:8px;
          padding:12px;font-size:12px;color:#8a99a3;max-height:40vh;overflow:auto}
      p{line-height:1.5;color:#c3ced6}
    </style>
    <main><h1>\(htmlEscape(title))</h1>\(body)</main>
    """
}

// MARK: - Python backend

enum AppError: Error {
    case message(String)
}

final class BackendRunner {
    let port: Int
    let logURL: URL
    var onExit: ((Int32) -> Void)?
    private var process: Process?

    init(port: Int) {
        self.port = port
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
        self.logURL = logs.appendingPathComponent("Fieldwatch.log")
    }

    func start(resources: URL) throws {
        let support = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Fieldwatch")
        let python = support.appendingPathComponent("venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw AppError.message(
                "The Python environment is missing at \(python.path). Re-run ./build_app.sh from the project folder.")
        }
        try FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)  // truncates
        let log = try FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = python
        p.arguments = ["-m", "fieldwatch_mac", "--no-open", "--port", String(port)]
        p.currentDirectoryURL = resources
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = resources.path
        env["PYTHONUNBUFFERED"] = "1"
        env["FIELDWATCH_WIFI_EMBEDDED"] = "1"
        env["FIELDWATCH_PARENT_PID"] = String(getpid())
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.onExit?(proc.terminationStatus) }
        }
        try p.run()
        process = p
    }

    func stop() {
        guard let p = process, p.isRunning else { return }
        p.interrupt()  // SIGINT: the backend shuts down cleanly on it
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
            if p.isRunning { p.terminate() }
        }
    }

    func logTail(_ bytes: Int = 3000) -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "" }
        let tail = data.suffix(bytes)
        return String(decoding: tail, as: UTF8.self)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var scanner: WifiScanner?
    private let port = findFreePort()
    private lazy var backend = BackendRunner(port: port)
    private var pollTimer: Timer?
    private var startedAt = Date()
    private var uiLoaded = false
    private var quitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A leftover standalone helper from the command-line version would fight over the same JSON file.
        let pk = Process()
        pk.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        pk.arguments = ["-x", "FieldwatchWiFi"]
        try? pk.run()

        buildMenu()
        buildWindow()
        showStatus(title: "Starting Fieldwatch…", body: "<p>Starting the scanners.</p>")
        NSApp.activate(ignoringOtherApps: true)

        let wifiJSON = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".fieldwatch/wifi.json")
        try? FileManager.default.removeItem(at: wifiJSON)  // never show a previous session's data as current
        let s = WifiScanner(out: wifiJSON, interval: 6)
        s.start()
        scanner = s

        backend.onExit = { [weak self] code in
            guard let self = self, !self.quitting else { return }
            self.showBackendFailure("The Fieldwatch backend stopped (exit code \(code)).")
        }
        do {
            try backend.start(resources: Bundle.main.resourceURL!)
        } catch AppError.message(let m) {
            showStatus(title: "Fieldwatch can't start", body: "<p>\(htmlEscape(m))</p>", isError: true)
            return
        } catch {
            showStatus(title: "Fieldwatch can't start", body: "<p>\(htmlEscape(error.localizedDescription))</p>", isError: true)
            return
        }

        startedAt = Date()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in
            self?.pollBackend()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        pollTimer?.invalidate()
        scanner?.stop()
        backend.stop()
    }

    // MARK: backend readiness

    private func pollBackend() {
        if uiLoaded { return }
        if Date().timeIntervalSince(startedAt) > 45 {
            pollTimer?.invalidate()
            showBackendFailure("The backend did not start within 45 seconds.")
            return
        }
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/snapshot") else { return }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { [weak self] _, resp, _ in
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return }
            DispatchQueue.main.async { self?.loadUI() }
        }.resume()
    }

    private func loadUI() {
        if uiLoaded { return }
        uiLoaded = true
        pollTimer?.invalidate()
        if let url = URL(string: "http://127.0.0.1:\(port)/") {
            webView.load(URLRequest(url: url))
        }
    }

    private func showBackendFailure(_ headline: String) {
        pollTimer?.invalidate()
        let tail = backend.logTail()
        let body = "<p>\(htmlEscape(headline))</p><pre>\(htmlEscape(tail.isEmpty ? "(no log output)" : tail))</pre>"
            + "<p>Full log: ~/Library/Logs/Fieldwatch.log</p>"
        showStatus(title: "Fieldwatch hit a problem", body: body, isError: true)
    }

    private func showStatus(title: String, body: String, isError: Bool = false) {
        uiLoaded = false
        webView.loadHTMLString(statusPage(title: title, body: body, isError: isError), baseURL: nil)
    }

    // MARK: window and menu

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false)
        window.title = "Fieldwatch"
        window.minSize = NSSize(width: 760, height: 480)
        window.contentView = webView
        _ = window.setFrameAutosaveName("FieldwatchMainWindow")
        if !window.setFrameUsingName("FieldwatchMainWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)
    }

    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About Fieldwatch", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Fieldwatch", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Fieldwatch", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        editItem.submenu = edit
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: "View")
        viewItem.submenu = view
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)

        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
    }

    @objc private func reloadPage() {
        if uiLoaded { webView.reload() }
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        if let host = url?.host, host != "127.0.0.1" {
            decisionHandler(.cancel)  // the UI never links off-box; refuse anything that tries
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let http = navigationResponse.response as? HTTPURLResponse,
           let disposition = http.value(forHTTPHeaderField: "Content-Disposition"),
           disposition.lowercased().contains("attachment") {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    // MARK: WKDownloadDelegate (CSV / JSON export)

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }

    // MARK: WKUIDelegate (the page uses confirm())

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
        completionHandler()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let appDelegate = AppDelegate()
app.delegate = appDelegate
app.run()
