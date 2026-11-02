import AppKit
import Combine
import FoundationModels
import KoeFlowCore

@MainActor final class FlowModel: ObservableObject {
    enum Phase { case idle, preparing, listening, finishing, polishing }
    @Published var phase: Phase = .idle
    @Published var text = ""
    @Published var rawText = ""
    @Published var status = "入力欄を選び、長押しで開始"
    @Published var level: Float = 0
    @Published var direct = false
    @Published var continuous = false
    @Published private(set) var draftPending = false
    private var recordPrefix = ""
    private var rawPrefix = ""
    private var commitRequested = false
    private var limitReached = false
    private var windowDismissed = false
    var closeForCommit: (() -> Void)?
    var showDraftWindow: (() -> Void)?
    @Published private(set) var voiceEditing = false
    @Published private(set) var editingWorkspace = false
    @Published var draftSelection = NSRange(location: 0, length: 0)
    private var editDraft: VoiceEditDraft?
    private var draftExternalSource: String?
    private var draftExternalWhole: Bool?
    private var draftExternalBundle = ""
    private var lastInputOriginal = ""
    private var lastInputWasAttempted = false
    private var lastInputBundle = ""
    private var lastInputContext = WritingContext()
    private var lastInputDestination = LogDestination()
    private var editOriginal = ""
    private var editWholeDocument = false
    private var lastVoiceEditSource: String?
    private var lastVoiceEditWhole = false
    private let voiceEditor = LocalVoiceEditor()
    @Published var showDictationRequest = UUID()
    @Published private(set) var lastInput = ""
    @Published private(set) var inputFailed = false
    @Published var logError = ""
    @Published private(set) var isRetrying = false
    private var sessionRecorded = false
    private var dictationID = UUID()
    private var dictationStartedAt = Date()
    private var logDestination = LogDestination()
    private var transferOutcome: TransferOutcome = .notAttempted
    private var lastDictationID: UUID?
    private var lastInputLogged = false
    private let transcriptLog = TranscriptLog(url: TranscriptLog.defaultURL)
    private let transcriptSnapshot = TranscriptLog(url: TranscriptLog.snapshotURL)
    private let draftSnapshot = TranscriptLog(url: TranscriptLog.defaultURL.deletingLastPathComponent().appendingPathComponent("最後の入力.json"))
    private var lastCheckpoint = Date.distantPast
    private var checkpointText = ""
    @Published var targetName = "字幕プレビュー"
    @Published var elapsed: TimeInterval = 0
    @Published var firstTextLatency: TimeInterval?
    @Published var writingPurpose: WritingPurpose = .automatic
    @Published var writingNotice = ""
    @Published private(set) var lastTargetBundle = ""
    @Published private(set) var lastTargetApp = ""
    private(set) var writingSeconds: TimeInterval = 0
    private(set) var writingOutcome = "none"
    private var writingPlan = WritingPlan()
    private var recentContext = RecentContext<TextTarget.ContextIdentity>()
    private let pageContext = PageContextObserver()
    private var pageContextCapturedAt: TimeInterval = 0
    private var contextSubscriptions = Set<AnyCancellable>()
    private var writingRules: [WordRule] = []
    private var inputFormatter = TextFormatter.Prepared(rules: [])
    var reviewWordRules: [WordRule] { WordRule.resolve(settings.rules, bundleID: writingPlan.context.bundleID) }
    private var sourceText = ""
    private let writer = LocalWritingEngine()
    private(set) var lastCommitSeconds: TimeInterval?
    var inputTiming: [String: Double] { ["pasteSeconds": target.pasteSeconds,
        "modifierWaitSeconds": target.modifierWaitSeconds, "clipboardSaveSeconds": target.clipboardSaveSeconds] }
    @Published var accessibility = AXIsProcessTrusted()
    let settings = Settings()
    let engine: SpeechEngine
    private var target = TextTarget()
    private var preparedWindowTarget: TextTarget?
    private var pendingStart: Task<Void, Never>?
    private var timer: Timer?
    private var started = Date()
    private var errorStopping = false
    private(set) var heldKey: UInt16?
    private(set) var lastStopReason = "none"
    var demoFile: URL?
    var demoWritingPlan: WritingPlan?
    var showOverlay: (() -> Void)?
    var openSettings: (() -> Void)?
    var windowInputApp: (() -> NSRunningApplication?)?
    var openManual: (() -> Void)?
    var openQuickGuide: (() -> Void)?
    var openSetup: (() -> Void)?
    var setupBlocksInput: (() -> Bool)?
    var hideOverlay: (() -> Void)?
    var onStateChange: (() -> Void)?
    var showRecovery: (() -> Void)?
    var retryLastInput: (() -> Void)?
    var isBusy: Bool { phase != .idle || isRetrying }
    var localPolishAvailable: Bool { writer.available }
    var pasteText: String { editingWorkspace ? text : lastInput }
    var canRestoreOriginal: Bool { !sourceText.isEmpty || !rawText.isEmpty }
    var targetDiagnostic: [String: Any] { target.captureDiagnostic }
    var hasPendingDraft: Bool { draftPending || text != lastInput }
    var requiresReplacementReview: Bool { editingWorkspace || lastVoiceEditSource != nil }
    private func consumeDeliveredDraft(_ content: String) {
        if content == lastInput { lastInputWasAttempted = true }
        guard !editingWorkspace, content == text else { return }
        lastInput = content; lastInputWasAttempted = true; draftPending = false
    }

    static func joinRecording(_ prefix: String, _ value: String) -> String {
        guard !prefix.isEmpty else { return value }
        guard !value.isEmpty else { return prefix }
        return prefix + (prefix.hasSuffix("\n") || prefix.hasSuffix(" ") ? "" : "\n") + value
    }

