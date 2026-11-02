import AppKit
import KoeFlowCore

/// Uses device modifier bits so right Control cannot trigger left Control.
@MainActor final class ModifierHold {
    var selectedKey: (() -> String)?
    var onPress: ((UInt16) -> Void)?
    var onRelease: ((UInt16) -> Void)?
    var onInteraction: ((_ external: Bool) -> Void)?
    var continuousShortcut: (() -> ContinuousShortcut)?
    var voiceEditShortcut: (() -> VoiceEditShortcut)?
    var onContinuous: ((_ promotingHold: Bool) -> Void)?
    var continuousActive: (() -> Bool)?
    var mainHoldEnabled: (() -> Bool)?
    var onDiagnostic: (([String: Any]) -> Void)?
    var windowShortcut: (() -> WindowShortcut)?
    var inputActive: (() -> Bool)?
    var onWindowToggle: (() -> Void)?
    var mainWindowMode: (() -> WindowInputMode)?
    var onMainSingle: (() -> Void)?
    var onMainCommit: (() -> Void)?
    private var global: Any?
    private var local: Any?
    private var pending: Task<Void, Never>?
    private var releaseCheck: Task<Void, Never>?
    private var mainPoll: Task<Void, Never>?
    private var mainPollGeneration = 0
    private var mainContextCode: UInt16?
    private var mainGesture = WindowInputGesture()
    private var activeKey: String?
    private var keyDown: (UInt16) -> Bool = { CGEventSource.keyState(.hidSystemState, key: $0) }
    private var mouseDown: () -> Bool = {
        CGEventSource.buttonState(.hidSystemState, button: .left) ||
        CGEventSource.buttonState(.hidSystemState, button: .right)
    }
    private var clock: () -> Double = { ProcessInfo.processInfo.systemUptime }
    private var gesture = HoldGesture()
    private var chord = ChordGesture()
    private var windowTap = WindowTapGesture()
    private var previousWindowShortcut: WindowShortcut = .none
    func install() {
        let events: NSEvent.EventTypeMask = [.flagsChanged, .keyDown, .leftMouseDown, .rightMouseDown]
        global = NSEvent.addGlobalMonitorForEvents(matching: events) { [weak self] event in
            Task { @MainActor [weak self] in self?.handle(event, external: true) }
        }
        local = NSEvent.addLocalMonitorForEvents(matching: events) { [weak self] event in
            self?.handle(event, external: false); return event
        }
    }
    func restartMonitoring() {
        if let global { NSEvent.removeMonitor(global) }
        if let local { NSEvent.removeMonitor(local) }
        global = nil; local = nil
        install()
    }
    private func handle(_ event: NSEvent, external: Bool) {
        // A verified automatic paste must not cancel itself during receipt checking.
        if event.cgEvent?.getIntegerValueField(.eventSourceUserData) == TextTarget.pasteEventMarker { return }
        let shortcut = continuousShortcut?() ?? .controlShift
        let modifiers = event.modifierFlags.intersection([.control, .option, .command, .shift])
        diagnose(event.type == .flagsChanged ? "modifier" : "interaction", event: event)
        guard event.type == .flagsChanged else {
            let isToggleShortcut = event.type == .keyDown && event.keyCode == 49 &&
                (modifiers == [.control, .option] || (shortcut == .controlShiftSpace && modifiers == [.control, .shift]))
            let edit = voiceEditShortcut?() ?? .none
            let isEditShortcut = event.type == .keyDown && event.keyCode == 14 &&
                ((edit == .controlOptionE && modifiers == [.control, .option]) ||
                 (edit == .optionShiftE && modifiers == [.option, .shift]))
            chord.interaction()
            windowTap.reset()
            mainGesture.interaction()
            scheduleMainPolling()
            if !isToggleShortcut && !isEditShortcut { onInteraction?(external) }
            apply(gesture.interaction()); return
        }
        let chordMask: UInt8 = (modifiers.contains(.control) ? 1 : 0) |
            (modifiers.contains(.shift) ? 2 : 0) | (modifiers.contains(.option) ? 4 : 0) |
            (modifiers.contains(.command) ? 8 : 0)
        let windowKey = windowShortcut?() ?? .none
        if windowKey != previousWindowShortcut { windowTap.reset(); previousWindowShortcut = windowKey }
        let windowCode = windowKey.keyCode
        let windowMask: UInt64 = windowCode == 59 ? 1 : 0x20
        let windowDown = (event.cgEvent?.flags.rawValue ?? 0) & windowMask != 0
        let windowFlags: NSEvent.ModifierFlags = windowCode == 59 ? .control : .option
        let physicalRelease = windowCode.map { !keyDown($0) } ?? false
        let mainMode = mainWindowMode?() ?? .inactive
        let mainRoute = mainMode != .inactive && gesture.state != .recording
        let mainCode = selectedCode()
        let mainOwnsWindowKey = mainRoute && mainCode != nil && mainCode == windowCode
        if mainOwnsWindowKey { windowTap.reset() }
        if !mainOwnsWindowKey && windowTap.update(isTarget: event.keyCode == windowCode,
            targetDown: windowDown, isolated: windowDown ? modifiers == windowFlags : modifiers.isEmpty && physicalRelease,
            time: event.timestamp, busy: gesture.state == .recording) {
            pending?.cancel(); pending = nil
            releaseCheck?.cancel(); releaseCheck = nil; activeKey = nil
            _ = gesture.reset(); chord = ChordGesture()
            resetMainGesture(blockUntilRelease: true)
            if mainRoute { diagnose("mainCommitWindowShortcut"); onMainCommit?() }
            else { diagnose("windowToggle"); onWindowToggle?() }
            return
        }
        if chord.update(modifiers: chordMask, chord: shortcut.modifierMask) {
            let promotingHold = gesture.state == .recording
            pending?.cancel(); pending = nil
            releaseCheck?.cancel(); releaseCheck = nil; activeKey = nil
            _ = gesture.reset() // A slow chord may promote an already-started hold session.
            resetMainGesture(blockUntilRelease: true)
            onContinuous?(promotingHold); return
        }
        if chord.armed {
            pending?.cancel(); pending = nil
            mainGesture.interaction()
            scheduleMainPolling()
            apply(gesture.interaction()); return
        }
        if mainRoute {
            pending?.cancel(); pending = nil
            releaseCheck?.cancel(); releaseCheck = nil; activeKey = nil
            _ = gesture.reset()
            sampleMainInput(event: event)
            scheduleMainPolling()
            return
        }
        discardMainContext()
        if continuousActive?() == true && mainHoldEnabled?() != true {
            pending?.cancel(); pending = nil
            releaseCheck?.cancel(); releaseCheck = nil; activeKey = nil
            _ = gesture.reset(); return
        }
        let key = activeKey ?? selectedKey?() ?? "none"
        guard key != "none" else { apply(gesture.reset()); return }
        let code: UInt16 = key == "leftControl" ? 59 : 58
        let mask: UInt64 = key == "leftControl" ? 0x1 : 0x20
        let allowed: NSEvent.ModifierFlags = key == "leftControl" ? .control : .option
        let isDown = (event.cgEvent?.flags.rawValue ?? 0) & mask != 0
        if event.keyCode == code && isDown { releaseCheck?.cancel(); releaseCheck = nil }
        apply(gesture.modifierChanged(isTarget: event.keyCode == code, targetDown: isDown, onlyTarget: modifiers == allowed))
    }

