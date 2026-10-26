// SPDX-License-Identifier: Apache-2.0
// luma-web-signin: a small sign-in window for Luma apps on macOS (luma port).
//
// An app that needs a website's sign-in cookies (Messages: Google Messages for web) runs this
// program from Contents/Helpers. It shows one window with the site's own page in a WKWebView,
// and as soon as every named cookie is present for the named domains it writes them to stdout
// as one JSON object and exits 0. Nothing is written anywhere else: the web view uses a
// non-persistent data store, so the session lives only in this process and is gone when it
// exits, and nothing here logs a cookie.
//
//   luma-web-signin --url URL --cookie NAME... [--optional NAME...] --domain DOMAIN...
//                   [--title TITLE] [--icon PATH] [--timeout SECONDS]
//
// Exit codes: 0 cookies written, 1 the window was closed, 2 bad arguments, 3 timed out,
// 4 the app went away (stdin closed). The app cancels by closing our stdin or sending SIGTERM.
import AppKit
import WebKit

struct Options {
    var url: URL?
    var cookies: [String] = []
    var optional: [String] = []
    var domains: [String] = []
    var title = "Sign In"
    var icon: String?
    var timeout: TimeInterval = 900
}

func parse() -> Options? {
    var options = Options()
    var args = CommandLine.arguments.dropFirst().makeIterator()
    while let flag = args.next() {
        guard let value = args.next() else { return nil }
        switch flag {
        case "--url": options.url = URL(string: value)
        case "--cookie": options.cookies.append(value)
        case "--optional": options.optional.append(value)
        case "--domain": options.domains.append(value.hasPrefix(".") ? String(value.dropFirst()) : value)
        case "--title": options.title = value
        case "--icon": options.icon = value
        case "--timeout": options.timeout = TimeInterval(value) ?? options.timeout
        default: return nil
        }
    }
    guard let url = options.url, url.scheme == "https", !options.cookies.isEmpty, !options.domains.isEmpty else {
        return nil
    }
    return options
}

/// Safari's own version, so Google sees the browser this web view is (WebKit) rather than an
/// unnamed embedded view.
func safariSuffix() -> String {
    let plist = URL(fileURLWithPath: "/Applications/Safari.app/Contents/Info.plist")
    var version = "26.0"
    if let info = NSDictionary(contentsOf: plist), let short = info["CFBundleShortVersionString"] as? String {
        version = short
    }
    return "Version/\(version) Safari/605.1.15"
}

final class SignIn: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let options: Options
    var window: NSWindow!
    var web: WKWebView!
    var timer: Timer?
    var finished = false

    init(options: Options) { self.options = options }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let path = options.icon, let image = NSImage(contentsOfFile: path) { NSApp.applicationIconImage = image }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.applicationNameForUserAgent = safariSuffix()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 480, height: 640), configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 640),
                          styleMask: [.titled, .closable, .resizable, .miniaturizable],
                          backing: .buffered, defer: false)
        window.title = options.title
        window.contentView = web
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        setupMenu()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.load(URLRequest(url: options.url!))
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in self?.poll() }
        DispatchQueue.main.asyncAfter(deadline: .now() + options.timeout) { [weak self] in self?.finish(code: 3) }
        // The app went away (or cancelled): stdin reaches end of file.
        DispatchQueue.global().async { [weak self] in
            while let line = readLine(strippingNewline: true) { if line == "cancel" { break } }
            DispatchQueue.main.async { self?.finish(code: 4) }
        }
    }

    func setupMenu() {
        // Edit shortcuts (copy, paste, select all) reach the page only through a menu.
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }

    func matches(_ cookie: HTTPCookie) -> Bool {
        let host = cookie.domain.hasPrefix(".") ? String(cookie.domain.dropFirst()) : cookie.domain
        return options.domains.contains { host == $0 || host.hasSuffix("." + $0) }
    }

    func poll() {
        guard !finished else { return }
        web.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] all in
            guard let self = self, !self.finished else { return }
            var found: [String: String] = [:]
            let wanted = Set(self.options.cookies + self.options.optional)
            // Prefer the cookie set for the shortest (widest) domain, as a browser's jar would send it.
            for cookie in all.sorted(by: { $0.domain.count > $1.domain.count })
            where wanted.contains(cookie.name) && self.matches(cookie) {
                found[cookie.name] = cookie.value
            }
            if self.options.cookies.allSatisfy({ found[$0] != nil }) {
                if let data = try? JSONSerialization.data(withJSONObject: found, options: [.sortedKeys]) {
                    FileHandle.standardOutput.write(data)
                    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
                }
                self.finish(code: 0)
            }
        }
    }

    func finish(code: Int32) {
        guard !finished else { return }
        finished = true
        timer?.invalidate()
        web?.stopLoading()
        exit(code)
    }

    // Pages that open a new window (account chooser links) stay in this one.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { poll() }

    func windowWillClose(_ notification: Notification) { finish(code: 1) }

    // Quit from the Dock or with Command-Q is the person closing it too, not a finished sign-in.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        finish(code: 1)
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

guard let options = parse() else {
    FileHandle.standardError.write("usage: luma-web-signin --url https://… --cookie NAME… --domain DOMAIN…\n".data(using: .utf8)!)
    exit(2)
}
signal(SIGPIPE, SIG_IGN)
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = SignIn(options: options)
app.delegate = delegate
app.run()