    func requestWindowCommit(force: Bool = false) {
        guard !isRetrying, !voiceEditing else { return }
        if requiresReplacementReview {
            if force { retryLastInput?() }
            else { status = "編集した文書は、内容と置き換える範囲を確認して貼り付けてください。"; onStateChange?() }
            return
        }
        if phase == .idle { Task { await commitWindow(force: force) }; return }
        guard continuous else { return }
        commitRequested = true
        if phase == .listening { Task { await stop(reason: "windowCommit") } }
    }

    private func commitWindow(force: Bool = false) async {
        guard phase == .idle, !isRetrying else { return }
        commitRequested = false
        guard !text.isEmpty, force || hasPendingDraft else { closeForCommit?(); return }
        guard demoFile == nil else {
            draftPending = true; inputFailed = true
            status = "検証モードでは外部アプリへ転記しません。文章は保持しています。"
            showDraftWindow?(); onStateChange?(); return
        }
        // A fresh explicit invocation can select a new external field. An own
        // window commit uses only its captured target, never a previous app.
        if let front = NSWorkspace.shared.frontmostApplication,
           front.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            let fresh = TextTarget()
            direct = fresh.capture(application: front)
            target = fresh; preparedWindowTarget = nil
            if direct {
                let context = fresh.writingContext(scope: settings.scope(for: front.bundleIdentifier ?? ""), application: front)
                logDestination = settings.recognitionLogEnabled ? fresh.logDestination(context: context) : LogDestination()
            }
        }
        guard direct else {
            draftPending = true; inputFailed = true
            status = "入力先を確認できません。文章は保持しています。入力欄を選び直すか、コピーして貼り付けてください。"
            persistWindowDraft()
            showDraftWindow?(); onStateChange?(); return
        }
        isRetrying = true; onStateChange?()
        await finishInput(restoreFromWindow: true)
        let sent = transferOutcome == .confirmed || transferOutcome == .unconfirmed
        draftPending = !sent
        lastInput = text; lastInputWasAttempted = sent
        if settings.recognitionLogEnabled, demoFile == nil {
            do {
                try transcriptLog.append(TranscriptRecord(dictationID: lastDictationID, kind: .retry,
                    startedAt: Date(), destination: logDestination, outcome: transferOutcome))
            } catch { logError = "転記の記録を保存できませんでした。文章は画面からコピーできます。" }
        }
        isRetrying = false
        if sent {
            status = transferOutcome == .confirmed ? "入力しました。" : "転記を送信しました。入力先を確認してください。自動で再送はしません。"
            closeForCommit?()
        } else { showDraftWindow?() }
        persistWindowDraft()
        onStateChange?()
    }

    func closeWindowWithoutCommit() {
        commitRequested = false
        windowDismissed = true
        persistWindowDraft()
        if phase == .preparing || phase == .listening {
            cancel(reason: "録音を中止しました。認識した文章は保持しています。", presentRecovery: false)
        }
    }

    func prepareWindowInput(application: NSRunningApplication?) {
        preparedWindowTarget = nil
        if phase == .idle, !isRetrying, !editingWorkspace,
           application?.processIdentifier != ProcessInfo.processInfo.processIdentifier {
            target = TextTarget(); direct = false
        }
        guard demoFile == nil, phase == .idle, !isRetrying,
              let application, application.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
        let snapshot = TextTarget()
        preparedWindowTarget = snapshot.capture(application: application) ? snapshot : nil
        if !editingWorkspace {
            target = preparedWindowTarget ?? TextTarget()
            direct = preparedWindowTarget != nil
        }
    }

    init(engine: SpeechEngine? = nil) {
        self.engine = engine ?? SpeechEngine()
        settings.$contextScope.dropFirst().sink { [weak self] _ in self?.recentContext.clear(); self?.pageContext.clear() }.store(in: &contextSubscriptions)
        settings.$appProfiles.dropFirst().sink { [weak self] _ in self?.recentContext.clear(); self?.pageContext.clear() }.store(in: &contextSubscriptions)
        settings.$pageContextEnabled.dropFirst().sink { [weak self] _ in self?.pageContext.clear() }.store(in: &contextSubscriptions)
        pageContext.allowed = { [weak self] bundle in
            guard let self else { return false }
            return self.demoFile == nil && self.settings.pageContextEnabled && self.settings.scope(for: bundle) != .appOnly
        }
        if !DevelopmentChecks.isIsolatedFixture { pageContext.start() }
        self.engine.onText = { [weak self] text, _ in self?.receive(text) }
        self.engine.onLevel = { [weak self] level in self?.level = level }
        self.engine.onStatus = { [weak self] status in self?.status = status }
        self.engine.onError = { [weak self] message in self?.fail(message) }
        self.engine.onLimit = { [weak self] in self?.recordingLimitReached() }
        if settings.recognitionLogEnabled && !DevelopmentChecks.isIsolatedFixture,
           let saved = try? draftSnapshot.readSnapshot(), let snapshot = try? DraftSnapshot.decode(saved) {
            text = snapshot.text; rawText = snapshot.rawText; sourceText = snapshot.original
            lastInput = snapshot.text; lastInputOriginal = snapshot.original
            draftPending = snapshot.pending; lastInputWasAttempted = !snapshot.pending
        }
    }
    func persistWindowDraft() {
        guard demoFile == nil, settings.recognitionLogEnabled, !DevelopmentChecks.isIsolatedFixture,
              !requiresReplacementReview, draftExternalSource == nil, !text.isEmpty else { return }
        do {
            try draftSnapshot.writeSnapshot(DraftSnapshot(text: text, rawText: rawText,
                original: sourceText.isEmpty ? text : sourceText, pending: phase != .idle || hasPendingDraft).encoded())
        } catch { logError = "最後の入力を保存できませんでした。本文をコピーして保管してください。" }
    }
    func toggle() {
        switch phase {
        case .idle: start()
        case .preparing: cancel()
        case .listening: Task { await stop(reason: "toggle") }
        case .polishing: if voiceEditing { voiceEditor.cancel() } else { writer.cancel() }
        default: break
        }
    }
    func toggleContinuous() {
        guard !voiceEditing || phase == .idle else { return }
        if (phase == .listening || phase == .preparing) && !continuous {
            // Adding Shift after a slow Control press promotes the same recording.
            continuous = true; heldKey = nil; onStateChange?(); return
        }
        if phase == .idle { start(continuous: true) }
        else { toggle() }
    }
    func start(continuous: Bool = false, voiceEdit: Bool = false, heldKey: UInt16? = nil) {
        guard phase == .idle, !isRetrying else { return }
        guard setupBlocksInput?() != true else { return }
        let previousText = text, previousRaw = rawText, previousPending = draftPending
        persistWindowDraft()
        let ownWindow = NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier
        if editingWorkspace && !voiceEdit {
            if ownWindow { status = "編集中です。音声編集のキーで指示するか、本文を手直ししてください。"; return }
            endEditingWorkspace()
        }
        if editingWorkspace && voiceEdit && !ownWindow { endEditingWorkspace() }
        self.continuous = continuous
        recordPrefix = continuous && !voiceEdit && hasPendingDraft ? text : ""
        rawPrefix = recordPrefix.isEmpty ? "" : rawText
        commitRequested = false
        limitReached = false
        windowDismissed = false
        self.heldKey = heldKey; lastStopReason = "none"
        inputFailed = false; sessionRecorded = false; logError = ""
        dictationID = UUID(); dictationStartedAt = Date(); transferOutcome = .notAttempted
        lastCheckpoint = .distantPast
        checkpointText = ""
        accessibility = AXIsProcessTrusted()
        let usePreviousInput = ownWindow && (continuous || heldKey != nil) && !voiceEdit
        let inputApp = usePreviousInput ? windowInputApp?() : NSWorkspace.shared.frontmostApplication
        target = TextTarget()
        var captured = false
        if demoFile == nil && (settings.liveInput || continuous || voiceEdit) && (!ownWindow || usePreviousInput) {
            if usePreviousInput, let preparedWindowTarget, preparedWindowTarget.matchesCapture(application: inputApp) {
                target = preparedWindowTarget; captured = true
            } else { captured = target.capture(application: inputApp) }
        }
        preparedWindowTarget = nil
        direct = demoFile == nil && (settings.liveInput || continuous || voiceEdit) &&
            (!ownWindow || usePreviousInput) && captured
        if settings.liveInput && !continuous && demoFile == nil && !voiceEdit && !direct { inputFailed = true }
        if voiceEdit {
            if !ownWindow {
                guard direct, let selected = target.voiceEditText() else {
                    target.detach(); direct = false
                    status = target.reason.isEmpty ? "音声編集の本文を取得できませんでした。対応する入力欄を選んでください。対象は10万文字までです。" : target.reason
                    onStateChange?(); showOverlay?(); return
                }
                text = selected.text; sourceText = text; draftSelection = NSRange(location: 0, length: 0)
                draftExternalSource = selected.text; draftExternalWhole = selected.wholeDocument
                draftExternalBundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
                editingWorkspace = true
            }
            guard editingWorkspace, let draft = try? VoiceEditDraft(text: text, selection: draftSelection) else {
                target.detach(); direct = false; status = "編集する本文を大きいウィンドウに用意してください。"; onStateChange?(); openSettings?(); return
            }
            editDraft = draft; editOriginal = draft.target; editWholeDocument = draft.wholeDocument
        } else { editOriginal = ""; editWholeDocument = false }
        voiceEditing = voiceEdit
        let bundleID = inputApp?.bundleIdentifier ?? ""
        var context = voiceEdit && ownWindow ? writingPlan.context : demoFile == nil ? target.writingContext(scope: settings.scope(for: bundleID), application: inputApp) : WritingContext()
        if !voiceEdit, demoFile == nil, settings.pageContextEnabled, settings.scope(for: bundleID) != .appOnly,
           !target.contextExcluded, !PageContext.excluded(context.windowTitle) {
            context.recentPages = pageContext.reference()
        }
        pageContextCapturedAt = ProcessInfo.processInfo.systemUptime
        if !voiceEdit, demoFile == nil, direct, settings.scope(for: bundleID) == .nearby,
           !context.before.isEmpty || !context.after.isEmpty {
            let recent = recentContext.reference(for: target.contextIdentity(), now: ProcessInfo.processInfo.systemUptime)
            if !recent.isEmpty && !context.before.contains(recent) {
                context.before = recent + "\n" + String(context.before.suffix(219))
            }
        } else { recentContext.clear() }
        if !(voiceEdit && ownWindow) { logDestination = settings.recognitionLogEnabled && demoFile == nil ? target.logDestination(context: context) : LogDestination() }
        writingPlan = demoWritingPlan ?? settings.plan(for: context)
        writingRules = WordRule.resolve(settings.rules, bundleID: writingPlan.context.bundleID)
        inputFormatter = TextFormatter.Prepared(rules: writingRules, commands: settings.commands)
        writingPurpose = writingPlan.purpose; writingNotice = ""; writingSeconds = 0; writingOutcome = "none"
        if !voiceEdit { sourceText = "" }
        if !voiceEdit { draftExternalSource = nil; draftExternalWhole = nil; draftExternalBundle = "" }
        if !context.bundleID.isEmpty { lastTargetBundle = context.bundleID; lastTargetApp = context.appName }
        targetName = voiceEdit ? "編集モード · 自動転記オフ" : direct ? target.appName : "字幕プレビュー"
        status = voiceEdit ? "\(editWholeDocument ? "文書全体" : "選択範囲") · 編集モード · 自動転記オフ" : continuous ? "停止で文章を保持します。キーを2回押すと閉じて転記します。" : direct ? "字幕を表示し、停止すると入力先へ貼り付けます。" : (settings.liveInput ? target.reason : "話すと字幕に文字が表示されます。")
        if status.isEmpty { status = "入力欄を選び、ショートカットで開始してください。" }
        if !voiceEdit { text = recordPrefix }
        rawText = rawPrefix; firstTextLatency = nil; elapsed = 0
        phase = .preparing; onStateChange?()
        if voiceEdit { target.detach(); direct = false; showDictationRequest = UUID(); hideOverlay?(); openSettings?() }
        else if !continuous { showOverlay?() }
        else { showDictationRequest = UUID() }
        pendingStart = Task { [weak self] in
            guard let self else { return }
            do {
                if let demoFile {
                    phase = .listening; started = Date(); onStateChange?()
                    status = "テスト音声を実時間で認識しています。マイクは使用しません。"
                    _ = try await engine.streamFile(demoFile, localeID: settings.language)
                    await prepareFinalText()
                    if direct { await finishInput() }
                    retainLastInput(partial: false)
                    phase = .idle; status = direct ? "テスト音声を入力しました。" : "テスト音声を認識しました。"
                    onStateChange?(); pendingStart = nil; return
                }
                let hints = ContextVocabulary.recognitionHints(rules: writingRules, context: writingPlan.context)
                try await engine.startMicrophone(localeID: settings.language, hints: hints)
                guard !Task.isCancelled else { await engine.cancel(); return }
                phase = .listening; started = Date(); onStateChange?()
                if limitReached { await stop(reason: "timeLimit"); pendingStart = nil; return }
                if commitRequested && continuous {
                    await stop(reason: "windowCommit")
                    pendingStart = nil
                    return
                }
                timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        self.elapsed = Date().timeIntervalSince(self.started)
                        self.saveCheckpoint()
                        if self.elapsed >= 300 { self.recordingLimitReached() }
                    }
                }
            } catch {
                await engine.cancel()
                if !Task.isCancelled {
                    text = previousText; rawText = previousRaw; draftPending = previousPending
                    commitRequested = false
                    status = error.localizedDescription; phase = .idle; onStateChange?()
                }
            }
            pendingStart = nil
        }
    }
    private func receive(_ value: String) {
        guard phase == .listening || phase == .finishing || phase == .preparing else { return }
        rawText = Self.joinRecording(rawPrefix, value)
        if !voiceEditing { text = Self.joinRecording(recordPrefix, inputFormatter.format(value)) }
        saveCheckpoint()
        if !text.isEmpty && firstTextLatency == nil { firstTextLatency = Date().timeIntervalSince(started) }
    }
    private func finishInput(restoreFromWindow: Bool = false) async {
        let confirmed = await target.finish(text, restoreFromOwnApp: restoreFromWindow || continuous || heldKey != nil)
        transferOutcome = confirmed ? .confirmed : (target.captureDiagnostic["pastePosted"] as? Bool == true ? .unconfirmed : .notAttempted)
        if confirmed, settings.scope(for: writingPlan.context.bundleID) == .nearby {
            let topic = ContextVocabulary.topicReference(in: text, rules: writingRules)
            if topic.isEmpty { recentContext.advance(to: target.confirmedContextIdentity, now: ProcessInfo.processInfo.systemUptime) }
            else { recentContext.remember(key: target.confirmedContextIdentity, text: topic, now: ProcessInfo.processInfo.systemUptime) }
        } else { recentContext.clear() }
        if !confirmed { inputFailed = true; direct = false; targetName = "字幕プレビュー"; status = target.reason }
        else if continuous {
            let next = TextTarget()
            if next.capture() { preparedWindowTarget = next }
        }
    }
    func suspendDirectInput() {
        recentContext.clear()
        guard direct, phase == .listening || phase == .preparing || phase == .finishing || phase == .polishing else { return }
        if voiceEditing {
            voiceEditor.cancel()
            if phase == .listening || phase == .preparing { cancel(reason: "入力先や選択範囲が変わったため、音声編集を中止しました。") ; return }
        }
        inputFailed = true; target.detach(); direct = false; targetName = "字幕プレビュー"
        transferOutcome = .notAttempted
        status = "キー操作やクリックがあったため、直接入力を停止しました。字幕からコピーできます。"
        onStateChange?()
    }
    private func recordingLimitReached() {
        guard phase == .preparing || phase == .listening else { return }
        limitReached = true; commitRequested = false
        if phase == .listening { Task { await stop(reason: "timeLimit") } }
    }
    func stop(reason: String = "explicit") async {
        guard phase == .listening else { return }
        // No other shortcut/button may commit a hold-owned recording while its key is down.
        if reason != "timeLimit", let heldKey, CGEventSource.keyState(.hidSystemState, key: heldKey) {
            lastStopReason = "blockedKeyStillDown:" + reason; onStateChange?(); return
        }
        lastStopReason = reason
        let stopStarted = Date()
        if demoFile != nil { cancel(); return }
        phase = .finishing; timer?.invalidate(); timer = nil; onStateChange?()
        do { try await engine.finish() }
        catch {
            inputFailed = true
            target.detach(); direct = false
            status = "最終確認に失敗しました。表示されている文字はコピーできます。\n" + error.localizedDescription
            await engine.cancel()
        }
        if voiceEditing { await completeVoiceEdit(); return }
        await prepareFinalText()
        if windowDismissed { target.detach(); direct = false; transferOutcome = .cancelled }
        if !continuous && !limitReached && !windowDismissed && direct && !text.isEmpty { await finishInput() }
        draftPending = !text.isEmpty && transferOutcome != .confirmed && transferOutcome != .unconfirmed
        retainLastInput(partial: false)
        lastCommitSeconds = Date().timeIntervalSince(stopStarted)
        phase = .idle; level = 0
        persistWindowDraft()
        if text.isEmpty { status = "音声を認識できませんでした。マイクの入力を確認してください。" }
        else if limitReached { status = "録音は1回5分までです。文章を保持しました。再開すると追記できます。自動では転記していません。" }
        else if windowDismissed { status = "文章を保持しました。自動では転記していません。" }
        else if continuous && !inputFailed && !text.isEmpty { status = "文章を保持しました。キーを2回押すと、閉じて転記します。" }
        else if direct { status = writingNotice.isEmpty ? "入力しました。次の入力を開始できます。" : "入力しました。" + writingNotice }
        else if inputFailed && transferOutcome == .notAttempted && !target.reason.isEmpty { status = target.reason }
        onStateChange?()
        if continuous && commitRequested {
            if inputFailed { commitRequested = false; showDraftWindow?() }
            else { await commitWindow() }
            return
        }
        if !windowDismissed && (limitReached || inputFailed) && !text.isEmpty { showRecovery?() }
        if direct && transferOutcome == .confirmed && !text.isEmpty { hideOverlay?() }
    }
    private func completeVoiceEdit() async {
        defer { phase = .idle; level = 0; onStateChange?() }
        guard editingWorkspace, let draft = editDraft, !inputFailed else {
            status = "音声編集を中止しました。本文は変更していません。"
            recordVoiceEdit(final: ""); return
        }
        phase = .polishing; status = "対象の文章を端末内で編集しています。"; onStateChange?()
        do {
            let replacement = try await voiceEditor.edit(editOriginal, instruction: rawText)
            text = try draft.applying(replacement, to: text)
            draftSelection = NSRange(location: 0, length: 0)
            transferOutcome = .notAttempted; direct = false
            recordVoiceEdit(final: replacement)
            lastDictationID = dictationID; lastInputLogged = settings.recognitionLogEnabled
            status = "編集結果を表示しました。自動転記はしません。内容を確認してコピーするか、貼り付けてください。"
            showDictationRequest = UUID(); openSettings?()
        } catch {
            target.detach(); direct = false
            recordVoiceEdit(final: "")
            switch error {
            case VoiceEditError.notFound: status = "指定した語句が対象の文章に見つかりません。変更していません。"
            case VoiceEditError.tooLong: status = "語句の置換は10万文字、文体変更は1800文字までです。文体を変える場合は短い範囲を選択してください。変更していません。"
            case VoiceEditError.noChange: status = "変更はありませんでした。"
            case VoiceEditError.sourceChanged: status = "編集中に本文が変わったため、編集結果を反映しませんでした。"
            case VoiceEditError.unsupported: status = "「AをBに置き換えて」「敬語にして」などの指示を使ってください。変更していません。"
            case WritingError.unavailable: status = "文体の変更にはApple Intelligenceが必要です。語句の置換は端末内で使えます。"
            case WritingError.timeout: status = "音声編集が時間内に終わりませんでした。変更していません。"
            case WritingError.cancelled, is CancellationError: status = "音声編集を中止しました。変更していません。"
            default: status = "音声編集を完了できませんでした。本文は変更していません。"
            }
            openSettings?()
        }
    }
    func beginLastInputEdit() {
        guard phase == .idle, !isRetrying, !lastInput.isEmpty else { return }
        draftExternalSource = lastInputWasAttempted ? lastInput : nil
        draftExternalWhole = nil; draftExternalBundle = lastInputWasAttempted ? lastInputBundle : ""
        writingPlan = settings.plan(for: lastInputContext); logDestination = lastInputDestination
        text = lastInputOriginal.isEmpty ? lastInput : lastInputOriginal
        sourceText = text; rawText = ""; draftSelection = NSRange(location: 0, length: 0)
        editingWorkspace = true; voiceEditing = false; direct = false; target.detach()
        targetName = "編集モード · 自動転記オフ"
        status = "転記前の原文を呼び戻しました。入力先は元に戻しません。再編集後は、置き換える範囲を入力先で選んでください。"
        showDictationRequest = UUID(); hideOverlay?(); onStateChange?(); openSettings?()
    }
    func beginTextReview() {
        guard phase == .idle, !isRetrying, !editingWorkspace, !text.isEmpty else { return }
        draftExternalSource = lastInputWasAttempted ? lastInput : nil
        draftExternalWhole = nil; draftExternalBundle = lastInputWasAttempted ? lastInputBundle : ""
        // Keep the displayed result, including dictionary spelling; do not reread another app.
        if sourceText.isEmpty { sourceText = text }
        editingWorkspace = true; voiceEditing = false; direct = false; target.detach()
        transferOutcome = .notAttempted
        targetName = "編集モード · 自動転記オフ"
        status = "信楽の本文を編集しています。内容を確認してから貼り付けてください。"
        onStateChange?(); openSettings?()
    }
    func prepareSuggestionFixture() {
        demoFile = URL(fileURLWithPath: "/dev/null")
        text = "🙂原稿の構成をお願いします。\n章の構成はこのままです。\n匝瑳市へ送る資料を確認します。"
        sourceText = text; lastInput = text; editingWorkspace = true
        settings.rules = [WordRule(heard: "構成", written: "校正", mode: .suggestion)]
        targetName = "候補表示の検証"
        status = "下線の語をクリックすると、その箇所の候補を選べます。"
    }
    func prepareRecoveryFixture() {
        demoFile = URL(fileURLWithPath: "/dev/null")
        text = "小窓の復旧表示を確認するための文章です。"
        rawText = text; sourceText = text
        transferOutcome = .unconfirmed; retainLastInput(partial: false)
        phase = .idle; inputFailed = true; direct = false
        targetName = "字幕プレビュー"
        status = "転記結果を確認できませんでした。入力欄を確認してください。文字が入っていない場合だけ、最後の入力を貼り付け直せます。"
    }
    func endEditingWorkspace() {
        guard phase == .idle, !isRetrying, editingWorkspace else { return }
        if !text.isEmpty {
            lastInput = text; lastInputOriginal = sourceText
            lastVoiceEditSource = draftExternalSource; lastVoiceEditWhole = draftExternalWhole ?? false
            lastInputWasAttempted = draftExternalSource != nil; lastInputBundle = draftExternalBundle
            lastInputContext = writingPlan.context; lastInputContext.recentPages = ""; lastInputDestination = logDestination
        }
        editingWorkspace = false; voiceEditing = false; target.detach(); direct = false
        status = "通常の音声入力に戻りました。編集した文章は最後の入力として保持しています。"; onStateChange?()
    }
    // Development fixtures use fixed synthetic content and do not change settings.
    func prepareManualFixture(recording: Bool) {
        demoFile = URL(fileURLWithPath: "/dev/null")
        text = "信楽のMac版で音声入力をしています。\n話している間から途中の文字を確認できます。"
        rawText = text; sourceText = text; lastInput = text
        continuous = true; draftPending = !recording; phase = recording ? .listening : .idle
        elapsed = 20; level = recording ? 0.65 : 0; targetName = "入力先の例"
        status = recording ? "停止で文章を保持します。キーを2回押すと閉じて転記します。" : "文章を保持しました。キーを2回押すと、閉じて転記します。"
    }
    func prepareReeditFixture() {
        demoFile = URL(fileURLWithPath: "/dev/null") // Suppress real microphone/log activity in the UI fixture.
        lastInput = "前🙂東京です。東京の予定は15時です。後"
        lastInputOriginal = "前🙂東京です東京の予定は15時です後"
        lastInputWasAttempted = true; lastInputBundle = "jp.ys.koeflow.fixture"
        lastInputContext = WritingContext(bundleID: lastInputBundle, appName: "音声編集テスト")
        lastInputDestination = LogDestination(bundleID: lastInputBundle, appName: "音声編集テスト")
    }
    func previewFixture(audio: URL, selection: NSRange) async throws -> [String: Any] {
        let original = "前🙂東京です。東京の予定は15時です。後"
        demoFile = audio; text = original; sourceText = original
        editingWorkspace = true; voiceEditing = true; draftSelection = selection
        let draft = try VoiceEditDraft(text: original, selection: selection)
        editDraft = draft; editOriginal = draft.target; editWholeDocument = draft.wholeDocument
        phase = .listening; _ = try await engine.streamFile(audio, localeID: "ja-JP")
        await completeVoiceEdit()
        let expected = draft.wholeDocument ? "前🙂大阪です。大阪の予定は15時です。後" : "前🙂大阪です。東京の予定は15時です。後"
        return ["passed": text == expected && !direct, "text": text, "instruction": rawText,
            "automaticPaste": direct, "speechEvents": engine.eventCount, "wholeDocument": draft.wholeDocument]
    }
    private func recordVoiceEdit(final: String, partial: Bool = false) {
        guard !sessionRecorded else { return }
        sessionRecorded = true
        guard settings.recognitionLogEnabled, demoFile == nil else { return }
        do {
            try transcriptLog.append(TranscriptRecord(id: dictationID, kind: .voiceEdit, startedAt: dictationStartedAt,
                destination: logDestination, outcome: transferOutcome, partial: partial,
                recognized: rawText, final: "", purpose: .plain,
                editScope: editWholeDocument && draftExternalWhole != false ? .document : .selection, editedCharacters: final.isEmpty ? nil : final.count))
        } catch { logError = "音声編集のログを保存できませんでした。" }
    }
    private func prepareFinalText() async {
        sourceText = text
        if writingPlan.mode == .automatic && !text.isEmpty {
            phase = .polishing; onStateChange?()
            await rewriteCurrentText(timeout: 4)
        }
    }
    func cancel(reason: String = "停止しました。表示された文字は残ります。", presentRecovery: Bool = true) {
        recentContext.clear()
        guard phase == .preparing || phase == .listening else { return }
        lastStopReason = errorStopping ? "engineError" : "cancel"
        commitRequested = false
        pendingStart?.cancel(); target.detach(); direct = false
        timer?.invalidate(); timer = nil; phase = .finishing; onStateChange?()
        Task {
            // A preparing task may still own setup work; drain it before clearing the engine.
            await pendingStart?.value
            await engine.cancel()
            transferOutcome = .cancelled
            draftPending = !text.isEmpty
            if voiceEditing { recordVoiceEdit(final: "", partial: true) }
            else { retainLastInput(partial: true) }
            writingPlan.context = writingPlan.context.limited(to: .window)
            phase = .idle; level = 0; status = reason; onStateChange?()
            persistWindowDraft()
            if presentRecovery && !text.isEmpty { showRecovery?() }
        }
    }
    private func fail(_ message: String) {
        guard !errorStopping else { return }
        errorStopping = true
        if phase == .listening || phase == .preparing { cancel(reason: message) }
        else { status = message }
        errorStopping = false
    }
    func copy() {
        guard !text.isEmpty else { return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
    }
    func copyLastInput() {
        guard !isBusy, !lastInput.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(lastInput, forType: .string)
        status = "最後の入力をコピーしました。"; onStateChange?()
    }
    private func retainLastInput(partial: Bool) {
        guard !text.isEmpty, !sessionRecorded else { return }
        lastInput = text; sessionRecorded = true; lastVoiceEditSource = nil
        lastInputOriginal = sourceText.isEmpty ? rawText : sourceText
        lastInputWasAttempted = transferOutcome == .confirmed || transferOutcome == .unconfirmed
        lastInputBundle = writingPlan.context.bundleID
        lastInputContext = writingPlan.context; lastInputContext.recentPages = ""; lastInputDestination = logDestination
        lastDictationID = dictationID
        lastInputLogged = settings.recognitionLogEnabled && demoFile == nil
        guard settings.recognitionLogEnabled, demoFile == nil else { return }
        saveCheckpoint(force: true)
        do {
            try transcriptLog.append(TranscriptRecord(id: dictationID, startedAt: dictationStartedAt,
                destination: logDestination, outcome: transferOutcome, partial: partial,
                recognized: rawText, final: text, purpose: writingPurpose, cardName: writingPlan.cardName))
        }
        catch { logError = "認識ログを保存できませんでした。表示されている文字をコピーしてください。" }
    }
    private func saveCheckpoint(force: Bool = false) {
        guard demoFile == nil, settings.recognitionLogEnabled, !rawText.isEmpty,
              rawText != checkpointText, force || Date().timeIntervalSince(lastCheckpoint) >= 3 else { return }
        lastCheckpoint = Date()
        do { try transcriptSnapshot.writeSnapshot(rawText); checkpointText = rawText }
        catch { logError = "認識途中の文字を保存できませんでした。表示されている文字をコピーしてください。" }
    }
    func pasteLastInput() async {
        guard phase == .idle, !isRetrying, !pasteText.isEmpty else { return }
        let content = pasteText
        isRetrying = true
        defer { isRetrying = false }
        let retryStarted = Date()
        let retryTarget = TextTarget()
        let captured = retryTarget.capture()
        let context = retryTarget.writingContext(scope: settings.scope(for: NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""))
        let destination = settings.recognitionLogEnabled && (editingWorkspace || lastInputLogged) ? retryTarget.logDestination(context: context) : LogDestination()
        let confirmed: Bool
        if captured {
            if editingWorkspace, let original = draftExternalSource {
                guard let current = retryTarget.voiceEditText(), current.text == original,
                      draftExternalWhole == nil || current.wholeDocument == draftExternalWhole,
                      draftExternalBundle.isEmpty || context.bundleID == draftExternalBundle else {
                    inputFailed = true; status = "入力先の本文と範囲が一致しません。置き換える範囲を選び直すか、編集結果をコピーしてください。"
                    showRecovery?(); onStateChange?(); return
                }
                confirmed = await retryTarget.finish(content, replaceWholeDocument: current.wholeDocument)
            } else if let original = lastVoiceEditSource, !editingWorkspace {
                guard let current = retryTarget.voiceEditText(), current.text == original, current.wholeDocument == lastVoiceEditWhole else {
                    inputFailed = true; status = "元の文書と範囲が一致しないため、音声編集の再転記を中止しました。変更後の文章はコピーできます。"
                    showRecovery?(); onStateChange?(); return
                }
                guard lastInputBundle.isEmpty || context.bundleID == lastInputBundle else { inputFailed = true; status = "元の入力先を選び直してください。"; onStateChange?(); return }
                confirmed = await retryTarget.finish(content, replaceWholeDocument: lastVoiceEditWhole)
            } else { confirmed = await retryTarget.finish(content) }
        } else { confirmed = false }
        let sent = confirmed || retryTarget.captureDiagnostic["pastePosted"] as? Bool == true
        if sent { consumeDeliveredDraft(content) }
        if settings.recognitionLogEnabled && demoFile == nil && (editingWorkspace || lastInputLogged) {
            do {
                try transcriptLog.append(TranscriptRecord(dictationID: lastDictationID, kind: .retry,
                    startedAt: retryStarted, destination: destination, outcome: confirmed ? .confirmed : (sent ? .unconfirmed : .notAttempted)))
            } catch { logError = "再貼り付けの記録を保存できませんでした。" }
        }
        if confirmed {
            inputFailed = false; status = "最後の入力を貼り付けました。"
            if editingWorkspace {
                lastInput = content; lastInputOriginal = sourceText; lastInputWasAttempted = true; lastInputBundle = context.bundleID
                lastInputContext = context; lastInputDestination = destination
                draftExternalSource = content; draftExternalBundle = context.bundleID
            }
        } else { inputFailed = true; status = retryTarget.reason; showRecovery?() }
        persistWindowDraft()
        onStateChange?()
    }
    func openRecognitionLog() {
        do { try transcriptLog.prepare(); NSWorkspace.shared.open(transcriptLog.url) }
        catch { logError = "認識ログを開けませんでした。保存先のアクセス権を確認してください。" }
    }
    func openLogFolder() { NSWorkspace.shared.activateFileViewerSelecting([TranscriptLog.defaultURL.deletingLastPathComponent()]) }
    func requestAccessibility() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        accessibility = AXIsProcessTrustedWithOptions(options)
    }
    func refreshPermission() { accessibility = AXIsProcessTrusted() }
    func polishLocally() {
        guard phase == .idle, !text.isEmpty, localPolishAvailable else { return }
        if voiceEditing { voiceEditing = false; if !editingWorkspace { sourceText = text } }
        // A manual request can use updated purpose settings, but never rereads another app.
        writingPlan = settings.plan(for: writingPlan.context)
        writingRules = WordRule.resolve(settings.rules, bundleID: writingPlan.context.bundleID); writingPurpose = writingPlan.purpose
        if writingPlan.purpose == .plain { status = "原文の設定では文章を変更しません。用途を選んでください。"; return }
        if sourceText.isEmpty { sourceText = text }
        phase = .polishing; target.detach(); direct = false; onStateChange?()
        Task {
            defer { phase = .idle; onStateChange?() }
            await rewriteCurrentText(timeout: 15, originalOverride: editingWorkspace ? text : nil)
            status = writingOutcome == "formatted" ? "端末内で整えました。内容を確認してコピーしてください。" : writingNotice
            writingPlan.context = writingPlan.context.limited(to: .window)
        }
    }
    private func rewriteCurrentText(timeout: TimeInterval, originalOverride: String? = nil) async {
        writingPlan.context = writingPlan.context.limited(to: settings.scope(for: writingPlan.context.bundleID))
        if !settings.pageContextEnabled || ProcessInfo.processInfo.systemUptime - pageContextCapturedAt > 180 {
            writingPlan.context.recentPages = ""
        }
        let original = originalOverride ?? (sourceText.isEmpty ? text : sourceText)
        writingNotice = ""; writingOutcome = "original"
        guard writer.available else { writingNotice = "端末内整形を利用できないため、原文を使いました。"; return }
        do {
            let result = try await writer.rewrite(original, plan: writingPlan, rules: writingRules, timeout: timeout)
            text = result.text; writingPurpose = result.purpose; writingSeconds = result.seconds
            writingOutcome = result.purpose == .plain ? "abstained" : "formatted"
            if result.purpose == .plain { writingNotice = "用途を判断できないため、原文を使いました。" }
        } catch {
            text = original
            switch error {
            case WritingError.timeout: writingNotice = "整形を待つ時間を超えたため、原文を使いました。"
            case WritingError.tooLong: writingNotice = "長い文章のため、原文を使いました。"
            case WritingError.invalid: writingNotice = "内容の変化を検出したため、原文を使いました。"
            default: writingNotice = "整形を完了できなかったため、原文を使いました。"
            }
        }
    }
    func restoreOriginal() { guard phase == .idle else { return }; text = sourceText.isEmpty ? rawText : sourceText; draftSelection = NSRange(location: 0, length: 0); status = editingWorkspace ? "信楽側の原文に戻しました。入力先の文書は変更していません。" : "認識した原文に戻しました。" }
    func addLastTargetProfile() {
        guard !lastTargetBundle.isEmpty, !settings.appProfiles.contains(where: { $0.bundleID == lastTargetBundle }) else { return }
        settings.appProfiles.append(AppWritingProfile(bundleID: lastTargetBundle, name: lastTargetApp, scope: settings.contextScope))
    }

    /// Model-free integration checks: never request a microphone, load a model,
    /// capture another app's text, send keys, or read/write real preferences.
    static func windowDraftSelfTest() async -> [String: Any] {
        func fixture() -> FlowModel {
            let missing = FileManager.default.temporaryDirectory.appendingPathComponent("missing-haas-\(UUID()).gguf")
            let assets = HaasAssets(modelURL: missing, helperURL: URL(fileURLWithPath: "/dev/null"), libraryURL: URL(fileURLWithPath: "/dev/null"))
            let model = FlowModel(engine: SpeechEngine(assets: assets))
            model.demoFile = URL(fileURLWithPath: "/dev/null")
            model.settings.recognitionLogEnabled = false
            return model
        }
        var cases: [[String: Any]] = []
        let stopped = fixture()
        stopped.demoFile = nil // No writer/microphone exists; exercise the real stop path.
        stopped.continuous = true; stopped.phase = .listening
        stopped.text = "停止直前の語尾。"; stopped.rawText = stopped.text
        var closes = 0
        stopped.closeForCommit = { closes += 1 }
        await stopped.stop(reason: "mainSingle")
        cases.append(["case": "singleStopKeepsDraft", "passed": stopped.phase == .idle && stopped.text == "停止直前の語尾。" && stopped.draftPending && closes == 0])

        let sent = fixture()
        sent.lastInput = "転記済みの本文"; sent.text = sent.lastInput; sent.draftPending = false
        sent.closeForCommit = { closes += 1 }
        await sent.commitWindow()
        cases.append(["case": "alreadySentDoubleOnlyCloses", "passed": closes == 1 && !sent.isRetrying && !sent.inputFailed])
        sent.text += "追記"; let edited = sent.hasPendingDraft; sent.text = sent.lastInput
        cases.append(["case": "undoDoesNotResend", "passed": edited && !sent.hasPendingDraft])
        let retried = fixture()
        retried.text = "単停止した草稿"; retried.lastInput = retried.text; retried.draftPending = true
        retried.consumeDeliveredDraft(retried.lastInput) // Same rule for confirmed and sent-unverified.
        cases.append(["case": "manualRetryConsumesMatchingDraft", "passed": !retried.hasPendingDraft && retried.lastInputWasAttempted])
        retried.draftPending = true; retried.text = "さらに編集した草稿"
        retried.consumeDeliveredDraft(retried.lastInput)
        cases.append(["case": "retryDoesNotConsumeDifferentEditedDraft", "passed": retried.hasPendingDraft && retried.text == "さらに編集した草稿"])

        let noTarget = fixture()
        noTarget.text = "保持する草稿"; noTarget.draftPending = true
        var recovery = 0
        noTarget.showDraftWindow = { recovery += 1 }
        await noTarget.commitWindow()
        cases.append(["case": "unavailableDestinationKeepsText", "passed": noTarget.draftPending && noTarget.text == "保持する草稿" && recovery == 1 && !noTarget.isRetrying])

        let resumed = fixture()
        resumed.text = "編集した前半。"; resumed.rawText = "元の前半。"
        resumed.lastInput = "元の前半。"; resumed.draftPending = true
        resumed.start(continuous: true)
        let prefixPreserved = resumed.text == "編集した前半。" && resumed.rawPrefix == "元の前半。"
        await resumed.pendingStart?.value
        cases.append(["case": "failedRestartRestoresExactEditedDraft", "passed": prefixPreserved && resumed.phase == .idle && resumed.text == "編集した前半。" && resumed.rawText == "元の前半。" && resumed.draftPending])
        cases.append(["case": "resumeSeparatesUtterances", "passed": joinRecording("前半。", "後半。") == "前半。\n後半。" && joinRecording("前半。", "") == "前半。"])

        let limit = fixture()
        limit.demoFile = nil
        limit.phase = .listening; limit.continuous = false; limit.heldKey = 59
        limit.text = "上限までの本文"; limit.rawText = limit.text
        limit.limitReached = true; limit.commitRequested = false
        await limit.stop(reason: "timeLimit")
        cases.append(["case": "limitStopsHeldInputWithoutPasting", "passed": limit.phase == .idle && limit.text == "上限までの本文" && limit.draftPending && limit.transferOutcome == .notAttempted])

        let closed = fixture()
        closed.phase = .finishing; closed.continuous = true; closed.commitRequested = true
        closed.text = "閉じる前の本文"; closed.closeWindowWithoutCommit()
        cases.append(["case": "standardCloseClearsCommitIntent", "passed": !closed.commitRequested && closed.windowDismissed && closed.text == "閉じる前の本文"])
        return ["passed": cases.allSatisfy { $0["passed"] as? Bool == true }, "cases": cases,
            "modelsLoaded": 0, "microphoneUsed": false, "externalTextRead": false, "keysSent": false, "userDataWritten": false]
    }
}
