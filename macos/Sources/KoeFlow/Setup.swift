import AppKit
import AVFoundation
import Combine
import KoeFlowCore
import SwiftUI

/// Checks permissions without recording audio or reading another application's text.
@MainActor final class SetupModel: ObservableObject {
    enum Assets: String { case checking, missing, downloading, ready, unavailable }
    @Published private(set) var microphone = AVCaptureDevice.authorizationStatus(for: .audio)
    @Published private(set) var accessibility = AXIsProcessTrusted()
    @Published private(set) var assets: Assets = .checking
    @Published private(set) var error = ""
    @Published private(set) var installing = false
    @Published private(set) var progress: HaasPreparationProgress?
    private(set) var preparationCancelled = false
    private var preparationTask: Task<Void, Error>?
    private var assetLanguage = ""
    private let settings: Settings
    private let defaults: UserDefaults
    private let fixture: String?
    private var automaticPreparationAttempted = false
    private static let completionKey = "haasInitialSetupCompletedV1"
    let readOnlyLocation: Bool
    var completed: Bool { defaults.bool(forKey: Self.completionKey) }
    var needsPreparation: Bool {
        !completed || !ready
    }
    var ready: Bool {
        !readOnlyLocation && microphone == .authorized && (!settings.liveInput || accessibility) &&
        assets == .ready && assetLanguage == settings.language && !installing &&
        (fixture != nil || HaasAssets.shared.isReady)
    }
    var canRetryPreparation: Bool {
        !readOnlyLocation && !installing && assets != .unavailable &&
        (assets == .missing || !error.isEmpty && assets != .checking)
    }

