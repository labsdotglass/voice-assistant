// Audio helper: mic + speakers through macOS voice processing (echo cancellation),
// so the assistant's own voice is removed from the mic and the user can talk over it.
//
//   audio_helper --out-rate 24000 [--orb <orb-page/dist/orb.html> [--orb-hidden]]
//
// stdout: the cleaned mic, raw 16 kHz mono int16, continuously.
// stdin:  framed messages, [type: 1 byte][length: UInt32 LE][payload]
//           "A": audio to play, mono int16 at --out-rate
//           "F": flush, stop playing everything queued (barge-in)
//           "S": orb state, JSON {"voice": "...", "busy": bool, "opening": bool, "hidden": bool}
//           "V": whether the Voice panel is showing, "1" or "0" (for the orb's menu)
//           "T": your queued tasks, JSON {"waiting": n, "tasks": [{"text", "status"}]} (the orb's badge and task panel)
//           "M": drop the orb at a spot, JSON {"x", "y"}: its centre, as fractions of the screen (tests, demo)
//           "P": play a soft tick (the tour moved by voice)
//           "K": press a Claude app shortcut, "new_session" (Cmd+N) or "next_session" (Ctrl+Tab); answers
//                on stderr "shortcut: done", "no-access", "no-app" or "not-front"
//           "C": the menu's settings, JSON {"language", "voice", "pass_on", "languages": [[id, label]], "voices": [[id, label]], "modes": [[id, label]], "progress": bool}
//
// With --orb it also shows a floating, transparent, always-on-top window with the
// The AiAvatar orb (WebGPU, orb-page/), driven by the "S" messages. --orb-hidden starts it out of sight, as
// "hidden" keeps it while the onboarding window shows its own orb (the first run, learning your voice again).
// stderr: logs.
import AppKit
import AVFoundation
import Foundation
import WebKit

func log(_ s: String) { FileHandle.standardError.write(("audio_helper: " + s + "\n").data(using: .utf8)!) }

func arg(_ name: String) -> String? {
    guard let i = CommandLine.arguments.firstIndex(of: name), i + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[i + 1]
}
let outRate = Double(arg("--out-rate") ?? "") ?? 24000

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // no Dock icon, no menu bar

// The audio side: voice processing (echo cancellation) on the mic and the speaker, the player for the
// assistant's voice and the tick, and the mic tap. Built once at start, and built again from scratch when
// the devices change (earphones, AirPods), in this same process so the orb stays put.
let playFormat = AVAudioFormat(standardFormatWithSampleRate: outRate, channels: 1)!
let micOut = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
let stdout = FileHandle.standardOutput

// A soft tick (the system's Tink, turned down), for "P" messages: the tour moved by voice. Played here, by
// voice's own audio, because voice processing ducks other apps' sound: a tick from the tour's window was
// near silent. Echo cancellation takes it out of the mic as it does speech.
let tick: AVAudioPCMBuffer? = {
    guard let file = try? AVAudioFile(forReading: URL(fileURLWithPath: "/System/Library/Sounds/Tink.aiff")),
          let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length)),
          (try? file.read(into: buffer)) != nil else { return nil }
    return buffer
}()

// The orb's meters (0...1): the mic's level and the assistant's playback level, below.
var micLevel: Float = 0
var playLevel: Float = 0
let levelLock = NSLock()

// The current engine and its nodes. Swapped on the main thread; the thread reading the daemon's messages
// plays through them, so it takes audioLock.
var engine: AVAudioEngine!
var player: AVAudioPlayerNode!
var tickPlayer: AVAudioPlayerNode!
var micFormat: AVAudioFormat!
let audioLock = NSLock()
var engineObserver: NSObjectProtocol?

