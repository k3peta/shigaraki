import AppKit
import ApplicationServices
import KoeFlowCore

@MainActor final class PageContextObserver {
    private var pages = PageContext()
    private var timer: Timer?
    private var pending = false
    private var generation = 0
    var allowed: (String) -> Bool = { _ in false }
    private let browsers: Set<String> = ["com.apple.Safari", "com.google.Chrome", "com.google.Chrome.canary", "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser", "org.mozilla.firefox"]
    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.sample() }
        }
    }
    func clear() { generation += 1; pages.clear() }
    func reference() -> String { pages.reference(now: ProcessInfo.processInfo.systemUptime) }
    private func sample() {
        _ = pages.reference(now: ProcessInfo.processInfo.systemUptime)
        guard AXIsProcessTrusted() else { clear(); return }
        guard let app = NSWorkspace.shared.frontmostApplication, app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
        let bundle = app.bundleIdentifier ?? ""
        guard allowed(bundle) else { clear(); return }
        guard browsers.contains(bundle), !pending else { return }
        let pid = app.processIdentifier, ticket = generation
        pending = true
        // AX providers can be slow. Never block the microphone/shortcut/UI path.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let title = Self.readTitle(pid: pid)
            Task { @MainActor in
                guard let self else { return }
                self.pending = false
                guard ticket == self.generation, self.allowed(bundle),
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { return }
                if let title { self.pages.observe(title: title, now: ProcessInfo.processInfo.systemUptime) }
                else { self.clear() }
            }
        }
    }
    nonisolated private static func readTitle(pid: pid_t) -> String? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.05)
        var reference: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &reference) == .success,
              let reference, CFGetTypeID(reference) == AXUIElementGetTypeID() else { return nil }
        let window = reference as! AXUIElement
        AXUIElementSetMessagingTimeout(window, 0.05)
        var title: CFTypeRef?
        guard AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title) == .success else { return nil }
        return title as? String
    }
}