    private func selectedCode() -> UInt16? {
        switch selectedKey?() ?? "none" {
        case "leftControl": return 59
        case "leftOption": return 58
        default: return nil
        }
    }

    private func cancelMainPolling() {
        mainPollGeneration += 1
        mainPoll?.cancel(); mainPoll = nil
    }

    private func discardMainContext() {
        cancelMainPolling()
        mainContextCode = nil
        mainGesture.reset()
    }

    /// Reset on a new session or window transition, not on its single-tap stop.
    func resetMainGesture(blockUntilRelease: Bool = false) {
        cancelMainPolling()
        mainContextCode = selectedCode()
        mainGesture.reset(blockUntilUp: blockUntilRelease)
        scheduleMainPolling()
    }

    private func sampleMainInput(event: NSEvent? = nil) {
        let mode = mainWindowMode?() ?? .inactive
        guard mode != .inactive, gesture.state != .recording, let code = selectedCode() else {
            discardMainContext(); return
        }
        let down = keyDown(code)
        if mainContextCode != code {
            cancelMainPolling()
            let mask: UInt64 = code == 59 ? 1 : 0x20
            let freshPress = event?.type == .flagsChanged && event?.keyCode == code &&
                (event?.cgEvent?.flags.rawValue ?? 0) & mask != 0
            mainGesture.reset(blockUntilUp: down && !freshPress)
            mainContextCode = code
        }
        let otherModifiers: [UInt16] = [54, 55, 56, 57, 58, 59, 60, 61, 62, 63]
        let isolated = !mouseDown() && !otherModifiers.contains { $0 != code && keyDown($0) }
        let action = mainGesture.update(targetDown: down, isolated: isolated, time: clock(),
            canToggle: mode == .idle || mode == .recording, recording: mode == .recording)
        switch action {
        case .none: break
        case .singleTap: diagnose("mainSingle"); onMainSingle?()
        case .doubleTap: diagnose("mainCommit"); onMainCommit?()
        }
    }