func buildAudio() throws {
    let e = AVAudioEngine()
    let input = e.inputNode
    do {
        _ = e.mainMixerNode  // the output graph must exist before voice processing is turned on
        try input.setVoiceProcessingEnabled(true)
        if !e.outputNode.isVoiceProcessingEnabled { try e.outputNode.setVoiceProcessingEnabled(true) }

        // Speaker side.
        let voice = AVAudioPlayerNode()
        e.attach(voice)
        e.connect(voice, to: e.mainMixerNode, format: playFormat)
        let ticker = AVAudioPlayerNode()
        e.attach(ticker)
        if let tick { e.connect(ticker, to: e.mainMixerNode, format: tick.format) }
        ticker.volume = 0.35

        // Mic side: voice processing hands over several channels; channel 0 is the processed voice. Its
        // format depends on the device (48 kHz x9 on the MacBook mic, 24 kHz x3 on AirPods).
        let inFormat = input.outputFormat(forBus: 0)
        let monoIn = AVAudioFormat(standardFormatWithSampleRate: inFormat.sampleRate, channels: 1)!
        let converter = AVAudioConverter(from: monoIn, to: micOut)!
        input.installTap(onBus: 0, bufferSize: 960, format: inFormat) { buf, _ in
            guard let mono = AVAudioPCMBuffer(pcmFormat: monoIn, frameCapacity: buf.frameLength),
                  let src = buf.floatChannelData?[0], let dst = mono.floatChannelData?[0] else { return }
            mono.frameLength = buf.frameLength
            dst.update(from: src, count: Int(buf.frameLength))

            let capacity = AVAudioFrameCount(Double(buf.frameLength) * micOut.sampleRate / monoIn.sampleRate) + 32
            guard let out = AVAudioPCMBuffer(pcmFormat: micOut, frameCapacity: capacity) else { return }
            var fed = false
            var err: NSError?
            converter.convert(to: out, error: &err) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true
                status.pointee = .haveData
                return mono
            }
            if out.frameLength > 0, let p = out.int16ChannelData?[0] {
                stdout.write(Data(bytes: p, count: Int(out.frameLength) * 2))
            }

            // Mic meter for the orb: how loud the (processed) mic is, the same way as the playback meter.
            var sum: Float = 0
            for i in 0 ..< Int(buf.frameLength) { sum += src[i] * src[i] }
            let db = 10 * log10(sum / Float(max(buf.frameLength, 1)) + 1e-10)
            let target = min(max((db + 60) / 35, 0), 1)  // about -60 dBFS (quiet room) to -25 dBFS (talking close)
            levelLock.lock()
            micLevel += (target - micLevel) * (target > micLevel ? 0.6 : 0.15)
            levelLock.unlock()
        }

        // Playback meter for the orb: how loud the assistant's voice is right now, 0...1,
        // measured on what the player actually renders. Fast attack, slower release, so the
        // orb follows syllables and settles in the pauses between words.
        voice.installTap(onBus: 0, bufferSize: 512, format: playFormat) { buf, _ in
            guard let p = buf.floatChannelData?[0], buf.frameLength > 0 else { return }
            var sum: Float = 0
            for i in 0 ..< Int(buf.frameLength) { sum += p[i] * p[i] }
            let db = 10 * log10(sum / Float(buf.frameLength) + 1e-10)
            let target = min(max((db + 50) / 32, 0), 1)  // about -50 dBFS (silence) to -18 dBFS (full)
            levelLock.lock()
            playLevel += (target - playLevel) * (target > playLevel ? 0.6 : 0.15)
            levelLock.unlock()
        }

        try e.start()
        voice.play()
        audioLock.lock()
        (engine, player, tickPlayer, micFormat) = (e, voice, ticker, inFormat)
        audioLock.unlock()
        watchDevices(e)
    } catch {
        // Taken apart, so it doesn't keep voice processing or its tap and spoil the next try.
        input.removeTap(onBus: 0)
        e.stop()
        try? input.setVoiceProcessingEnabled(false)
        throw error
    }
}

// Earphones, AirPods or another mic or speakers connected or gone: macOS stops the engine and posts this,
// often several times over a second or two. Once the notices settle, build a new engine for the new
// devices. Restarting the same engine doesn't hold with voice processing on (back on the MacBook mic its
// tap stayed silent and its input format read stale); a new one picks them up, as a fresh helper did,
// and the orb stays where it is. If a new engine won't start, exit with devicesChanged: the daemon
// starts a fresh helper (helper_transport.py, DEVICES_CHANGED), whose orb reopens where you left it
// (the window saves its frame, setFrameAutosaveName below).
let devicesChanged: Int32 = 3
var devicesSettle: DispatchWorkItem?
var switchingDevices = false  // the orb shows gray from the first notice until the new engine is up
var rebuildRun = 0  // each settled change starts a new run of tries; an older run's retries stop
func watchDevices(_ e: AVAudioEngine) {
    if let engineObserver { NotificationCenter.default.removeObserver(engineObserver) }
    engineObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: e, queue: .main) { _ in
        log("devices changed: the engine stopped")
        if !switchingDevices {
            switchingDevices = true
            grayOrb()
        }
        devicesSettle?.cancel()
        rebuildRun += 1  // a retry still pending from an earlier change gives way to this one
        let run = rebuildRun
        let settle = DispatchWorkItem { rebuildAudio(run: run) }
        devicesSettle = settle
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5, execute: settle)
    }
}