    init(settings: Settings, defaults: UserDefaults = .standard, fixture: String? = nil,
         readOnlyLocation: Bool? = nil) {
        self.settings = settings; self.defaults = defaults; self.fixture = fixture
        self.readOnlyLocation = readOnlyLocation ?? (fixture != nil ? false :
            (try? Bundle.main.bundleURL.resolvingSymlinksInPath()
                .resourceValues(forKeys: [.volumeIsReadOnlyKey]).volumeIsReadOnly) == true)
        if let fixture {
            microphone = fixture == "missing" || fixture == "denied" ?
                (fixture == "denied" ? .denied : .notDetermined) : .authorized
            accessibility = fixture != "missing" && fixture != "denied"
            assets = fixture == "ready" ? .ready : fixture == "unsupported" ? .unavailable :
                fixture == "installing" ? .downloading : .missing
            assetLanguage = settings.language
            installing = fixture == "installing"
            if fixture == "error" { error = SetupCopy.downloadFailure }
        }
    }
    func refreshPermissions() {
        guard fixture == nil else { return }
        microphone = AVCaptureDevice.authorizationStatus(for: .audio)
        accessibility = AXIsProcessTrusted()
    }
    func requestMicrophone() async {
        guard fixture == nil, !readOnlyLocation else { return }
        if microphone == .notDetermined { _ = await AVCaptureDevice.requestAccess(for: .audio) }
        else if microphone != .authorized { openPrivacy("Privacy_Microphone") }
        refreshPermissions()
    }
    func requestAccessibility() {
        guard fixture == nil, !readOnlyLocation else { return }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        accessibility = AXIsProcessTrustedWithOptions(options)
        if !accessibility { openPrivacy("Privacy_Accessibility") }
    }
    private func openPrivacy(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?" + pane) {
            NSWorkspace.shared.open(url)
        }
    }
    func refreshAssets() async {
        refreshPermissions()
        guard fixture == nil, !installing, !readOnlyLocation else { return }
        if HaasAssets.shared.isReady {
            assets = .ready; assetLanguage = settings.language; error = ""; return
        }
        assets = .checking
        do {
            _ = try await HaasAssets.shared.check()
            assets = .missing
            if !automaticPreparationAttempted {
                automaticPreparationAttempted = true
                await installAssets()
            }
        } catch {
            assets = .missing; self.error = SetupCopy.downloadFailure + "\n" + error.localizedDescription
        }
    }
    func installAssets() async {
        guard fixture == nil, !readOnlyLocation, !installing, assets != .unavailable else { return }
        installing = true; assets = .downloading; error = ""; preparationCancelled = false
        let task = Task { try await HaasAssets.shared.prepare { [weak self] value in self?.progress = value } }
        preparationTask = task
        do {
            try await task.value
            guard !preparationCancelled else { throw CancellationError() }
            assets = .ready; assetLanguage = settings.language
        } catch {
            assets = .missing
            self.error = preparationCancelled ? SetupCopy.downloadCancelled : SetupCopy.downloadFailure + "\n" + error.localizedDescription
        }
        preparationTask = nil; installing = false
        refreshPermissions()
    }
    func cancelAssets() {
        guard installing else { return }
        preparationCancelled = true
        preparationTask?.cancel()
        HaasAssets.shared.cancelPreparation()
    }
    @discardableResult func finish() -> Bool {
        refreshPermissions()
        guard ready else { return false }
        if fixture == nil { defaults.set(true, forKey: Self.completionKey) }
        return true
    }
    var microphoneDetail: String {
        switch microphone {
        case .authorized: return SetupCopy.allowed
        case .denied: return SetupCopy.microphoneDenied
        case .restricted: return SetupCopy.microphoneRestricted
        default: return SetupCopy.microphoneDetail
        }
    }
    var assetDetail: String {
        if installing, let progress {
            switch progress.stage {
            case .checking: return SetupCopy.assetChecking
            case .downloading:
                return String(format: "Haasをダウンロードしています。%.0f%%（%.1f / %.1f MB）", progress.fraction * 100, Double(progress.bytes) / 1_000_000, Double(progress.total) / 1_000_000)
            case .verifying: return "ダウンロードしたモデルのサイズとハッシュを検証しています。"
            case .warming: return "Haasを読み込み、音声入力の準備をしています。"
            case .ready: return SetupCopy.assetReady
            }
        }
        switch assets {
        case .ready: return SetupCopy.assetReady
        case .checking: return SetupCopy.assetChecking
        case .downloading: return SetupCopy.assetDownloading
        case .unavailable: return SetupCopy.assetUnavailable
        case .missing: return SetupCopy.assetDetail
        }
    }
    static func selfTest() -> [String: Any] {
        let suite = "jp.ys.koeflow.setup-test." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let settings = Settings()
        var rows: [[String: Any]] = []
        for (scenario, expected) in [("missing", false), ("denied", false), ("unsupported", false),
                                     ("installing", false), ("error", false), ("ready", true)] {
            let model = SetupModel(settings: settings, defaults: defaults, fixture: scenario)
            let accepted = model.finish()
            rows.append(["case": scenario, "passed": accepted == expected &&
                !defaults.bool(forKey: completionKey), "finishAccepted": accepted])
        }
        let readOnly = SetupModel(settings: settings, defaults: defaults, fixture: "ready", readOnlyLocation: true)
        rows.append(["case": "readOnlyInstallationBlocked", "passed": !readOnly.finish()])
        let changed = SetupModel(settings: settings, defaults: defaults, fixture: "ready")
        changed.assetLanguage = "not-the-selected-language"
        rows.append(["case": "changedLanguageNotReady", "passed": !changed.finish()])
        let lostWorker = SetupModel(settings: settings, defaults: defaults, fixture: "ready")
        lostWorker.assets = .missing; lostWorker.automaticPreparationAttempted = true; lostWorker.error = ""
        rows.append(["case": "lostWorkerCanBePreparedAgain", "passed": !lostWorker.ready && lostWorker.canRetryPreparation])
        let flow = FlowModel()
        flow.demoFile = URL(fileURLWithPath: "/dev/null")
        flow.setupBlocksInput = { true }
        flow.start(continuous: true)
        rows.append(["case": "setupBlocksRecording", "passed": flow.phase == .idle && !flow.continuous && flow.text.isEmpty])
        return ["passed": rows.allSatisfy { $0["passed"] as? Bool == true }, "cases": rows]
    }
}

