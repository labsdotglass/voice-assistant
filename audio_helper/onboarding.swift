// The first run's window: language, setup progress, the tour and learning your voice, before and while voice sets up.
//
//   onboarding <page.html> [--replay] [--enroll] [--languages <json>] [--slide N]
//
// Shows the page (orb-page/dist/onboarding.html) in a window of its own and stays open for the whole first
// run. It has no audio code, so nothing listens or speaks while it's open. --replay (/voice-tour) ends with
// Done; --enroll opens at learning your voice. The page gets window.FIRST_RUN = {replay, enroll, languages}.
//
// The page talks to it with messages on the "tour" handler: {action:"language", language}, {action:"hide"},
// {action:"request", value} (written to first-run-request for the daemon), {action:"done"}, {action:"later"}
// (only before a language is chosen). It prints one line per event on stdout, flushed: "language he",
// "hidden", "shown", "done", "later", "closed". The daemon's state file (first-run.json) is polled every
// 0.2 s and passed to window.firstRun(state); a "ready" or "failed" phase brings a hidden window back.
import AppKit
import WebKit

let args = CommandLine.arguments
guard args.count >= 2, let html = try? String(contentsOfFile: args[1], encoding: .utf8) else {
    FileHandle.standardError.write("usage: onboarding <page.html> [--replay] [--enroll] [--languages <json>] [--slide N]\n".data(using: .utf8)!)
    exit(2)
}
let isReplay = args.contains("--replay")
let isEnroll = args.contains("--enroll")
let languagesJSON = args.firstIndex(of: "--languages").flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil } ?? "[]"

// While it's open, voice can step through it: ~/.voice-assistant/tour-open says so (holding this process's
// pid), a command written to tour-command ("next", "back", "first", "last", a slide number, "done") is
// passed to the page, and what it did is written to tour-result. "show" brings a hidden window back where
// it was (the footer's setup progress, pressed) and prints "shown".
let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".voice-assistant")
let tourOpen = home.appendingPathComponent("tour-open")
let tourCommand = home.appendingPathComponent("tour-command")
let tourResult = home.appendingPathComponent("tour-result")
let firstRunState = home.appendingPathComponent("first-run.json")
let firstRunRequest = home.appendingPathComponent("first-run-request")

var languageChosen = false
var hidden = false
var lastState = ""

func say(_ line: String) {
    print(line)
    fflush(stdout)
}

func finish(_ line: String) -> Never {
    try? FileManager.default.removeItem(at: tourOpen)
    say(line)
    exit(0)
}

func hideWindow() {
    guard !hidden else { return }
    hidden = true
    window.orderOut(nil)
    say("hidden")
}

@MainActor func showWindow() {
    guard hidden else { return }
    hidden = false
    window.makeKeyAndOrderFront(nil)
    window.makeFirstResponder(web)
    app.activate(ignoringOtherApps: true)
    say("shown")
}

// Done: fade the window out, then exit.
func fadeOutAndFinish() {
    NSAnimationContext.runAnimationGroup({ ctx in
        ctx.duration = 0.3
        window.animator().alphaValue = 0
    }, completionHandler: { finish("done") })
}

// The daemon's request, written whole and renamed in so it never reads half of one.
func writeRequest(_ value: String) {
    try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    let tmp = home.appendingPathComponent("first-run-request.\(ProcessInfo.processInfo.processIdentifier).tmp")
    guard (try? value.write(to: tmp, atomically: false, encoding: .utf8)) != nil else { return }
    _ = try? FileManager.default.replaceItemAt(firstRunRequest, withItemAt: tmp)
}

// The daemon's state, handed to the page when it changes; "ready" and "failed" (its Retry is here) bring a hidden window back.
@MainActor func pollState() {
    // The page gets the state as parsed and written back, so only JSON reaches its script, never the file's raw text;
    // a file caught half-written isn't valid yet and waits for the next poll.
    guard let text = try? String(contentsOf: firstRunState, encoding: .utf8), text != lastState,
          let data = text.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let json = try? JSONSerialization.data(withJSONObject: obj), let state = String(data: json, encoding: .utf8) else { return }
    if let phase = obj["phase"] as? String {
        // Setup stopped can't be closed, so it doesn't float: other apps can cover it; the next state floats it again.
        window.level = (phase == "failed") ? .normal : .floating
        if phase == "ready" || phase == "failed" { showWindow() }
    }
    // Remembered as delivered only once the page had window.firstRun (its bundle may still be loading); otherwise the next poll retries.
    web.evaluateJavaScript("window.firstRun ? (window.firstRun(\(state)), true) : false") { result, _ in
        if (result as? Bool) == true { lastState = text }
    }
}

