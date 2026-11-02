import AppKit
import AVFoundation
import SwiftUI
import WebKit
import PDFKit
import KoeFlowCore
import Combine
import Carbon

final class CaptionPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = FlowModel()
    let hotkey = Hotkey()
    let modifierHold = ModifierHold()
    private let continuousHotkey = Hotkey()
    private var shortcutSubscription: AnyCancellable?
    private var continuousMenuItem: NSMenuItem?
    private let retryHotkey = Hotkey()
    private let voiceEditHotkey = Hotkey()
    private var voiceEditSubscription: AnyCancellable?
    private var lastExternalApp: NSRunningApplication?
    private var mainHoldStopKey: UInt16?
    private var workspaceObserver: NSObjectProtocol?
    private var retrying = false
    private var statusItem: NSStatusItem?
    private var window: NSWindow?
    private var setupWindow: NSWindow?
    private lazy var setup: SetupModel = {
        SetupModel(settings: model.settings, fixture: DevelopmentChecks.setupFixture)
    }()
    private var overlay: NSPanel?
    private var overlayAutoHide: Task<Void, Never>?
    private var documentWindows: [String: NSWindow] = [:]
    var openSettingsOnLaunch = false
    var diagnosticsURL: URL?
    private let diagnosticsQueue = DispatchQueue(label: "jp.ys.koekichi.flow.diagnostics", qos: .utility)
    private var inputControlEvents: [[String: Any]] = []
    var fixtureOnly = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "信楽")
        let quitItem = NSMenuItem(title: "信楽を終了", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self; appMenu.addItem(quitItem)
        appItem.submenu = appMenu; mainMenu.addItem(appItem)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "編集")
        editMenu.addItem(withTitle: "取り消す", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "切り取り", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼り付け", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべて選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu; mainMenu.addItem(editItem)
        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "ウィンドウ")
        let closeItem = NSMenuItem(title: "設定画面を閉じる", action: #selector(closeWindow), keyEquivalent: "w")
        closeItem.target = self; windowMenu.addItem(closeItem)
        windowItem.submenu = windowMenu; mainMenu.addItem(windowItem)
        let helpItem = NSMenuItem()
        let helpMenu = NSMenu(title: "ヘルプ")
        helpMenu.addItem(withTitle: SetupCopy.guide, action: #selector(openSetup), keyEquivalent: "")
        helpMenu.addItem(withTitle: SetupCopy.installGuide, action: #selector(openInstallGuide), keyEquivalent: "")
        helpMenu.addItem(withTitle: "操作マニュアル", action: #selector(openManual), keyEquivalent: "?")
        helpMenu.addItem(withTitle: "操作早見表", action: #selector(openQuickGuide), keyEquivalent: "")
        helpMenu.addItem(withTitle: "文章整形・アプリ別設定", action: #selector(openWritingGuide), keyEquivalent: "")
        helpMenu.addItem(withTitle: "連続入力・プロンプトカード", action: #selector(openContinuousGuide), keyEquivalent: "")
        helpMenu.addItem(withTitle: "地名辞書", action: #selector(openGeographyGuide), keyEquivalent: "")
        helpMenu.addItem(withTitle: "音声編集", action: #selector(openVoiceEditGuide), keyEquivalent: "")
        for entry in helpMenu.items { entry.target = self }
        helpItem.submenu = helpMenu; mainMenu.addItem(helpItem)
        NSApp.mainMenu = mainMenu
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "信楽")
        statusItem = item
        let menu = NSMenu()
        menu.addItem(withTitle: "設定・辞書・全文を開く", action: #selector(openWindow), keyEquivalent: "")
        menu.addItem(withTitle: SetupCopy.guide, action: #selector(openSetup), keyEquivalent: "")
        menu.addItem(withTitle: "操作マニュアル", action: #selector(openManual), keyEquivalent: "")
        menu.addItem(withTitle: "操作早見表", action: #selector(openQuickGuide), keyEquivalent: "")
        menu.addItem(withTitle: "文章整形・アプリ別設定", action: #selector(openWritingGuide), keyEquivalent: "")
        menu.addItem(withTitle: "連続入力・プロンプトカード", action: #selector(openContinuousGuide), keyEquivalent: "")
        menu.addItem(withTitle: "地名辞書", action: #selector(openGeographyGuide), keyEquivalent: "")
        menu.addItem(withTitle: "音声編集", action: #selector(openVoiceEditGuide), keyEquivalent: "")
        menu.addItem(withTitle: "字幕を開始・停止  ⌃⌥Space", action: #selector(toggle), keyEquivalent: "")
        let continuousItem = NSMenuItem(title: "連続入力を開始・停止  " + model.settings.continuousShortcut.symbol,
            action: #selector(toggleContinuous), keyEquivalent: "")
        menu.addItem(continuousItem); continuousMenuItem = continuousItem
        menu.addItem(withTitle: "最後の入力を貼り付け  ⌃⌥V", action: #selector(pasteLastInput), keyEquivalent: "")
        menu.addItem(withTitle: "最後の入力を再編集", action: #selector(reeditLastInput), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "字幕を閉じる", action: #selector(closeOverlay), keyEquivalent: "")
        menu.addItem(withTitle: "信楽を終了", action: #selector(quit), keyEquivalent: "q")
        for entry in menu.items { entry.target = self }
        item.menu = menu
        hotkey.onPress = { [weak self] in
            guard let self else { return }
            if self.model.settings.holdToTalk { self.model.start() } else { self.model.toggle() }
        }
        hotkey.onRelease = { [weak self] in
            guard let self, self.model.settings.holdToTalk, !self.model.continuous, !self.model.voiceEditing else { return }
            if self.model.phase == .preparing { self.model.cancel() }
            else if self.model.phase == .listening { Task { await self.model.stop(reason: "hotkeyRelease") } }
        }
        if model.demoFile == nil && !hotkey.register() { model.status = "⌃⌥Spaceを登録できませんでした。別のアプリのショートカットを確認してください。" }
        modifierHold.selectedKey = { [weak self] in
            guard let self else { return "none" }
            if self.setupWindow?.isVisible == true { return "none" }
            return self.model.voiceEditing && self.model.isBusy ? "none" : self.model.settings.holdModifier
        }
        modifierHold.onInteraction = { [weak self] external in
            self?.mainHoldStopKey = nil
            if external { self?.model.suspendDirectInput() }
        }
        modifierHold.onPress = { [weak self] in self?.modifierPressed($0) }
        modifierHold.onRelease = { [weak self] in self?.modifierReleased($0) }
        voiceEditHotkey.onPress = { [weak self] in self?.model.start(voiceEdit: true) }
        voiceEditHotkey.onRelease = { [weak self] in
            guard let self, self.model.voiceEditing else { return }
            if self.model.phase == .preparing { self.model.cancel() }
            else if self.model.phase == .listening { Task { await self.model.stop(reason: "voiceEditRelease") } }
        }
        modifierHold.voiceEditShortcut = { [weak self] in self?.model.settings.voiceEditShortcut ?? .none }
        configureVoiceEditShortcut(model.settings.voiceEditShortcut)
        voiceEditSubscription = model.settings.$voiceEditShortcut.dropFirst().sink { [weak self] in self?.configureVoiceEditShortcut($0) }
        modifierHold.continuousShortcut = { [weak self] in self?.model.settings.continuousShortcut ?? .none }
        modifierHold.continuousActive = { [weak self] in self?.model.continuous == true && self?.model.isBusy == true }
        modifierHold.mainHoldEnabled = { [weak self] in self?.mainWindowPresented == true && self?.model.voiceEditing == false }
        modifierHold.mainWindowMode = { [weak self] in
            guard let self, self.mainWindowPresented, self.setupWindow?.isVisible != true,
                  !self.model.voiceEditing, !self.model.editingWorkspace else { return .inactive }
            if self.model.phase == .idle && !self.model.isRetrying { return .idle }
            if self.model.phase == .listening { return .recording }
            return .finishing
        }
        modifierHold.onMainSingle = { [weak self] in
            guard let self else { return }
            if self.model.phase == .idle { self.model.start(continuous: true) }
            else if self.model.phase == .listening { Task { await self.model.stop(reason: "mainSingle") } }
        }
        modifierHold.onMainCommit = { [weak self] in self?.model.requestWindowCommit() }
        modifierHold.onContinuous = { [weak self] in self?.continuousModifierReleased(promotingHold: $0) }
        modifierHold.windowShortcut = { [weak self] in self?.model.settings.windowShortcut ?? .none }
        modifierHold.inputActive = { [weak self] in self?.model.isBusy == true || self?.setupWindow?.isVisible == true }
        modifierHold.onWindowToggle = { [weak self] in self?.toggleMainWindow() }
        if diagnosticsURL != nil {
            modifierHold.onDiagnostic = { [weak self] event in
                guard let self else { return }
                self.inputControlEvents.append(event)
                if self.inputControlEvents.count > 40 { self.inputControlEvents.removeFirst() }
                self.writeDiagnostics()
            }
        }
        continuousHotkey.onPress = { [weak self] in self?.toggleContinuous() }
        configureContinuousShortcut(model.settings.continuousShortcut)
        retryHotkey.onPress = { [weak self] in self?.pasteLastInput() }
        if model.demoFile == nil && !retryHotkey.register(keyCode: 9) {
            model.status = "再貼り付けのショートカットを登録できませんでした。画面のボタンを使ってください。"
        }
        if let app = NSWorkspace.shared.frontmostApplication,
           app.processIdentifier != ProcessInfo.processInfo.processIdentifier { lastExternalApp = app }
        workspaceObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
            object: nil, queue: .main) { [weak self] notification in
                guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                      app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
                Task { @MainActor [weak self] in
                    self?.lastExternalApp = app
                    self?.writeDiagnostics()
                }
            }
        shortcutSubscription = model.settings.$continuousShortcut.dropFirst().sink { [weak self] value in
            self?.configureContinuousShortcut(value)
        }
        if model.demoFile == nil { modifierHold.install() }
        model.showOverlay = { [weak self] in self?.showOverlay() }
        model.openSettings = { [weak self] in self?.openWindow() }
        model.windowInputApp = { [weak self] in self?.lastExternalApp }
        model.openManual = { [weak self] in self?.openManual() }
        model.openQuickGuide = { [weak self] in self?.openQuickGuide() }
        model.openSetup = { [weak self] in self?.openSetup() }
        model.setupBlocksInput = { [weak self] in
            guard let self else { return false }
            if self.setupWindow?.isVisible == true || !self.setup.ready {
                self.openSetup(); return true
            }
            return false
        }
        model.hideOverlay = { [weak self] in self?.closeOverlay() }
        model.onStateChange = { [weak self] in self?.updateStatus() }
        model.showRecovery = { [weak self] in
            guard let self else { return }
            // A missing AX confirmation can occur even when the paste succeeded.
            // Keep the destination undisturbed; open the full text only on request.
            self.model.showDictationRequest = UUID()
            self.showOverlay(); self.updateStatus()
        }
        model.retryLastInput = { [weak self] in self?.pasteLastInput() }
        model.closeForCommit = { [weak self] in self?.window?.orderOut(nil); self?.closeOverlay() }
        model.showDraftWindow = { [weak self] in
            guard let self else { return }
            self.presentWindow(activate: false)
            NSApp.activate(ignoringOtherApps: true); self.window?.makeKeyAndOrderFront(nil)
        }
        // Remain in the menu bar while idle; the caption appears on speech start.
        writeDiagnostics()
        if DevelopmentChecks.setupFixture != nil ||
           (model.demoFile == nil && !fixtureOnly && (!setup.completed || setup.readOnlyLocation ||
               setup.microphone != .authorized || model.settings.liveInput && !setup.accessibility)) {
            openSetup()
        } else if model.demoFile == nil && !fixtureOnly {
            Task {
                await setup.refreshAssets()
                if !setup.ready && !setup.preparationCancelled { openSetup() }
                else if openSettingsOnLaunch { openWindow() }
            }
        } else if openSettingsOnLaunch { openWindow() }
        if ProcessInfo.processInfo.arguments.contains("--recovery-ui-fixture") {
            model.prepareRecoveryFixture(); model.showRecovery?()
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            if !fixtureOnly && setup.needsPreparation { openSetup() }
            else { openWindow() }
        }
        return true
    }
    func applicationDidBecomeActive(_ notification: Notification) {
        model.settings.refreshLoginStatus(); model.refreshPermission()
        setup.refreshPermissions()
    }
    private func writeDiagnostics() {
        guard let diagnosticsURL else { return }
        var data: [String: Any] = ["accessibility": AXIsProcessTrusted(),
            "microphone": AVCaptureDevice.authorizationStatus(for: .audio).rawValue,
            "phase": String(describing: model.phase), "direct": model.direct,
            "target": model.targetName, "status": model.status,
            "settingsVisible": window?.isVisible ?? false,
            "overlayVisible": overlay?.isVisible ?? false,
            "documentsVisible": documentWindows.filter { $0.value.isVisible }.keys.sorted(),
            "loginItemStatus": model.settings.loginStatus.rawValue,
            "bundle": Bundle.main.bundleIdentifier ?? "none"]
        data["voiceEditing"] = model.voiceEditing
        data["setupVisible"] = setupWindow?.isVisible ?? false
        data["setupCompleted"] = setup.completed
        data["setupReady"] = setup.ready
        data["setupAssets"] = setup.assets.rawValue
        data["setupReadOnlyLocation"] = setup.readOnlyLocation
        data["voiceEditShortcut"] = model.settings.voiceEditShortcut.rawValue
        data["continuous"] = model.continuous
        data["continuousShortcut"] = model.settings.continuousShortcut.rawValue
        data["inputFailed"] = model.inputFailed
        data["inputTarget"] = model.targetDiagnostic
        data["frontmostBundle"] = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
        data["lastInputLength"] = model.lastInput.count
        data["currentInputLength"] = model.text.count
        data["recognitionEvents"] = model.engine.eventCount
        data["maximumRMS"] = model.engine.maximumRMS
        data["audibleInputSeconds"] = model.engine.audibleInputSeconds
        data["inputChannels"] = model.engine.inputChannelCount
        data["elapsedSeconds"] = model.elapsed
        data["recognitionLogEnabled"] = model.settings.recognitionLogEnabled
        data["finalizationSeconds"] = model.engine.finalizationSeconds
        data["commitSeconds"] = model.lastCommitSeconds
        data["inputTiming"] = model.inputTiming
        data["holdModifier"] = model.settings.holdModifier
        data["windowShortcut"] = model.settings.windowShortcut.rawValue
        data["lastStopReason"] = model.lastStopReason
        data["inputControlEvents"] = inputControlEvents
        data["writingPurpose"] = model.writingPurpose.rawValue
        data["writingOutcome"] = model.writingOutcome
        data["writingSeconds"] = model.writingSeconds
        if let frame = window?.frame {
            data["windowFrame"] = ["x": frame.minX, "y": frame.minY, "width": frame.width, "height": frame.height]
        }
        if let frame = window?.screen?.frame {
            data["screenFrame"] = ["x": frame.minX, "y": frame.minY, "width": frame.width, "height": frame.height]
        }
        data["mainWindowOpaque"] = window?.isOpaque ?? false
        data["mainWindowMovable"] = window?.isMovable ?? false
        data["mainWindowLevel"] = window?.level.rawValue ?? 0
        data["overlayWindowLevel"] = overlay?.level.rawValue ?? 0
        data["overlayWindowOpaque"] = overlay?.isOpaque ?? false
        if let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.prettyPrinted, .sortedKeys]) {
            // Developer diagnostics may target a slow or permission-gated volume.
            // Keep that optional output off the app's main thread, in write order.
            diagnosticsQueue.async { try? bytes.write(to: diagnosticsURL, options: .atomic) }
        }
    }
    @objc func openWindow() {
        if setupWindow?.isVisible == true || setup.installing { openSetup(); return }
        presentWindow(activate: true)
    }
    private func modifierPressed(_ code: UInt16) {
        mainHoldStopKey = nil
        if mainWindowPresented && !model.voiceEditing {
            if model.phase == .idle { model.start(continuous: true) }
            else if model.continuous && (model.phase == .listening || model.phase == .preparing) {
                // Commit on release so Control cannot modify the automatic paste.
                mainHoldStopKey = code
            }
        } else { model.start(heldKey: code) }
    }
    private func modifierReleased(_ code: UInt16) {
        if mainHoldStopKey == code {
            mainHoldStopKey = nil
            if model.continuous { model.toggle() }
            return
        }
        guard model.heldKey == code, !model.continuous, !model.voiceEditing else { return }
        if model.phase == .preparing { model.cancel() }
        else if model.phase == .listening { Task { await model.stop(reason: "modifierRelease") } }
    }
    private func continuousModifierReleased(promotingHold: Bool) {
        // A slow chord can follow the hold that just started main-window input.
        if promotingHold && model.continuous && mainHoldStopKey == nil { return }
        toggleContinuous()
    }
    private var mainWindowPresented: Bool { window?.isVisible == true && window?.isMiniaturized != true }
    private func toggleMainWindow() {
        if mainWindowPresented { model.requestWindowCommit() }
        else { window?.deminiaturize(nil); openWindow() }
    }
    private func presentWindow(activate: Bool) {
        if activate { model.prepareWindowInput(application: NSWorkspace.shared.frontmostApplication) }
        closeOverlay()
        model.refreshPermission()
        model.settings.refreshLoginStatus()
        if window == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 680), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            window.title = "信楽"; window.isReleasedWhenClosed = false; window.delegate = self
            window.titleVisibility = .visible; window.titlebarAppearsTransparent = false
            window.isOpaque = true; window.backgroundColor = .windowBackgroundColor
            window.level = .floating
            window.isMovable = true
            window.isMovableByWindowBackground = true
            window.contentView = NSHostingView(rootView: FlowView(model: model, settings: model.settings))
            window.center(); self.window = window
        }
        window?.setContentSize(NSSize(width: 600, height: model.editingWorkspace ? 740 : 680))
        if activate {
            NSApp.activate(ignoringOtherApps: true)
            window?.makeKeyAndOrderFront(nil)
        } else { window?.orderFrontRegardless() }
        writeDiagnostics()
    }
    func windowWillClose(_ notification: Notification) {
        if (notification.object as? NSWindow) === setupWindow {
            setup.cancelAssets()
            // Release the SwiftUI subscription so the permission poll stops after setup.
            setupWindow?.contentView = nil
            setupWindow = nil
        }
        if let closed = notification.object as? NSWindow,
           let key = documentWindows.first(where: { $0.value === closed })?.key {
            documentWindows.removeValue(forKey: key)
        }
        let closedMain = (notification.object as? NSWindow) === window
        if closedMain { model.closeWindowWithoutCommit(); modifierHold.resetMainGesture() }
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if closedMain && !self.documentWindows.values.contains(where: { $0.isVisible }),
               NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier,
               let app = self.lastExternalApp, !app.isTerminated,
               app.processIdentifier != ProcessInfo.processInfo.processIdentifier {
                app.activate(options: [])
            }
            self.writeDiagnostics()
        }
    }
    func windowDidMiniaturize(_ notification: Notification) {
        if (notification.object as? NSWindow) === window && model.phase == .listening && !model.voiceEditing { showOverlay() }
        writeDiagnostics()
    }
    func windowDidDeminiaturize(_ notification: Notification) {
        if (notification.object as? NSWindow) === window { closeOverlay() }
        writeDiagnostics()
    }
    func windowDidMove(_ notification: Notification) {
        if let moved = notification.object as? NSWindow, moved === window { writeDiagnostics() }
    }
    @objc func toggle() { model.toggle() }
    @objc func toggleContinuous() {
        mainHoldStopKey = nil
        model.toggleContinuous()
        if model.continuous && model.isBusy {
            model.showDictationRequest = UUID()
            closeOverlay(); presentWindow(activate: false)
        }
    }
    private func configureVoiceEditShortcut(_ value: VoiceEditShortcut) {
        voiceEditHotkey.unregister()
        guard value != .none, model.demoFile == nil else { return }
        let modifiers = value == .controlOptionE ? UInt32(controlKey | optionKey) : UInt32(optionKey | shiftKey)
        if !voiceEditHotkey.register(keyCode: UInt32(kVK_ANSI_E), modifiers: modifiers) {
            model.status = "音声編集のショートカットを登録できませんでした。設定で別のキーを選んでください。"
        }
    }
    private func configureContinuousShortcut(_ value: ContinuousShortcut) {
        continuousHotkey.unregister()
        continuousMenuItem?.title = "連続入力を開始・停止  " + value.symbol
        if value == .controlShiftSpace, model.demoFile == nil,
           !continuousHotkey.register(modifiers: UInt32(controlKey | shiftKey)) {
            model.status = "連続入力のショートカットを登録できませんでした。別のキーを選んでください。"
        }
    }
    @objc func pasteLastInput() {
        guard !retrying, !model.isBusy, !model.pasteText.isEmpty else { return }
        retrying = true
        Task { @MainActor in
            defer { retrying = false }
            if fixtureOnly {
                guard let fixture = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "jp.ys.koeflow.fixture" }) else { return }
                fixture.activate(options: [])
                try? await Task.sleep(for: .milliseconds(200))
                guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "jp.ys.koeflow.fixture" else { return }
            }
            if NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier {
                guard let app = lastExternalApp, !app.isTerminated,
                      app.processIdentifier != ProcessInfo.processInfo.processIdentifier else {
                    model.status = "入力欄を選び、⌃⌥Vで最後の入力を貼り付けてください。"; return
                }
                app.activate(options: [])
                try? await Task.sleep(for: .milliseconds(150))
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { return }
            }
            await model.pasteLastInput()
        }
    }
    @objc func reeditLastInput() { model.beginLastInputEdit() }
    @objc func closeWindow() { (NSApp.keyWindow ?? window)?.performClose(nil) }
    @objc func openSetup() {
        guard !model.isBusy else { presentWindow(activate: true); return }
        closeOverlay(); window?.orderOut(nil)
        if setupWindow == nil {
            let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: 690),
                styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
            panel.title = SetupCopy.title; panel.isReleasedWhenClosed = false; panel.delegate = self
            // Permission dialogs and System Settings must appear in front of this guide.
            panel.level = .normal; panel.isOpaque = true; panel.backgroundColor = .windowBackgroundColor
            panel.contentView = NSHostingView(rootView: SetupView(setup: setup, settings: model.settings,
                finish: { [weak self] in
                    guard let self else { return }
                    self.model.refreshPermission()
                    if self.model.demoFile == nil { self.modifierHold.restartMonitoring() }
                    self.setupWindow?.close()
                    self.writeDiagnostics()
                    if let app = self.lastExternalApp, !app.isTerminated { app.activate(options: []) }
                }, openGuide: { [weak self] in self?.openInstallGuide() }))
            panel.center(); setupWindow = panel
        }
        NSApp.activate(ignoringOtherApps: true); setupWindow?.makeKeyAndOrderFront(nil)
        writeDiagnostics()
    }
    @objc func openInstallGuide() { openDocument("信楽_導入ガイド", title: "信楽 — 導入ガイド") }
    @objc func openManual() { openDocument("信楽_操作マニュアル", title: "信楽 — 操作マニュアル") }
    @objc func openQuickGuide() { openDocument("信楽_操作早見表", title: "信楽 — 操作早見表", format: "pdf") }
    @objc func openWritingGuide() { openDocument("信楽_文章整形とアプリ別設定", title: "信楽 — 文章整形・アプリ別設定") }
    @objc func openVoiceEditGuide() { openDocument("信楽_音声編集", title: "信楽 — 音声編集") }
    @objc func openGeographyGuide() { openDocument("信楽_地名辞書", title: "信楽 — 地名辞書") }
    @objc func openContinuousGuide() { openDocument("信楽_連続入力とプロンプトカード", title: "信楽 — 連続入力・プロンプトカード") }
    private func openDocument(_ resource: String, title: String, format: String = "html") {
        // APFS enumerates Japanese dakuten in decomposed Unicode; CFBundle lookup
        // can miss an otherwise existing file when given the composed spelling.
        let file = Bundle.main.resourceURL?.appendingPathComponent(resource).appendingPathExtension(format)
        guard let url = file.flatMap({ FileManager.default.fileExists(atPath: $0.path) ? $0 : nil })
                ?? Bundle.main.url(forResource: resource, withExtension: format) else {
            let alert = NSAlert(); alert.messageText = "ファイルが見つかりません"
            alert.informativeText = "配布セットに含まれる信楽アプリを使用してください。"; alert.runModal(); return
        }
        if documentWindows[resource] == nil {
            let frame = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1000, height: 900)
            let doc = NSWindow(contentRect: NSRect(x: 0, y: 0, width: min(860, frame.width-80), height: min(760, frame.height-80)), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            doc.level = resource == "信楽_導入ガイド" ? .normal : .floating
            doc.title = title; doc.titlebarAppearsTransparent = true; doc.isReleasedWhenClosed = false; doc.delegate = self
            if format == "pdf" {
                let pdf = PDFView(frame: NSRect(origin: .zero, size: doc.contentLayoutRect.size))
                pdf.displayMode = .singlePage; pdf.displaysPageBreaks = false
                pdf.document = PDFDocument(url: url)
                doc.contentView = pdf
                pdf.autoScales = true; pdf.scaleFactor = pdf.scaleFactorForSizeToFit
            } else {
                let web = WKWebView(); web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
                doc.contentView = web
            }
            doc.center(); documentWindows[resource] = doc
        }
        NSApp.activate(ignoringOtherApps: true)
        documentWindows[resource]?.makeKeyAndOrderFront(nil); writeDiagnostics()
    }
    @objc func closeOverlay() { overlay?.orderOut(nil) }
    @objc func quit() {
        model.persistWindowDraft()
        setup.cancelAssets()
        Task { await model.engine.cancel(); await HaasAssets.shared.shutdown(); NSApp.terminate(nil) }
    }
    func showOverlay() {
        overlayAutoHide?.cancel(); overlayAutoHide = nil
        if mainWindowPresented {
            closeOverlay(); model.showDictationRequest = UUID(); writeDiagnostics(); return
        }
        if overlay == nil {
            let panel = CaptionPanel(contentRect: NSRect(x: 0, y: 0, width: 328, height: 80), styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
            panel.level = .floating; panel.isOpaque = false; panel.backgroundColor = .clear
            panel.hasShadow = true; panel.hidesOnDeactivate = false; panel.isMovableByWindowBackground = true
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            panel.contentView = NSHostingView(rootView: OverlayView(model: model, settings: model.settings))
            if let screen = NSScreen.main {
                panel.setFrameOrigin(NSPoint(x: screen.visibleFrame.midX - 164, y: screen.visibleFrame.minY + 60))
            }
            overlay = panel
        }
        overlay?.orderFrontRegardless()
    }
    private func updateStatus() {
        writeDiagnostics()
        window?.setContentSize(NSSize(width: 600, height: model.editingWorkspace ? 740 : 680))
        overlayAutoHide?.cancel(); overlayAutoHide = nil
        if model.phase == .idle {
            mainHoldStopKey = nil
            let delay: UInt64 = model.inputFailed ? 8_000_000_000 : 3_000_000_000
            overlayAutoHide = Task { [weak self] in
                try? await Task.sleep(nanoseconds: delay)
                guard !Task.isCancelled, let self, self.model.phase == .idle else { return }
                self.overlay?.orderOut(nil)
                self.writeDiagnostics()
            }
        }
        let compact = model.phase == .idle && model.text.isEmpty && model.status == "入力欄を選び、長押しで開始"
        overlay?.setContentSize(NSSize(width: 328, height: compact ? 80 : 132))
        statusItem?.button?.image = NSImage(systemSymbolName: model.phase == .listening ? "mic.fill" : "waveform", accessibilityDescription: "信楽")
        statusItem?.button?.contentTintColor = model.phase == .listening ? .systemBlue : nil
    }
}

@main struct Main {
    @MainActor static func main() async {
        let arguments = CommandLine.arguments
        if await DevelopmentChecks.run(arguments) { return }
        // Finder normally reuses an existing process; also protect direct launches.
        if let existing = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == Bundle.main.bundleIdentifier && $0.processIdentifier != ProcessInfo.processInfo.processIdentifier
        }) {
            let previousVersion = existing.bundleURL.flatMap { Bundle(url: $0) }?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            let thisVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            if previousVersion != thisVersion {
                let alert = NSAlert()
                alert.messageText = "以前の信楽が起動しています"
                alert.informativeText = "録音中の場合は、以前の信楽で停止して文章を確認してください。その後、メニューバーの信楽を終了し、新しい信楽をもう一度開いてください。以前のアプリや設定は削除していません。"
                alert.addButton(withTitle: "以前の信楽を表示")
                alert.addButton(withTitle: "閉じる")
                NSApplication.shared.activate(ignoringOtherApps: true)
                if alert.runModal() == .alertFirstButtonReturn { existing.activate(options: []) }
            } else { existing.activate(options: []) }
            return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        delegate.openSettingsOnLaunch = arguments.contains("--settings")
        delegate.fixtureOnly = arguments.contains("--retry-fixture") || arguments.contains("--edit-workspace-fixture") || arguments.contains("--suggestion-ui-fixture") || arguments.contains("--recovery-ui-fixture") || DevelopmentChecks.setupFixture != nil
        if DevelopmentChecks.setupFixture != nil {
            delegate.model.demoFile = URL(fileURLWithPath: "/dev/null")
            delegate.model.settings.recognitionLogEnabled = true
        }
        if arguments.contains("--edit-workspace-fixture") { delegate.model.prepareReeditFixture(); delegate.openSettingsOnLaunch = true }
        if arguments.contains("--suggestion-ui-fixture") { delegate.model.prepareSuggestionFixture(); delegate.openSettingsOnLaunch = true }
        if let i = arguments.firstIndex(of: "--diagnostics-output"), arguments.count > i + 1 {
            delegate.diagnosticsURL = URL(fileURLWithPath: arguments[i + 1])
        }
        if let i = arguments.firstIndex(of: "--demo-audio"), arguments.count > i + 1 {
            delegate.model.demoFile = URL(fileURLWithPath: arguments[i + 1])
            if let p = arguments.firstIndex(of: "--demo-purpose"), arguments.count > p + 1,
               let purpose = WritingPurpose(rawValue: arguments[p + 1]) {
                delegate.model.demoWritingPlan = WritingPlan(purpose: purpose, mode: .automatic)
            }
        }
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