@MainActor struct SetupView: View {
    @ObservedObject var setup: SetupModel
    @ObservedObject var settings: Settings
    let finish: () -> Void
    let openGuide: () -> Void
    private let poll = Timer.publish(every: 2, on: .main, in: .common).autoconnect()
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(SetupCopy.title, systemImage: "waveform.circle.fill")
                .font(.system(size: 26, weight: .semibold))
            Text(SetupCopy.intro).foregroundStyle(.secondary).lineSpacing(4)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if setup.readOnlyLocation {
                        Label(SetupCopy.copyFirst, systemImage: "externaldrive.badge.exclamationmark")
                            .foregroundStyle(.orange)
                        Button(SetupCopy.openApplications) { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications")) }
                    }
                    step(1, title: SetupCopy.microphoneTitle, detail: setup.microphoneDetail,
                         complete: setup.microphone == .authorized) {
                        if setup.microphone != .authorized {
                            Button(setup.microphone == .notDetermined ? SetupCopy.allowMicrophone : SetupCopy.openSettings) {
                                Task { await setup.requestMicrophone() }
                            }.disabled(setup.readOnlyLocation || setup.microphone == .restricted)
                        }
                    }
                    step(2, title: SetupCopy.accessibilityTitle,
                         detail: setup.accessibility ? SetupCopy.allowed : SetupCopy.accessibilityDetail,
                         complete: setup.accessibility) {
                        if !setup.accessibility {
                            Button(SetupCopy.allowAccessibility) { setup.requestAccessibility() }
                                .disabled(setup.readOnlyLocation)
                            Text(SetupCopy.returnHere).font(.system(size: 14)).foregroundStyle(.secondary)
                        }
                    }
                    step(3, title: SetupCopy.assetTitle, detail: setup.assetDetail, complete: setup.assets == .ready) {
                        if setup.installing {
                            if let progress = setup.progress, progress.stage == .downloading {
                                ProgressView(value: progress.fraction)
                            } else { ProgressView().controlSize(.small) }
                            Button("準備を中止") { setup.cancelAssets() }
                        } else if setup.canRetryPreparation {
                            Button(SetupCopy.prepareAssets) { Task { await setup.installAssets() } }
                                .disabled(setup.readOnlyLocation || setup.assets == .checking)
                        }
                        if !setup.error.isEmpty {
                            Text(setup.error).foregroundStyle(.orange).font(.system(size: 14))
                                .textSelection(.enabled)
                        }
                    }
                    Divider()
                    Text(SetupCopy.firstInputTitle).font(.system(size: 18, weight: .semibold))
                    Text(SetupCopy.firstInput).lineSpacing(4)
                    Text(SetupCopy.recovery).font(.system(size: 14)).foregroundStyle(.secondary)
                }.padding(.trailing, 8)
            }
            VStack(alignment: .leading, spacing: 6) {
                Toggle(SetupCopy.saveLog, isOn: $settings.recognitionLogEnabled)
                Text(SetupCopy.privacy).font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(3)
            }
            HStack {
                Button(SetupCopy.installGuide) { openGuide() }
                Button(SetupCopy.recheck) { Task { await setup.refreshAssets() } }.disabled(setup.installing)
                Spacer()
                Button(SetupCopy.finish) { if setup.finish() { finish() } }
                    .buttonStyle(.borderedProminent).disabled(!setup.ready)
            }
        }
        .padding(24).frame(width: 580, height: 690)
        .background(Color(nsColor: .windowBackgroundColor)).font(.system(size: 16))
        .task { await setup.refreshAssets() }
        .onReceive(poll) { _ in setup.refreshPermissions() }
        .onChange(of: settings.language) { _, _ in Task { await setup.refreshAssets() } }
    }
    private func step<Actions: View>(_ number: Int, title: String, detail: String, complete: Bool,
                                    @ViewBuilder actions: () -> Actions) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: complete ? "checkmark.circle.fill" : "\(number).circle")
                .font(.system(size: 26)).foregroundStyle(complete ? Color.green : Color.secondary)
                .accessibilityLabel(complete ? SetupCopy.allowed : String(number))
            VStack(alignment: .leading, spacing: 7) {
                Text(title).font(.system(size: 18, weight: .semibold))
                Text(detail).foregroundStyle(.secondary).lineSpacing(3)
                actions()
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}