final class Bridge: NSObject, WKScriptMessageHandler, NSWindowDelegate {
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let body = m.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "language", "start":  // "start" is the older page's answer: the language picked on its first slide
            if let code = body["language"] as? String, !code.isEmpty {
                languageChosen = true
                say("language " + code)
            }
            if action == "start" { hideWindow() }  // the older page has nothing more to show
        case "hide": hideWindow()
        case "request": if let value = body["value"] as? String { writeRequest(value) }
        case "done": fadeOutAndFinish()
        case "later": if languageChosen { hideWindow() } else { finish("later") }  // after a language, voice goes on: Escape hides
        default: break
        }
    }
    // The close button: before a language is chosen it dismisses the window; after, the page decides, as for Escape
    // (it hides while voice sets up, and ends the first run once setup is over). A page without that hides.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard languageChosen else { finish("later") }
        web.evaluateJavaScript("window.firstRunClose ? window.firstRunClose() : 'none'") { result, _ in
            if (result as? String) == nil || (result as? String) == "none" { hideWindow() }
        }
        return false
    }
    func windowWillClose(_ notification: Notification) { finish("closed") }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // no Dock icon; the window alone
let bridge = Bridge()

let config = WKWebViewConfiguration()
config.userContentController.add(bridge, name: "tour")
// --slide N opens at slide N (from 1), for checking how each one looks.
let slide = args.firstIndex(of: "--slide").flatMap { args.indices.contains($0 + 1) ? Int(args[$0 + 1]) : nil } ?? 1
config.userContentController.addUserScript(WKUserScript(
    source: "window.TOUR_REPLAY = \(isReplay); window.TOUR_START = \(slide - 1); window.FIRST_RUN = {replay: \(isReplay), enroll: \(isEnroll), languages: \(languagesJSON)}",
    injectionTime: .atDocumentStart, forMainFrameOnly: true))
let size = NSSize(width: 760, height: 460)
let web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
// A string with an https base, as the orb's page: WebKit won't run module scripts from file://,
// and the live orb's WebGPU needs a secure context.
web.loadHTMLString(html, baseURL: URL(string: "https://localhost/"))

let window = NSWindow(
    contentRect: NSRect(origin: .zero, size: size),
    styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
window.title = "Welcome to Voice Assistant"
window.titleVisibility = .hidden
window.titlebarAppearsTransparent = true
window.isMovableByWindowBackground = true
window.contentView = web
window.delegate = bridge
window.level = .floating  // above the app you start voice from
window.center()
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(web)  // the arrow keys and Escape reach the slides
app.activate(ignoringOtherApps: true)

try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
try? String(ProcessInfo.processInfo.processIdentifier).write(to: tourOpen, atomically: true, encoding: .utf8)
Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { _ in
    MainActor.assumeIsolated { pollState() }
    guard let step = try? String(contentsOf: tourCommand, encoding: .utf8) else { return }
    try? FileManager.default.removeItem(at: tourCommand)
    let command = step.trimmingCharacters(in: .whitespacesAndNewlines)
    if command == "show" {
        MainActor.assumeIsolated { showWindow() }
        try? "shown".write(to: tourResult, atomically: true, encoding: .utf8)
        return
    }
    let arg = String(data: try! JSONSerialization.data(withJSONObject: [command]), encoding: .utf8)!
    web.evaluateJavaScript("window.tourGo ? window.tourGo(\(arg)[0]) : 'not ready'") { result, _ in
        let done = (result as? String) ?? "unknown"
        try? done.write(to: tourResult, atomically: true, encoding: .utf8)
    }
}
app.run()