func rebuildAudio(run: Int, attempt: Int = 1) {
    guard run == rebuildRun else { return }  // a newer change took over
    // The old engine's notices no longer count: stopping it, or letting go of its voice processing, can
    // post one, which would start another rebuild.
    if let engineObserver { NotificationCenter.default.removeObserver(engineObserver) }
    engineObserver = nil
    let old = engine
    old?.inputNode.removeTap(onBus: 0)
    old?.stop()
    try? old?.inputNode.setVoiceProcessingEnabled(false)  // let go of the mic before a new engine takes it
    do {
        try buildAudio()
        log("devices changed: new engine, mic \(Int(micFormat.sampleRate))Hz x\(micFormat.channelCount)")
        switchingDevices = false
        if let lastOrbState { setOrb(lastOrbState) }  // back to its colors: voice hears and speaks again
    } catch {
        guard attempt < 4 else {
            log("devices changed: no new engine (\(error)); restarting the helper")
            exit(devicesChanged)
        }
        log("devices changed: new engine failed (\(error)), trying again")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { rebuildAudio(run: run, attempt: attempt + 1) }
    }
}

do { try buildAudio() } catch {
    log("audio failed to start: \(error)")
    exit(1)
}
log("ready: mic \(Int(micFormat.sampleRate))Hz x\(micFormat.channelCount) -> 16000Hz mono, speaker \(Int(outRate))Hz")


// The floating orb.
final class OrbBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        log("orb page: \(m.body)")
        // A state sent before the page loaded was dropped; show the latest one now.
        if let body = m.body as? [String: Any], body["ready"] != nil {
            if let last = lastOrbState { setOrb(last) }
            // The shadow is traced from what the window shows: once the orb is drawn, trace it again.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { orbWindow?.invalidateShadow() }
        }
    }
}
var lastOrbState: Data?
let orbBridge = OrbBridge()
var orbSettings: [String: Any] = [:]  // the menu's settings and their choices; "C" messages keep this current
var panelVisible = true  // the mod opens the Voice panel when voice starts; "V" messages keep this current

