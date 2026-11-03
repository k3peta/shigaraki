import AppKit
import SwiftUI
import KoeFlowCore

/// Captures the real SwiftUI views with fixed sample text. No microphone,
/// screen capture, event monitoring, external app reads or model downloads.
@MainActor enum ManualPreview {
    static func render(_ arguments: [String]) async -> Bool {
        guard let i = arguments.firstIndex(of: "--render-manual-fixture"), arguments.indices.contains(i + 2) else { return false }
        let kind = arguments[i + 1], output = URL(fileURLWithPath: arguments[i + 2])
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        application.appearance = NSAppearance(named: .aqua)
        let model = FlowModel(); model.demoFile = URL(fileURLWithPath: "/dev/null")
        let window: NSWindow
        if kind.hasPrefix("setup") {
            let domain = "ShigarakiManualFixture.\(UUID().uuidString)"
            let defaults = UserDefaults(suiteName: domain)!
            let setup = SetupModel(settings: model.settings, defaults: defaults, fixture: kind == "setup-ready" ? "ready" : "missing")
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: 690), styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.contentView = NSHostingView(rootView: SetupView(setup: setup, settings: model.settings, finish: {}, openGuide: {}))
            window.title = "信楽の初回準備"
            defaults.removePersistentDomain(forName: domain)
        } else if kind == "caption" {
            model.prepareManualFixture(recording: true)
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 328, height: 132), styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = NSHostingView(rootView: OverlayView(model: model, settings: model.settings))
        } else {
            model.prepareManualFixture(recording: kind == "listening")
            if kind == "profiles" {
                model.settings.inputContextProfiles = [InputContextProfile(kind: .website, identifier: "example.com", topic: "AIへの作業指示", terms: ["Codex", "Antigravity"])]
            } else if kind.hasPrefix("profiles-limit") {
                model.settings.inputContextProfiles = (0..<InputContextProfile.maximumProfiles).map {
                    InputContextProfile(kind: .application, identifier: "com.example.app\($0)")
                }
            }
            window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 680), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            window.title = "信楽 — メインパネル"
            let contextIdentifier = kind == "profiles-limit" ? "com.example.additional" : kind == "profiles-limit-edit" ? "com.example.app0" : ""
            window.contentView = NSHostingView(rootView: FlowView(model: model, settings: model.settings, initialTab: kind == "settings" ? 2 : kind == "dictionary" ? 1 : kind.hasPrefix("profiles") ? 3 : 0, initialContextIdentifier: contextIdentifier))
        }
        window.isReleasedWhenClosed = false; window.backgroundColor = .windowBackgroundColor
        window.center(); window.orderFrontRegardless() // Deliberately do not activate or take keyboard focus.
        try? await Task.sleep(for: .milliseconds(500))
        guard let view = window.contentView?.superview else { window.orderOut(nil); return true }
        view.layoutSubtreeIfNeeded(); view.displayIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { window.orderOut(nil); return true }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { window.orderOut(nil); return true }
        do {
            try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
            try png.write(to: output, options: .withoutOverwriting)
            DevelopmentChecks.printJSON(["rendered": true, "kind": kind, "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh,
                "sampleTextOnly": true, "microphoneUsed": false, "modelLoaded": false])
        } catch { DevelopmentChecks.printJSON(["rendered": false, "error": error.localizedDescription]) }
        window.orderOut(nil)
        return true
    }
}