enum SetupCopy {
    static let title = "信楽の初回準備"
    static let intro = "Haasの日本語モデル（約352MB）を取得し、このMacで使える状態にします。マイクと直接入力の権限を許可し、準備完了を待ってください。別のインストーラーは必要ありません。"
    static let allowed = "許可済み"
    static let microphoneTitle = "マイクを許可する"
    static let microphoneDetail = "話した声をMac内で文字にするために使います。ボタンを押し、macOSの確認画面で「許可」を選んでください。"
    static let microphoneDenied = "マイクが許可されていません。システム設定の「マイク」で「信楽」をオンにしてください。"
    static let microphoneRestricted = "このMacではマイクの使用が制限されています。管理者に確認してください。"
    static let allowMicrophone = "マイクを許可"
    static let openSettings = "システム設定を開く"
    static let accessibilityTitle = "入力欄への転記を許可する"
    static let accessibilityDetail = "システム設定の「アクセシビリティ」で「信楽」をオンにしてください。話した文章を、選んだ入力欄へ貼り付けるために使います。"
    static let allowAccessibility = "直接入力を許可"
    static let returnHere = "許可したら、この画面に戻ってください。状態は自動で更新されます。"
    static let assetTitle = "音声モデルを準備する"
    static let assetDetail = "Haasのモデルを自動で取得・検証します。取得済みの正常なモデルは再利用します。初回はインターネット接続が必要です。"
    static let assetChecking = "Haasのモデルと認識エンジンを確認しています。"
    static let assetDownloading = "Haasのモデルを準備しています。回線や空き容量によって時間がかかることがあります。"
    static let assetUnavailable = "認識エンジンを利用できません。アプリをApplicationsへコピーしたか、導入ガイドの対応条件を確認してください。"
    static let assetReady = "準備完了。音声認識はMac内で行います。"
    static let prepareAssets = "モデルの準備を再試行"
    static let preparing = "準備中…"
    static let downloadFailure = "音声モデルを準備できませんでした。インターネット接続とMacの空き容量を確認し、再試行してください。"
    static let downloadCancelled = "準備を中止しました。取得済みの正常なモデルは保持しています。「モデルの準備を再試行」から続けられます。"
    static let firstInputTitle = "最初の音声入力"
    static let firstInput = "「使い始める」を押すとメインパネルが開きます。入力パネルで使うには、メインパネルを閉じ、入力したいアプリの文字入力欄をクリックしてください。左Controlを長押しして話し、キーを離すと文章が入力されます。"
    static let recovery = "転記できなかった場合は、左Controlを2回押してメインパネルを開き、「最後の入力をコピー」を使えます。キーは設定で変更できます。"
    static let saveLog = "認識した文字をMac内のログに保存する"
    static let privacy = "録音データは保存しません。文字ログには日時・入力先・転記結果も保存します。この設定は後から変更できます。"
    static let guide = "初回の準備"
    static let installGuide = "導入ガイド"
    static let recheck = "状態を再確認"
    static let finish = "使い始める"
    static let copyFirst = "先に「信楽.app」をアプリケーションフォルダへコピーし、コピーしたアプリを開いてください。DMG内からは準備を開始できません。"
    static let openApplications = "アプリケーションフォルダを開く"
}