// Right-click on the orb: our menu instead of WebKit's (Reload, Inspect...).
// Its commands go to Python as stderr command lines, and the mod acts on them:
// show or hide the Voice transcript panel, clear the transcript, or stop voice.
// Voice and Pass on my words are settings the daemon applies itself, as is More's Progress updates;
// More's Learn my voice again and Show the tour go to the daemon and the mod.
final class OrbWebView: WKWebView {
    // Hover: the page shows its drag handle while the pointer is over the orb, and the pointer is
    // a grab hand over the handle. Tracked here, active always: the orb's app is never the active one.
    var hoverArea: NSTrackingArea?
    var isOverHandle = false

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let old = hoverArea { removeTrackingArea(old) }
        let area = NSTrackingArea(rect: bounds, options: [.activeAlways, .mouseEnteredAndExited, .mouseMoved, .inVisibleRect], owner: self)
        addTrackingArea(area)
        hoverArea = area
    }

    // Where the handle is (orb.css: the orb's center), with room around it so it's easy to hit.
    func handleRect() -> NSRect {
        NSRect(x: bounds.midX - 16, y: bounds.midY - 10, width: 32, height: 20)
    }

    override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        evaluateJavaScript("window.setHover && window.setHover(true)")
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        evaluateJavaScript("window.setHover && window.setHover(false)")
        if isOverHandle { NSCursor.arrow.set() }
        isOverHandle = false
    }

    override func mouseMoved(with event: NSEvent) {
        super.mouseMoved(with: event)
        guard !isDraggingOrb else { return }
        isOverHandle = handleRect().contains(convert(event.locationInWindow, from: nil))
        (isOverHandle ? NSCursor.openHand : NSCursor.arrow).set()
    }

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        menu.removeAllItems()
        let toggle = panelVisible
            ? NSMenuItem(title: "Hide transcript", action: #selector(hidePanel), keyEquivalent: "")
            : NSMenuItem(title: "Show transcript", action: #selector(showPanel), keyEquivalent: "")
        toggle.target = self
        menu.addItem(toggle)
        let clear = NSMenuItem(title: "Clear conversation", action: #selector(clearTranscript), keyEquivalent: "")
        clear.target = self
        menu.addItem(clear)
        menu.addItem(.separator())
        addChoices(to: menu, title: "Language", list: "languages", key: "language", action: #selector(pickLanguage(_:)))
        addChoices(to: menu, title: "Voice", list: "voices", key: "voice", action: #selector(pickVoice(_:)))
        addChoices(to: menu, title: "Pass on my words", list: "modes", key: "pass_on", action: #selector(pickPassOn(_:)))
        // More: what you need now and then.
        let more = NSMenu()
        for (title, action) in [("Learn my voice again", #selector(learnVoice)), ("Show the tour", #selector(showTour))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            more.addItem(item)
        }
        more.addItem(.separator())
        let progress = NSMenuItem(title: "Progress updates", action: #selector(toggleProgress), keyEquivalent: "")
        progress.target = self
        progress.state = (orbSettings["progress"] as? Bool ?? true) ? .on : .off
        more.addItem(progress)
        let moreItem = NSMenuItem(title: "More", action: nil, keyEquivalent: "")
        moreItem.submenu = more
        menu.addItem(moreItem)
        menu.addItem(.separator())
        let stop = NSMenuItem(title: "Stop voice", action: #selector(stopVoice), keyEquivalent: "")
        stop.target = self
        menu.addItem(stop)
    }

    // A submenu of choices from the latest "C" message, the current one ticked.
    func addChoices(to menu: NSMenu, title: String, list: String, key: String, action: Selector) {
        guard let choices = orbSettings[list] as? [[String]], !choices.isEmpty else { return }
        let sub = NSMenu()
        for c in choices where c.count == 2 {
            if c[0] == "-" {  // settings.MENU_DIVIDER
                sub.addItem(.separator())
                continue
            }
            let item = NSMenuItem(title: c[1], action: action, keyEquivalent: "")
            item.target = self
            item.representedObject = c[0]
            item.state = (orbSettings[key] as? String) == c[0] ? .on : .off
            sub.addItem(item)
        }
        let parent = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        parent.submenu = sub
        menu.addItem(parent)
    }

    @objc func pickLanguage(_ item: NSMenuItem) { log("command: language \(item.representedObject as? String ?? "")") }
    @objc func pickVoice(_ item: NSMenuItem) { log("command: voice \(item.representedObject as? String ?? "")") }
    @objc func pickPassOn(_ item: NSMenuItem) { log("command: pass-on \(item.representedObject as? String ?? "")") }
    @objc func learnVoice() { log("command: more enroll") }
    @objc func showTour() { log("command: more tour") }
    @objc func toggleProgress() { log("command: more progress") }
    @objc func stopVoice() { log("command: stop") }
    @objc func showPanel() { log("command: show-panel") }
    @objc func clearTranscript() { log("command: clear") }
    @objc func hidePanel() { log("command: hide-panel") }
}
var orbView: WKWebView?
var orbWindow: NSWindow?
var orbHidden = CommandLine.arguments.contains("--orb-hidden")  // out of sight: "S" messages' hidden keeps this current

// Where the orb sits: let go of it near any edge of the screen and it snaps to that edge (to two, in a
// corner), keeping a small gap. It starts in the bottom right corner.
// Declared before the orb is made: a global in this file is set when its line runs, so declaring these
// after the block below would happen after it uses them.
let orbGap: CGFloat = 24  // between the orb you see and the screen's edge
let orbSnap: CGFloat = 100  // let go this close to an edge and it snaps there
let orbInset: CGFloat = 0.12  // the orb fills the middle 76% of its window (orb.css): the clear margin around it

// Where the window goes so the orb you see sits against the edges near it, or nil if no edge is near.
func snapped(_ frame: NSRect, in area: NSRect) -> NSPoint? {
    let m = frame.width * orbInset, orb = frame.insetBy(dx: m, dy: m)
    var o = frame.origin, moved = false
    func snap(_ low: CGFloat, _ high: CGFloat, _ lowEdge: CGFloat, _ highEdge: CGFloat, _ v: inout CGFloat) {
        let toLow = low - lowEdge - orbGap, toHigh = highEdge - orbGap - high
        if toLow < orbSnap || toHigh < orbSnap {
            v += toLow < toHigh ? -toLow : toHigh
            moved = true
        }
    }
    snap(orb.minX, orb.maxX, area.minX, area.maxX, &o.x)
    snap(orb.minY, orb.maxY, area.minY, area.maxY, &o.y)
    return moved ? o : nil
}

if let page = arg("--orb") {
    let url = URL(fileURLWithPath: page)
    let size: CGFloat = 132
    let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
    let window = NSWindow(
        contentRect: NSRect(x: screen.maxX - size - 24, y: screen.minY + 24, width: size, height: size),
        styleMask: [.borderless], backing: .buffered, defer: false)
    window.isOpaque = false
    window.backgroundColor = .clear
    window.hasShadow = true  // macOS's own soft shadow, as on the task panel; it follows the round orb
    window.level = .floating
    window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
    window.setFrameAutosaveName("VoiceOrb")  // remembers where you dragged it
    // Against the edges it was left near (the bottom right corner at first), with today's gap.
    if let to = snapped(window.frame, in: screen) { window.setFrameOrigin(to) }

    let config = WKWebViewConfiguration()
    config.userContentController.add(orbBridge, name: "orb")
    let view = OrbWebView(frame: NSRect(x: 0, y: 0, width: size, height: size), configuration: config)
    view.setValue(false, forKey: "drawsBackground")
    // A string with an https base: WebKit won't run module scripts from file://, and the
    // base keeps the page a secure context, which WebGPU requires.
    let html = (try? String(contentsOf: url, encoding: .utf8)) ?? "<p>orb page missing</p>"
    view.loadHTMLString(html, baseURL: URL(string: "https://localhost/"))
    window.contentView = view
    if orbHidden { log("orb hidden: the onboarding window shows its own") } else { window.orderFrontRegardless() }
    orbView = view
    orbWindow = window
    dragOrb(window)
}

// Dragging the orb. WebKit takes the mouse, so the window's own background drag never starts: a left drag
// on the orb moves it here instead (the page needs no left clicks). Let go near an edge and it snaps there.
var isDraggingOrb = false  // pressed on the handle, or dragging the orb: the hand stays closed until you let go

func dragOrb(_ window: NSWindow) {
    var grab: NSPoint?  // where the mouse took hold, relative to the window's origin
    NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp]) { event in
        guard event.window === window else { return event }
        let mouse = NSEvent.mouseLocation
        switch event.type {
        case .leftMouseDown:
            grab = NSPoint(x: mouse.x - window.frame.minX, y: mouse.y - window.frame.minY)
            // A press on the handle closes the hand at once, before any drag.
            if let view = orbView as? OrbWebView, view.handleRect().contains(view.convert(event.locationInWindow, from: nil)) {
                isDraggingOrb = true
                NSCursor.closedHand.set()
            }
        case .leftMouseDragged:
            if let g = grab { window.setFrameOrigin(NSPoint(x: mouse.x - g.x, y: mouse.y - g.y)) }
            if !isDraggingOrb { isDraggingOrb = true; NSCursor.closedHand.set() }
        default:
            grab = nil
            if isDraggingOrb {
                isDraggingOrb = false
                let view = orbView as? OrbWebView, inside = view.map { $0.handleRect().contains($0.convert(event.locationInWindow, from: nil)) } ?? false
                (inside ? NSCursor.openHand : NSCursor.arrow).set()
            }
            dropped(window)
        }
        return nil
    }
}

// You let go of the orb: near an edge, it slides against it.
func dropped(_ window: NSWindow) {
    let area = (window.screen ?? NSScreen.main)?.visibleFrame ?? window.frame
    let to = snapped(window.frame, in: area)
    log("orb dropped at \(Int(window.frame.minX)),\(Int(window.frame.minY))" + (to.map { ": snapping to \(Int($0.x)),\(Int($0.y))" } ?? ""))
    if let to, to != window.frame.origin {
        window.setFrame(NSRect(origin: to, size: window.frame.size), display: true, animate: true)
    }
}

// A "K" message: press one of the Claude app's own shortcuts for you (voice's "new session", "next session").
// The Claude app is brought to the front first, and the keys go only if it really is in front, so they
// can never land in another app. Pressing keys needs Accessibility for Voice Assistant; macOS asks once.
let claudeApp = "com.anthropic.claudefordesktop"

func pressInClaude(_ name: String) {
    let shortcuts: [String: (CGKeyCode, CGEventFlags)] = ["new_session": (45, .maskCommand), "next_session": (48, .maskControl)]  // N, Tab
    guard let (key, flags) = shortcuts[name] else { return }
    DispatchQueue.main.async {
        let ask = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(ask) else { log("shortcut: no-access"); return }
        guard let claude = NSRunningApplication.runningApplications(withBundleIdentifier: claudeApp).first else {
            log("shortcut: no-app"); return
        }
        claude.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == claudeApp else { log("shortcut: not-front"); return }
            for isDown in [true, false] {
                let event = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: isDown)
                event?.flags = flags
                event?.post(tap: .cghidEventTap)
            }
            log("shortcut: done")
        }
    }
}

// An "M" message: carry the orb to a spot, as a drag would, and let go there. The spot is where its
// centre goes, as fractions of the screen's usable area (0,0 bottom left; 1,1 top right).
func dropOrb(_ json: Data) {
    guard let spot = try? JSONSerialization.jsonObject(with: json) as? [String: Double],
          let fx = spot["x"], let fy = spot["y"] else { return }
    DispatchQueue.main.async {
        guard let window = orbWindow else { return }
        let area = (window.screen ?? NSScreen.main)?.visibleFrame ?? window.frame, size = window.frame.size
        let origin = NSPoint(x: area.minX + area.width * fx - size.width / 2, y: area.minY + area.height * fy - size.height / 2)
        window.setFrame(NSRect(origin: origin, size: size), display: true, animate: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { dropped(window) }
    }
}

// Queued tasks: a small circle with the waiting count on the orb's top-right edge, moving with it; a click
// opens the orb's own task panel (a popover) listing each task and its status.
final class BadgeView: NSView {
    var count = 0 { didSet { needsDisplay = true } }
    var onClick: (() -> Void)?

    override func draw(_ dirtyRect: NSRect) {
        let circle = NSBezierPath(ovalIn: bounds.insetBy(dx: 1, dy: 1))
        NSColor.systemBlue.setFill()
        circle.fill()
        NSColor.white.withAlphaComponent(0.9).setStroke()
        circle.lineWidth = 1.5
        circle.stroke()
        let text = "\(count)" as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .semibold), .foregroundColor: NSColor.white,
        ]
        let size = text.size(withAttributes: attrs)
        text.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2), withAttributes: attrs)
    }

    override func mouseDown(with event: NSEvent) { onClick?() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

let statusMark = ["waiting": "◷  Waiting", "running": "▶  Running", "done": "✓  Done", "cancelled": "✕  Cancelled"]
var badgeWindow: NSPanel?
var badgeView: BadgeView?
var taskPopover: NSPopover?
var lastTasks: [[String: String]] = []
var lastTasksJSON: Data?  // the last "T" message, to put the badge back when the orb shows again

// The task panel's content: one row per task, status first, newest at the bottom.
func taskList() -> NSViewController {
    let width: CGFloat = 300
    let stack = NSStackView()
    stack.translatesAutoresizingMaskIntoConstraints = false
    stack.widthAnchor.constraint(equalToConstant: width).isActive = true
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 8
    stack.edgeInsets = NSEdgeInsets(top: 12, left: 14, bottom: 12, right: 14)
    let title = NSTextField(labelWithString: "Queued tasks")
    title.font = .systemFont(ofSize: 13, weight: .semibold)
    stack.addArrangedSubview(title)
    if lastTasks.isEmpty {
        stack.addArrangedSubview(NSTextField(labelWithString: "Nothing queued."))
    }
    for task in lastTasks {
        let status = NSTextField(labelWithString: statusMark[task["status"] ?? ""] ?? task["status"] ?? "")
        status.font = .systemFont(ofSize: 11, weight: .medium)
        status.textColor = task["status"] == "running" ? .systemBlue : .secondaryLabelColor
        let text = NSTextField(wrappingLabelWithString: task["text"] ?? "")
        text.font = .systemFont(ofSize: 12)
        // Up to three wrapped lines at the panel's width (less its side insets), "…" ending the last one.
        // A fixed width, so the height is worked out for the real width rather than a narrower guess.
        text.translatesAutoresizingMaskIntoConstraints = false
        text.widthAnchor.constraint(equalToConstant: width - 28).isActive = true
        text.preferredMaxLayoutWidth = width - 28
        text.lineBreakMode = .byWordWrapping
        text.cell?.wraps = true
        text.cell?.truncatesLastVisibleLine = true
        text.maximumNumberOfLines = 3
        text.textColor = ["done", "cancelled"].contains(task["status"] ?? "") ? .secondaryLabelColor : .labelColor
        let row = NSStackView(views: [status, text])
        row.orientation = .vertical
        row.alignment = .leading
        row.spacing = 2
        stack.addArrangedSubview(row)
    }
    let vc = NSViewController()
    vc.view = stack
    return vc
}

func showTaskPanel() {
    guard let view = badgeView else { return }
    if let open = taskPopover, open.isShown { open.close(); return }
    let popover = NSPopover()
    popover.behavior = .transient  // a click anywhere else closes it
    popover.contentViewController = taskList()
    popover.show(relativeTo: view.bounds, of: view, preferredEdge: .maxY)
    taskPopover = popover
}

func setTasks(_ json: Data) {
    guard let obj = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else { return }
    let waiting = obj["waiting"] as? Int ?? 0
    let tasks = (obj["tasks"] as? [[String: Any]] ?? []).map { $0.compactMapValues { $0 as? String } }
    DispatchQueue.main.async {
        lastTasks = tasks
        lastTasksJSON = json
        guard let orb = orbWindow else { return }
        if badgeWindow == nil {
            let size: CGFloat = 24
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: size, height: size),
                                styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            panel.isOpaque = false
            panel.backgroundColor = .clear
            panel.hasShadow = true
            panel.level = .floating
            panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
            let view = BadgeView(frame: NSRect(x: 0, y: 0, width: size, height: size))
            view.onClick = showTaskPanel
            panel.contentView = view
            badgeWindow = panel
            badgeView = view
        }
        guard let panel = badgeWindow, let view = badgeView else { return }
        view.count = waiting
        if waiting > 0 && !orbHidden {
            // Centred on the orb's outline at its top right (45°), overlapping the edge. The orb fills
            // most of its window: its radius is about 0.46 of the window's width.
            let o = orb.frame, r = o.width * 0.46, half = panel.frame.width / 2
            panel.setFrameOrigin(NSPoint(x: o.midX + r * 0.7071 - half, y: o.midY + r * 0.7071 - half))
            if panel.parent == nil { orb.addChildWindow(panel, ordered: .above) }  // moves with the orb
            panel.orderFront(nil)
        } else if panel.parent != nil {
            orb.removeChildWindow(panel)
            panel.orderOut(nil)
        }
        if let open = taskPopover, open.isShown { open.contentViewController = taskList() }  // live while open
    }
}

func setOrb(_ json: Data) {
    guard let obj = try? JSONSerialization.jsonObject(with: json) as? [String: Any],
          let voice = obj["voice"] as? String else { return }
    let busy = (obj["busy"] as? Bool) == true
    let opening = (obj["opening"] as? Bool) == true
    let hidden = (obj["hidden"] as? Bool) == true
    DispatchQueue.main.async {
        lastOrbState = json
        if hidden != orbHidden { hideOrb(hidden) }
        if switchingDevices { return }  // stays gray until the new engine is up (rebuildAudio)
        // As a JSON string, not pasted between quotes: whatever the daemon sends stays a string.
        let arg = String(data: try! JSONSerialization.data(withJSONObject: [voice]), encoding: .utf8)!
        orbView?.evaluateJavaScript("window.setVoiceState && window.setVoiceState(\(arg)[0], \(busy), \(opening))")
    }
}

// Out of sight while the onboarding window shows its own orb, and back as it first appeared: where you left
// it, gray until voice is ready. Its task badge goes and comes with it.
func hideOrb(_ hidden: Bool) {
    orbHidden = hidden
    guard let window = orbWindow else { return }
    log(hidden ? "orb hidden: the onboarding window shows its own" : "orb shown")
    if hidden {
        taskPopover?.close()
        if let panel = badgeWindow, panel.parent != nil { window.removeChildWindow(panel); panel.orderOut(nil) }
        window.orderOut(nil)
    } else {
        window.orderFrontRegardless()
        if let last = lastTasksJSON { setTasks(last) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { window.invalidateShadow() }
    }
}

// Gray while the mic and speakers change over: 'starting' is the orb's not-ready look.
func grayOrb() {
    let last = lastOrbState.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
    let busy = last?["busy"] as? Bool ?? false
    let opening = last?["opening"] as? Bool ?? false
    orbView?.evaluateJavaScript("window.setVoiceState && window.setVoiceState('starting', \(busy), \(opening))")
}

// Push the playback and mic levels to the orb about 30 times a second, decaying when nothing plays.
var sentLevel: Float = -1
var sentMic: Float = -1
let orbDebug = ProcessInfo.processInfo.environment["VOICE_ORB_DEBUG"] == "1"
Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { _ in
    guard let view = orbView else { return }
    levelLock.lock()
    if !player.isPlaying { playLevel = 0 }
    let level = playLevel
    playLevel *= 0.92  // no fresh audio in this tick: settle toward silence
    let mic = micLevel
    levelLock.unlock()
    if abs(mic - sentMic) >= 0.02 {
        sentMic = mic
        if orbDebug { log("orb mic \(String(format: "%.2f", mic))") }
        view.evaluateJavaScript("window.setMicLevel && window.setMicLevel(\(String(format: "%.3f", mic)))")
    }
    if abs(level - sentLevel) < 0.02 { return }
    sentLevel = level
    if orbDebug { log("orb level \(String(format: "%.2f", level))") }
    view.evaluateJavaScript("window.setPlaybackLevel && window.setPlaybackLevel(\(String(format: "%.3f", level)))")
}

// Python went away (voice stopped): the orb shrinks toward its center and fades out, then the
// helper exits, rather than the orb just vanishing. Without an orb it exits at once.
func leave() -> Never {
    let secs = 0.35
    DispatchQueue.main.async {
        guard let window = orbWindow, let view = orbView else { exit(0) }
        view.evaluateJavaScript("""
            var s = document.documentElement.style;
            s.transformOrigin = '50% 50%';
            s.transition = 'transform \(secs)s cubic-bezier(.4,0,1,1), opacity \(secs)s ease-in';
            s.transform = 'scale(0.6)';
            s.opacity = '0';
            """)
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = secs
            ctx.timingFunction = CAMediaTimingFunction(name: .easeIn)
            window.animator().alphaValue = 0
            badgeWindow?.animator().alphaValue = 0
        }, completionHandler: { exit(0) })
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { exit(0) }  // never hang on the way out
    }
    while true { sleep(60) }
}

