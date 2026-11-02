import AppKit

/// Only touches the disposable fixture app; verifies actual field text after every paste.
@MainActor enum WindowTransferCheck {
    static func launch() {
        let app = NSApplication.shared
        Task { @MainActor in
            let result = await run()
            if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(to: URL(fileURLWithPath: "/tmp/shigaraki-window-transfer-fixture.json"), options: .atomic)
            }
            DevelopmentChecks.printJSON(result); exit(result["passed"] as? Bool == true ? 0 : 2)
        }
        app.run()
    }
    static func run() async -> [String: Any] {
        guard !NSWorkspace.shared.runningApplications.contains(where: {
            $0.bundleIdentifier == Bundle.main.bundleIdentifier && $0.processIdentifier != ProcessInfo.processInfo.processIdentifier
        }), let fixture = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "jp.ys.koeflow.fixture" }),
              AXIsProcessTrusted() else { return ["passed": false, "error": "fixtureOrPermissionMissing"] }
        fixture.activate(options: [])
        try? await Task.sleep(for: .milliseconds(200))
        let application = AXUIElementCreateApplication(fixture.processIdentifier)
        var reference: CFTypeRef?
        guard AXUIElementCopyAttributeValue(application, kAXFocusedUIElementAttribute as CFString, &reference) == .success,
              let reference, CFGetTypeID(reference) == AXUIElementGetTypeID() else {
            return ["passed": false, "error": "focusedFixtureFieldMissing"]
        }
        let field = reference as! AXUIElement
        func value() -> String? {
            var v: CFTypeRef?
            guard AXUIElementCopyAttributeValue(field, kAXValueAttribute as CFString, &v) == .success else { return nil }
            return v as? String
        }
        func select(_ position: Int) {
            var range = CFRange(location: position, length: 0)
            if let v = AXValueCreate(.cfRange, &range) { AXUIElementSetAttributeValue(field, kAXSelectedTextRangeAttribute as CFString, v) }
        }
        let original = "前🙂\n\n後"
        func reset() -> Bool {
            AXUIElementSetAttributeValue(field, kAXValueAttribute as CFString, original as CFString)
            select(3); return value() == original
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let window = NSWindow(contentRect: NSRect(x: 100, y: 200, width: 360, height: 120),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "信楽 — 転記検証"; window.isReleasedWhenClosed = false
        func showOwnWindow() async {
            app.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil)
            for _ in 0..<10 {
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier { return }
                try? await Task.sleep(for: .milliseconds(25))
            }
        }
        var cases: [[String: Any]] = []
        guard reset() else { return ["passed": false, "error": "fixtureResetFailed"] }
        let beforeWindow = TextTarget()
        let remembered = beforeWindow.capture(application: fixture)
        await showOwnWindow()
        let unchanged = beforeWindow.matchesCapture(application: fixture)
        let returned = await beforeWindow.finish("確認。", restoreFromOwnApp: true)
        let expected = "前🙂確認。\n\n後"
        cases.append(["case": "captureBeforeOwnWindowAndReturn", "passed": remembered && unchanged && returned && value() == expected,
                      "captured": remembered, "unchanged": unchanged, "pasted": returned,
                      "captureDiagnostic": beforeWindow.captureDiagnostic])
        try? await Task.sleep(for: .milliseconds(1100))
        guard reset() else { return ["passed": false, "error": "fixtureResetFailed"] }
        await showOwnWindow()
        let target = TextTarget()
        let captured = target.capture(application: fixture)
        let context = target.writingContext(scope: .nearby, application: fixture)
        let stayedOwn = NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier
        let pasted = await target.finish("確認。", restoreFromOwnApp: true)
        cases.append(["case": "backgroundCaptureAndReturn", "passed": captured && stayedOwn && pasted && value() == expected &&
                      context.bundleID == "jp.ys.koeflow.fixture", "text": value() ?? "missing",
                      "captured": captured, "stayedOwn": stayedOwn, "pasted": pasted, "reason": target.reason,
                      "captureDiagnostic": target.captureDiagnostic,
                      "contextBundle": context.bundleID, "ownPID": ProcessInfo.processInfo.processIdentifier,
                      "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0])
        let repeated = await target.finish("確認。", restoreFromOwnApp: true)
        cases.append(["case": "sameTargetCannotPasteTwice", "passed": !repeated && value() == expected])
        try? await Task.sleep(for: .milliseconds(1100))
        for changedValue in [false, true] {
            guard reset() else { return ["passed": false, "error": "fixtureResetFailed"] }
            await showOwnWindow()
            let stale = TextTarget(); let captured = stale.capture(application: fixture)
            if changedValue { AXUIElementSetAttributeValue(field, kAXValueAttribute as CFString, "変更済み" as CFString) }
            else { select((original as NSString).length) }
            let before = value()
            let clipboard = NSPasteboard.general.changeCount
            let pasted = await stale.finish("入れてはいけない", restoreFromOwnApp: true)
            cases.append(["case": changedValue ? "changedValueBlocksTransfer" : "changedCursorBlocksTransfer",
                          "captured": captured, "pasted": pasted, "reason": stale.reason,
                          "frontmostPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
                          "passed": captured && !pasted && value() == before && NSPasteboard.general.changeCount == clipboard &&
                            NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier])
        }
        if let other = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "com.apple.finder" }), reset() {
            await showOwnWindow()
            let stale = TextTarget(); let captured = stale.capture(application: fixture)
            other.activate(options: [])
            try? await Task.sleep(for: .milliseconds(200))
            let clipboard = NSPasteboard.general.changeCount
            let pasted = await stale.finish("入れてはいけない", restoreFromOwnApp: true)
            cases.append(["case": "anotherApplicationBlocksTransfer",
                          "passed": captured && !pasted && value() == original && NSPasteboard.general.changeCount == clipboard &&
                            NSWorkspace.shared.frontmostApplication?.processIdentifier == other.processIdentifier])
        } else { cases.append(["case": "anotherApplicationBlocksTransfer", "passed": false]) }
        window.close()
        return ["passed": cases.allSatisfy { $0["passed"] as? Bool == true }, "cases": cases]
    }
}
