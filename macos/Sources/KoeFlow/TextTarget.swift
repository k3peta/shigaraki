import AppKit
import ApplicationServices
import KoeFlowCore
import CryptoKit

@MainActor final class TextTarget {
    struct ContextIdentity: Equatable {
        let field: AXUIElement
        let pid: pid_t
        let digest: Data
        let range: NSRange
        static func == (lhs: Self, rhs: Self) -> Bool {
            lhs.pid == rhs.pid && CFEqual(lhs.field, rhs.field) && lhs.digest == rhs.digest && lhs.range == rhs.range
        }
    }
    private(set) var confirmedContextIdentity: ContextIdentity?
    func contextIdentity() -> ContextIdentity? {
        guard let element, let capturedValue, capturedSelection != nil else { return nil }
        return identity(of: element, expected: capturedValue)
    }
    private func identity(of field: AXUIElement, expected: String) -> ContextIdentity? {
        guard AXIsProcessTrusted(), expected.utf16.count <= 100_000, !expected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, isFocused(field),
              string(field, kAXValueAttribute) == expected, let range = selection(field),
              WritingContext.surrounding(expected, range: range) != nil else { return nil }
        return ContextIdentity(field: field, pid: pid, digest: Data(SHA256.hash(data: Data(expected.utf8))), range: range)
    }
    static let pasteEventMarker: Int64 = 0x5348494741524b49
    private var element: AXUIElement?
    private var pid: pid_t = 0
    private(set) var appName = ""
    private(set) var reason = ""
    private(set) var contextExcluded = false
    private var capturedValue: String?
    private var capturedSelection: NSRange?
    private(set) var captureDiagnostic: [String: Any] = [:]
    private(set) var pasteSeconds: TimeInterval = 0
    private(set) var modifierWaitSeconds: TimeInterval = 0
    private(set) var clipboardSaveSeconds: TimeInterval = 0
    private var modifiersReleased: Bool {
        CGEventSource.flagsState(.combinedSessionState).intersection([.maskControl, .maskAlternate, .maskShift, .maskCommand]).isEmpty &&
            ![CGKeyCode(59), 62, 58, 61, 56, 60, 55, 54].contains { CGEventSource.keyState(.hidSystemState, key: $0) }
    }

    func logDestination(context: WritingContext) -> LogDestination {
        var destination = LogDestination(bundleID: context.bundleID, appName: context.appName, windowTitle: context.windowTitle)
        let browsers: Set<String> = ["com.apple.Safari", "com.google.Chrome", "com.google.Chrome.canary",
            "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser", "org.mozilla.firefox"]
        guard browsers.contains(context.bundleID), AXIsProcessTrusted(),
              let app = NSRunningApplication(processIdentifier: pid) ?? NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier == context.bundleID else { return destination }
        let application = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(application, 0.1)
        func host(_ object: AXUIElement, _ attribute: String) -> String? {
            var value: CFTypeRef?
            guard AXUIElementCopyAttributeValue(object, attribute as CFString, &value) == .success else { return nil }
            let url = (value as? URL)?.absoluteString ?? (value as? String)
            return url.flatMap { LogDestination.host(from: $0) }
        }
        var reference: CFTypeRef?
        if AXUIElementCopyAttributeValue(application, kAXFocusedWindowAttribute as CFString, &reference) == .success,
           let reference, CFGetTypeID(reference) == AXUIElementGetTypeID() {
            let window = reference as! AXUIElement; AXUIElementSetMessagingTimeout(window, 0.1)
            destination.siteHost = host(window, kAXDocumentAttribute)
        }
        // Chromium exposes a page URL on the focused field's WebArea ancestor.
        // Walk parents only, with a fixed bound; never search page content or the AX tree.
        if destination.siteHost == nil, let element {
            var node = element
            let deadline = Date().addingTimeInterval(0.15)
            for _ in 0..<8 {
                guard Date() < deadline else { break }
                AXUIElementSetMessagingTimeout(node, 0.1)
                if string(node, kAXRoleAttribute) == "AXWebArea", let site = host(node, "AXURL") {
                    destination.siteHost = site; break
                }
                var parent: CFTypeRef?
                guard AXUIElementCopyAttributeValue(node, kAXParentAttribute as CFString, &parent) == .success,
                      let parent, CFGetTypeID(parent) == AXUIElementGetTypeID() else { break }
                node = parent as! AXUIElement
            }
        }
        return destination
    }