    private func scheduleMainPolling() {
        guard mainWindowMode?() != nil, mainWindowMode?() != .inactive,
              gesture.state != .recording, selectedCode() != nil,
              mainGesture.needsPolling else {
            cancelMainPolling(); return
        }
        guard mainPoll == nil else { return }
        let generation = mainPollGeneration
        mainPoll = Task { [weak self] in
            defer {
                if let self, self.mainPollGeneration == generation { self.mainPoll = nil }
            }
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(25))
                guard !Task.isCancelled, let self, self.mainPollGeneration == generation else { return }
                self.sampleMainInput()
                guard self.mainGesture.needsPolling else { return }
            }
        }
    }
    private func apply(_ action: HoldGesture.Action) {
        switch action {
        case .none: break
        case .arm:
            let key = selectedKey?()
            activeKey = key
            pending = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 230_000_000)
                guard !Task.isCancelled, let self, self.selectedKey?() == key else { return }
                self.pending = nil
                let code: UInt16 = key == "leftControl" ? 59 : 58
                guard self.keyDown(code) else {
                    self.apply(self.gesture.reset()); return
                }
                self.apply(self.gesture.holdElapsed())
            }
        case .cancelPending: pending?.cancel(); pending = nil; activeKey = nil
        case .start:
            diagnose("start")
            onPress?(activeKey == "leftControl" ? 59 : 58)
        case .confirmRelease:
            releaseCheck?.cancel()
            let key = activeKey
            releaseCheck = Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(70))
                guard !Task.isCancelled, let self, self.activeKey == key else { return }
                self.releaseCheck = nil
                let down = self.keyDown(key == "leftControl" ? 59 : 58)
                self.diagnose(down ? "releaseIgnoredKeyStillDown" : "releaseConfirmed")
                self.apply(self.gesture.releaseConfirmed(targetDown: down))
            }
        case .stop:
            pending?.cancel(); pending = nil
            let code: UInt16 = activeKey == "leftControl" ? 59 : 58
            releaseCheck?.cancel(); releaseCheck = nil; activeKey = nil
            onRelease?(code)
        }
    }
    private func diagnose(_ kind: String, event: NSEvent? = nil) {
        guard let onDiagnostic else { return }
        guard gesture.state != .idle || kind != "interaction" else { return }
        // Bounded, opt-in diagnostics never retain typed characters or audio/text.
        var entry: [String: Any] = ["time": Date().timeIntervalSince1970, "kind": kind,
            "gesture": String(describing: gesture.state), "key": activeKey ?? selectedKey?() ?? "none",
            "leftControlDown": keyDown(59), "leftOptionDown": keyDown(58)]
        if let event {
            entry["modifierFlags"] = event.modifierFlags.rawValue
            if event.type == .flagsChanged { entry["modifierKeyCode"] = event.keyCode }
        }
        onDiagnostic(entry)
    }
    /// Runs real monitor/timer logic with in-process events; never posts keyboard input.
    static func releaseSelfTest() async -> [String: Any] {
        var rows: [[String: Any]] = []
        func event(_ code: UInt16, _ flags: UInt, type: NSEvent.EventType = .flagsChanged, time: Double = 0) -> NSEvent {
            NSEvent.keyEvent(with: type, location: .zero, modifierFlags: .init(rawValue: flags), timestamp: time,
                windowNumber: 0, context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: code)!
        }
        for key in ["leftControl", "leftOption"] {
            let code: UInt16 = key == "leftControl" ? 59 : 58
            let flag = key == "leftControl" ? NSEvent.ModifierFlags.control.rawValue | 1 : NSEvent.ModifierFlags.option.rawValue | 0x20
            let hold = ModifierHold()
            var physicallyDown = true, starts = 0, stops = 0, interactions = 0
            var mode: WindowInputMode = .inactive
            var mainActions = 0
            hold.selectedKey = { key }; hold.continuousShortcut = { .none }
            hold.keyDown = { $0 == code && physicallyDown }
            hold.mainWindowMode = { mode }
            hold.onMainSingle = { mainActions += 1 }; hold.onMainCommit = { mainActions += 1 }
            hold.onPress = { _ in starts += 1; mode = .recording }; hold.onRelease = { _ in stops += 1 }
            hold.onInteraction = { _ in interactions += 1 }
            hold.handle(event(code, flag), external: true)
            try? await Task.sleep(for: .milliseconds(270))
            // A cleared event flag while the physical key remains down cannot commit.
            hold.handle(event(code, 0), external: true)
            try? await Task.sleep(for: .milliseconds(100))
            hold.handle(event(0, flag, type: .keyDown), external: true)
            hold.handle(event(60, flag), external: true)
            let protected = starts == 1 && stops == 0 && interactions == 1
            physicallyDown = false
            hold.handle(event(code, 0), external: true)
            try? await Task.sleep(for: .milliseconds(100))
            hold.handle(event(code, 0), external: true)
            rows.append(["key": key, "falseReleaseAndTypingProtected": protected,
                "starts": starts, "stops": stops, "passed": protected && stops == 1 && mainActions == 0])
        }
        let hold = ModifierHold()
        var starts = 0, stops = 0
        hold.selectedKey = { "leftControl" }; hold.keyDown = { _ in false }
        hold.onPress = { _ in starts += 1 }; hold.onRelease = { _ in stops += 1 }
        hold.handle(event(59, NSEvent.ModifierFlags.control.rawValue | 1), external: true)
        try? await Task.sleep(for: .milliseconds(270))
        rows.append(["key": "quickReleaseBeforeStart", "passed": starts == 0 && stops == 0])
        let mainHold = ModifierHold()
        var mainDown = false, busy = false, mainStarts = 0, mainStops = 0, commits = 0, toggles = 0
        var mainMode: WindowInputMode = .idle
        let control = NSEvent.ModifierFlags.control.rawValue | 1
        mainHold.selectedKey = { "leftControl" }; mainHold.continuousShortcut = { .none }
        mainHold.windowShortcut = { .controlDoubleTap }; mainHold.inputActive = { busy }
        mainHold.keyDown = { $0 == 59 && mainDown }
        mainHold.mouseDown = { false }
        mainHold.mainWindowMode = { mainMode }
        mainHold.onMainSingle = { [weak mainHold] in
            if mainMode == .recording { mainStops += 1; mainMode = .finishing }
            else {
                mainStarts += 1; mainMode = .recording
                mainHold?.resetMainGesture(blockUntilRelease: true)
            }
        }
        mainHold.onMainCommit = { [weak mainHold] in
            commits += 1
            mainHold?.resetMainGesture(blockUntilRelease: true)
        }
        mainHold.onWindowToggle = { toggles += 1 }
        func mainTap() async {
            mainDown = true
            mainHold.handle(event(59, control, time: ProcessInfo.processInfo.systemUptime), external: false)
            try? await Task.sleep(for: .milliseconds(50))
            mainDown = false
            mainHold.handle(event(59, 0, time: ProcessInfo.processInfo.systemUptime), external: false)
            try? await Task.sleep(for: .milliseconds(100))
        }
        await mainTap(); await mainTap()
        let doubleWithoutStart = commits == 1 && mainStarts == 0 && toggles == 0
        try? await Task.sleep(for: .milliseconds(100))
        mainHold.resetMainGesture()
        await mainTap()
        try? await Task.sleep(for: .milliseconds(350))
        let singleStarts = mainStarts == 1 && mainMode == .recording
        try? await Task.sleep(for: .milliseconds(150))
        await mainTap()
        let singleStops = mainStops == 1 && mainMode == .finishing
        await mainTap()
        let stopThenCommit = commits == 2 && mainStops == 1
        try? await Task.sleep(for: .milliseconds(100))
        mainHold.resetMainGesture()
        await mainTap(); await mainTap()
        let freshBusyCommit = commits == 3 && mainStarts == 1 && mainStops == 1
        rows.append(["key": "mainWindowSingleDouble", "doubleWithoutStart": doubleWithoutStart,
            "singleStarts": singleStarts, "singleStops": singleStops, "stopThenCommit": stopThenCommit,
            "freshBusyCommit": freshBusyCommit, "starts": mainStarts, "stops": mainStops, "commits": commits,
            "passed": doubleWithoutStart && singleStarts && singleStops && stopThenCommit && freshBusyCommit])
        mainMode = .inactive; busy = true
        mainHold.resetMainGesture()
        await mainTap(); await mainTap()
        rows.append(["key": "hiddenBusyWindowReveal", "windowToggles": toggles,
            "passed": toggles == 1 && mainStarts == 1 && mainStops == 1 && commits == 3])

        let separate = ModifierHold()
        var optionDown = false, separateCommits = 0, separateSingles = 0
        let option = NSEvent.ModifierFlags.option.rawValue | 0x20
        separate.selectedKey = { "leftControl" }; separate.continuousShortcut = { .none }
        separate.windowShortcut = { .optionDoubleTap }; separate.mainWindowMode = { .idle }
        separate.keyDown = { $0 == 58 && optionDown }; separate.mouseDown = { false }
        separate.onMainCommit = { separateCommits += 1 }; separate.onMainSingle = { separateSingles += 1 }
        for _ in 0..<2 {
            optionDown = true
            separate.handle(event(58, option, time: ProcessInfo.processInfo.systemUptime), external: false)
            try? await Task.sleep(for: .milliseconds(50))
            optionDown = false
            separate.handle(event(58, 0, time: ProcessInfo.processInfo.systemUptime), external: false)
            try? await Task.sleep(for: .milliseconds(100))
        }
        rows.append(["key": "separateWindowShortcutCommit", "passed": separateCommits == 1 && separateSingles == 0])
        return ["passed": rows.allSatisfy { $0["passed"] as? Bool == true }, "cases": rows]
    }
    deinit {
        pending?.cancel()
        releaseCheck?.cancel()
        mainPoll?.cancel()
        if let global { NSEvent.removeMonitor(global) }
        if let local { NSEvent.removeMonitor(local) }
    }
}