// Commands from Python, on their own thread.
Thread.detachNewThread {
    let stdin = FileHandle.standardInput
    func read(_ n: Int) -> Data {
        var d = Data()
        while d.count < n {
            let chunk = stdin.readData(ofLength: n - d.count)
            if chunk.isEmpty { leave() }  // Python went away
            d.append(chunk)
        }
        return d
    }
    while true {
        let header = read(5)
        let type = header[header.startIndex]
        let len = header.subdata(in: header.startIndex + 1 ..< header.startIndex + 5)
            .withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }.littleEndian
        let payload = len > 0 ? read(Int(len)) : Data()
        switch type {
        case UInt8(ascii: "A"):
            let frames = payload.count / 2
            guard frames > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(frames)),
                  let dst = buf.floatChannelData?[0] else { continue }
            buf.frameLength = AVAudioFrameCount(frames)
            payload.withUnsafeBytes { raw in
                let s = raw.bindMemory(to: Int16.self)
                for i in 0 ..< frames { dst[i] = Float(Int16(littleEndian: s[i])) / 32768 }
            }
            // While the devices change over the engine is stopped: that audio is dropped, not queued up.
            audioLock.lock(); if engine.isRunning { player.scheduleBuffer(buf) }; audioLock.unlock()
        case UInt8(ascii: "F"):
            // play() on a stopped engine throws, and would take the helper down.
            audioLock.lock(); player.stop(); if engine.isRunning { player.play() }; audioLock.unlock()
            levelLock.lock(); playLevel = 0; levelLock.unlock()
        case UInt8(ascii: "S"):
            setOrb(payload)
        case UInt8(ascii: "T"):
            setTasks(payload)
        case UInt8(ascii: "M"):
            dropOrb(payload)
        case UInt8(ascii: "P"):
            if let tick {
                audioLock.lock()
                if engine.isRunning {
                    tickPlayer.scheduleBuffer(tick, at: nil, options: .interrupts)
                    if !tickPlayer.isPlaying { tickPlayer.play() }
                }
                audioLock.unlock()
            }
        case UInt8(ascii: "K"):
            pressInClaude(String(decoding: payload, as: UTF8.self))
        case UInt8(ascii: "C"):
            if let obj = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                DispatchQueue.main.async { orbSettings = obj }
            }
        case UInt8(ascii: "V"):
            let visible = payload.first == UInt8(ascii: "1")
            DispatchQueue.main.async { panelVisible = visible }
            if orbDebug { log("panel visible: \(visible)") }
        default:
            log("unknown message type \(type)")
        }
    }
}

app.run()