    /// Read only the focused field/window, once per utterance. No AX tree traversal.
    func writingContext(scope: ContextScope, application: NSRunningApplication? = nil) -> WritingContext {
        guard let app = application ?? NSWorkspace.shared.frontmostApplication, !app.isTerminated,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return WritingContext() }
        let basic = WritingContext(bundleID: app.bundleIdentifier ?? "", appName: app.localizedName ?? "")
        guard !contextExcluded else { return basic }
        guard scope != .appOnly, AXIsProcessTrusted() else { return basic }
        let application = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(application, 0.1)
        var reference: CFTypeRef?
        var title = ""
        if AXUIElementCopyAttributeValue(application, kAXFocusedWindowAttribute as CFString, &reference) == .success,
           let reference, CFGetTypeID(reference) == AXUIElementGetTypeID() {
            let window = reference as! AXUIElement; AXUIElementSetMessagingTimeout(window, 0.1)
            title = string(window, kAXTitleAttribute) ?? ""
        }
        guard !PageContext.excluded(title) else { contextExcluded = true; return basic }
        guard let element, pid == app.processIdentifier else {
            return WritingContext(bundleID: basic.bundleID, appName: basic.appName, windowTitle: title)
        }
        let hint = string(element, "AXPlaceholderValue") ?? string(element, kAXDescriptionAttribute) ?? ""
        var around: (String, String, String)?
        if scope == .nearby, let capturedValue, let capturedSelection { around = WritingContext.surrounding(capturedValue, range: capturedSelection) }
        return WritingContext(bundleID: basic.bundleID, appName: basic.appName, windowTitle: title, fieldHint: hint,
                              before: around?.0 ?? "", selected: around?.1 ?? "", after: around?.2 ?? "")
    }

    func capture(application: NSRunningApplication? = nil) -> Bool {
        confirmedContextIdentity = nil
        element = nil; reason = ""; capturedValue = nil; capturedSelection = nil
        contextExcluded = false
        captureDiagnostic = [:]
        guard AXIsProcessTrusted() else { reason = "直接入力にはアクセシビリティの許可が必要です。"; return false }
        guard let app = application ?? NSWorkspace.shared.frontmostApplication, !app.isTerminated,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier else {
            reason = "入力先を選び、ショートカットで開始してください。"; return false
        }
        pid = app.processIdentifier; appName = app.localizedName ?? "入力先"
        captureDiagnostic = ["bundleID": app.bundleIdentifier ?? "", "pid": pid]
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.25)
        let primary = focusedReference(application)
        captureDiagnostic["reportedRole"] = primary.flatMap { string($0, kAXRoleAttribute) } ?? "missing"
        captureDiagnostic["reportedSubrole"] = primary.flatMap { string($0, kAXSubroleAttribute) } ?? ""
        if let primary, string(primary, kAXSubroleAttribute) == kAXSecureTextFieldSubrole {
            contextExcluded = true
            reason = "この入力欄は直接入力に対応していません。字幕からコピーできます。"
            return false
        }
        captureDiagnostic["hasValue"] = primary.flatMap { string($0, kAXValueAttribute) } != nil
        captureDiagnostic["hasSelection"] = primary.flatMap { selection($0) } != nil
        captureDiagnostic["selectionSettable"] = primary.map { selectionSettable($0) } ?? false
        captureDiagnostic["valueSettable"] = primary.map { attributeSettable($0, kAXValueAttribute) } ?? false
        guard let field = focusedField() else {
            reason = primary == nil ? "入力欄が見つかりません。" : "この入力欄は直接入力に対応していません。字幕からコピーできます。"
            return false
        }
        AXUIElementSetMessagingTimeout(field, 0.25)
        captureDiagnostic["fieldRole"] = string(field, kAXRoleAttribute) ?? ""
        capturedValue = string(field, kAXValueAttribute)
        capturedSelection = selection(field)
        if app.processIdentifier != NSWorkspace.shared.frontmostApplication?.processIdentifier {
            guard capturedValue != nil, capturedSelection != nil else {
                reason = "直前の入力欄を確認できません。認識した文章はコピーできます。"; return false
            }
        }
        element = field
        return true
    }
    /// Keep the exact field captured before our window took focus, without recapturing
    /// a background application's window/container as if it were the text field.
    func matchesCapture(application: NSRunningApplication?) -> Bool {
        guard AXIsProcessTrusted(), let application, !application.isTerminated,
              application.processIdentifier == pid, let element,
              let capturedValue, let capturedSelection else { return false }
        return capturedValue == string(element, kAXValueAttribute) && capturedSelection == selection(element)
    }
    func voiceEditText() -> (text: String, wholeDocument: Bool)? {
        let terminals: Set<String> = ["com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "dev.warp.Warp", "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty", "com.github.wez.wezterm"]
        let hint = element.flatMap { string($0, kAXDescriptionAttribute) }?.lowercased() ?? ""
        if terminals.contains(NSRunningApplication(processIdentifier: pid)?.bundleIdentifier ?? "") || hint.contains("terminal") || hint.contains("ターミナル") {
            reason = "ターミナルでは音声編集を使えません。文書の入力欄で試してください。"; return nil
        }
        guard let value = capturedValue, let selection = capturedSelection,
              let draft = try? VoiceEditDraft(text: value, selection: selection) else { return nil }
        return (draft.target, draft.wholeDocument)
    }
    /// Captions stream independently; the target receives one verified paste on stop.
    func finish(_ text: String, replaceWholeDocument: Bool = false, restoreFromOwnApp: Bool = false) async -> Bool {
        confirmedContextIdentity = nil
        let pasteStarted = Date()
        captureDiagnostic["pastePosted"] = false
        captureDiagnostic["receipt"] = "preflight"
        for key in ["receiptValueReadable", "receiptLengthDelta", "receiptSeconds"] {
            captureDiagnostic.removeValue(forKey: key)
        }
        defer { pasteSeconds = Date().timeIntervalSince(pasteStarted) }
        if restoreFromOwnApp && NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier {
            guard !Task.isCancelled, AXIsProcessTrusted(), let element,
                  let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated,
                  capturedValue != nil, capturedSelection != nil,
                  capturedValue == string(element, kAXValueAttribute), capturedSelection == selection(element) else { return suspend() }
            app.activate(options: [])
            for _ in 0..<10 {
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { break }
                try? await Task.sleep(for: .milliseconds(25))
            }
        }
        guard !text.isEmpty, AXIsProcessTrusted(), let element,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, isFocused(element),
              capturedValue == string(element, kAXValueAttribute), capturedSelection == selection(element) else { return suspend() }
        // Allow the hold gesture to be fully released before sending Command-V.
        for _ in 0..<20 {
            if modifiersReleased { break }
            try? await Task.sleep(nanoseconds: 25_000_000)
        }
        modifierWaitSeconds = Date().timeIntervalSince(pasteStarted)
        guard !Task.isCancelled, self.element != nil,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, isFocused(element),
              capturedValue == string(element, kAXValueAttribute), capturedSelection == selection(element),
              modifiersReleased,
              let source = CGEventSource(stateID: .privateState),
              let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false) else { return suspend() }
        let originalSelection = capturedSelection
        var selectedWholeDocument = false
        var postedPaste = false
        defer {
            // If preparation fails, restore only our own unchanged selection.
            if selectedWholeDocument && !postedPaste, let originalSelection,
               capturedValue == string(element, kAXValueAttribute), selection(element) == capturedSelection, isFocused(element) {
                _ = setSelection(element, originalSelection)
                capturedSelection = originalSelection
            }
        }
        if replaceWholeDocument {
            guard let capturedValue, originalSelection != nil else { return suspend() }
            let wholeRange = NSRange(location: 0, length: (capturedValue as NSString).length)
            guard setSelection(element, wholeRange), selection(element) == wholeRange else {
                reason = "この入力欄では文書全体を指定できません。本文を選択してから音声編集を試してください。"
                return false
            }
            capturedSelection = wholeRange; selectedWholeDocument = true
        }
        let pasteboard = NSPasteboard.general
        let clipboardStarted = Date()
        // Preserve all available clipboard formats, and never overwrite a newer copy.
        let saved = (pasteboard.pasteboardItems ?? []).map { item -> NSPasteboardItem in
            let copy = NSPasteboardItem()
            for type in item.types { if let data = item.data(forType: type) { copy.setData(data, forType: type) } }
            return copy
        }
        clipboardSaveSeconds = Date().timeIntervalSince(clipboardStarted)
        pasteboard.clearContents()
        guard pasteboard.setString(text, forType: .string) else {
            if !saved.isEmpty { pasteboard.writeObjects(saved) }
            return suspend()
        }
        let count = pasteboard.changeCount
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard pasteboard.changeCount == count else { return }
            pasteboard.clearContents()
            if !saved.isEmpty { pasteboard.writeObjects(saved) }
        }
        // Clipboard providers can take time. Check the field again after copying
        // those formats so a focus/value change cannot redirect our paste.
        guard !Task.isCancelled, self.element != nil,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, isFocused(element),
              capturedValue == string(element, kAXValueAttribute), capturedSelection == selection(element),
              modifiersReleased, pasteboard.changeCount == count,
              pasteboard.string(forType: .string) == text else { return suspend() }
        // End the synthetic modifier state as well as the V key. Keeping Command
        // on the key-up leaves the session flags pressed until a physical key arrives.
        down.flags = .maskCommand; up.flags = []
        down.setIntegerValueField(.eventSourceUserData, value: Self.pasteEventMarker)
        up.setIntegerValueField(.eventSourceUserData, value: Self.pasteEventMarker)
        down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
        postedPaste = true
        captureDiagnostic["pastePosted"] = true
        self.element = nil
        // Sending Command-V is not proof that the editor accepted it.
        if let capturedValue, let capturedSelection,
           var plan = TextInsertion(original: capturedValue, selection: capturedSelection) {
            plan.commit(text)
            let verificationStarted = Date()
            let result = await PasteReceipt.wait(expected: plan.expected) {
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == self.pid,
                      self.isFocused(element) else { return .targetChanged }
                guard let value = self.string(element, kAXValueAttribute) else {
                    self.captureDiagnostic["receiptValueReadable"] = false
                    return .unavailable
                }
                // Record structure only, never the recognized or target text.
                self.captureDiagnostic["receiptValueReadable"] = true
                self.captureDiagnostic["receiptLengthDelta"] = (value as NSString).length - (plan.expected as NSString).length
                return .value(value)
            }
            captureDiagnostic["receipt"] = result.rawValue
            captureDiagnostic["receiptSeconds"] = Date().timeIntervalSince(verificationStarted)
            if result == .confirmed {
                confirmedContextIdentity = identity(of: element, expected: plan.expected)
                return true
            }
        } else {
            captureDiagnostic["receipt"] = "snapshotUnavailable"
        }
        reason = "転記結果を確認できませんでした。入力欄を確認してください。文字が入っていない場合だけ、最後の入力を貼り付け直せます。"
        return false
    }
    func detach() { element = nil }
    private func suspend() -> Bool {
        reason = "入力欄への反映を確認できないため、直接入力を停止しました。字幕からコピーできます。"
        element = nil; return false
    }
    private func string(_ e: AXUIElement, _ attribute: String) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(e, attribute as CFString, &value) == .success else { return nil }
        return value as? String
    }
    private func setSelection(_ element: AXUIElement, _ range: NSRange) -> Bool {
        var value = CFRange(location: range.location, length: range.length)
        guard let ax = AXValueCreate(.cfRange, &value) else { return false }
        return AXUIElementSetAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, ax) == .success
    }
    private func selection(_ e: AXUIElement) -> NSRange? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(e, kAXSelectedTextRangeAttribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        var range = CFRange()
        guard AXValueGetValue(value as! AXValue, .cfRange, &range) else { return nil }
        return NSRange(location: range.location, length: range.length)
    }
    private func isFocused(_ e: AXUIElement) -> Bool {
        guard let focused = focusedField() else { return false }
        return CFEqual(e, focused)
    }
    private func focusedReference(_ object: AXUIElement) -> AXUIElement? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(object, kAXFocusedUIElementAttribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
    private func selectionSettable(_ field: AXUIElement) -> Bool {
        attributeSettable(field, kAXSelectedTextRangeAttribute)
    }
    private func attributeSettable(_ field: AXUIElement, _ attribute: String) -> Bool {
        var settable = DarwinBoolean(false)
        return AXUIElementIsAttributeSettable(field, attribute as CFString, &settable) == .success && settable.boolValue
    }
    private func isTextField(_ field: AXUIElement) -> Bool {
        guard string(field, kAXSubroleAttribute) != kAXSecureTextFieldSubrole,
              let role = string(field, kAXRoleAttribute) else { return false }
        if [kAXTextAreaRole, kAXTextFieldRole, kAXComboBoxRole].contains(role) { return true }
        // Some custom editors expose their actual editable field as AXGroup.
        // Require text plus a valid, writable text selection; never accept a
        // container/web page solely because Command-V could be sent to it.
        guard role == kAXGroupRole, attributeSettable(field, kAXValueAttribute), let value = string(field, kAXValueAttribute),
              let range = selection(field), selectionSettable(field),
              range.location >= 0, range.length >= 0 else { return false }
        let length = (value as NSString).length
        return range.location <= length && range.length <= length - range.location
    }
    private func focusedField() -> AXUIElement? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.25)
        var references: [AXUIElement] = []
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid {
            let system = AXUIElementCreateSystemWide()
            AXUIElementSetMessagingTimeout(system, 0.25)
            if let field = focusedReference(system) {
                var fieldPID: pid_t = 0
                guard AXUIElementGetPid(field, &fieldPID) == .success, fieldPID == pid else { return nil }
                references.append(field)
            }
        }
        if let field = focusedReference(app) { references.append(field) }
        // Follow only explicit focus references, not the document's AX tree.
        for reference in references {
            var field = reference
            for _ in 0..<3 {
                var fieldPID: pid_t = 0
                AXUIElementSetMessagingTimeout(field, 0.25)
                guard AXUIElementGetPid(field, &fieldPID) == .success, fieldPID == pid else { break }
                if isTextField(field) { return field }
                guard let child = focusedReference(field), !CFEqual(field, child) else { break }
                field = child
            }
        }
        return nil
    }
}
