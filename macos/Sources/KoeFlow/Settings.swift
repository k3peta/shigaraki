import Foundation
import Combine
import KoeFlowCore
import ServiceManagement

@MainActor final class Settings: ObservableObject {
    @Published var language = "ja-JP" { didSet { save() } }
    @Published var liveInput = true { didSet { save() } }
    @Published var commands = false { didSet { save() } }
    @Published var holdToTalk = false { didSet { save() } }
    @Published var holdModifier = "leftControl" { didSet { save() } }
    @Published var windowShortcut: WindowShortcut = .controlDoubleTap { didSet { save() } }
    @Published var continuousShortcut: ContinuousShortcut = .controlShift { didSet { save() } }
    @Published var voiceEditShortcut: VoiceEditShortcut = .controlOptionE { didSet { save() } }
    @Published var rules: [WordRule] = [] { didSet { save() } }
    @Published var writingPurpose: WritingPurpose = .automatic { didSet { save() } }
    @Published var automaticWriting = false { didSet { save() } }
    @Published var contextScope: ContextScope = .nearby { didSet { save() } }
    @Published var pageContextEnabled = true { didSet { save() } }
    @Published var appProfiles: [AppWritingProfile] = [] { didSet { save() } }
    @Published var inputContextProfiles: [InputContextProfile] = [] {
        didSet { if !loadingInputContextProfiles { inputContextProfilesChanged = true; save() } }
    }
    @Published var promptCards = PromptCard.standards { didSet { save() } }
    @Published var promptCardID = "" { didSet { save() } }
    @Published var recognitionLogEnabled = true { didSet { save() } }
    @Published private(set) var loginStatus = SMAppService.mainApp.status
    @Published private(set) var loginError: String?
    var launchAtLogin: Bool { loginStatus == .enabled || loginStatus == .requiresApproval }
    func refreshLoginStatus() { loginStatus = SMAppService.mainApp.status }
    func setLaunchAtLogin(_ enabled: Bool) {
        guard savesEnabled else { return }
        loginError = nil
        do {
            if enabled { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
        } catch { loginError = "ログイン項目を変更できませんでした：" + error.localizedDescription }
        refreshLoginStatus()
    }
    var loginMessage: String {
        if let loginError { return loginError }
        switch loginStatus {
        case .enabled: return "ログイン時はメニューバーに常駐します。"
        case .requiresApproval: return "システム設定でログイン項目を許可してください。"
        case .notFound: return "ログイン時の自動起動はオフです。"
        default: return "ログイン時の自動起動はオフです。"
        }
    }
    private let defaults = UserDefaults.standard
    private let savesEnabled = !DevelopmentChecks.isIsolatedFixture
    private var inputContextProfilesChanged = false
    private var loadingInputContextProfiles = true
    init() {
        // The native UI fixture starts empty and never writes the user's preferences.
        if !savesEnabled { recognitionLogEnabled = false; return }
        // Haas-ja is a Japanese model. The legacy value is retained on disk
        // until a normal preferences save, for compatibility with older builds.
        language = "ja-JP"
        liveInput = defaults.object(forKey: "liveInput") as? Bool ?? true
        commands = defaults.bool(forKey: "commands")
        holdToTalk = defaults.bool(forKey: "holdToTalk")
        holdModifier = defaults.string(forKey: "holdModifier") ?? "leftControl"
        windowShortcut = WindowShortcut(rawValue: defaults.string(forKey: "windowShortcut") ?? "") ?? .controlDoubleTap
        continuousShortcut = ContinuousShortcut(rawValue: defaults.string(forKey: "continuousShortcut") ?? "") ?? .controlShift
        voiceEditShortcut = VoiceEditShortcut(rawValue: defaults.string(forKey: "voiceEditShortcut") ?? "") ?? .controlOptionE
        if let data = defaults.data(forKey: "rules"), let decoded = try? JSONDecoder().decode([WordRule].self, from: data) { rules = decoded }
        writingPurpose = WritingPurpose(rawValue: defaults.string(forKey: "writingPurpose") ?? "") ?? .automatic
        automaticWriting = defaults.bool(forKey: "automaticWriting")
        contextScope = ContextScope(rawValue: defaults.string(forKey: "writingContextScope") ?? "") ?? .nearby
        pageContextEnabled = defaults.object(forKey: "pageContextEnabled") as? Bool ?? true
        if let data = defaults.data(forKey: "appWritingProfiles"), let decoded = try? JSONDecoder().decode([AppWritingProfile].self, from: data) { appProfiles = decoded }
        if let data = defaults.data(forKey: "inputContextProfiles") { inputContextProfiles = InputContextProfile.restored(from: data) }
        if let data = defaults.data(forKey: "promptCards"), let decoded = try? JSONDecoder().decode([PromptCard].self, from: data) {
            promptCards = PromptCard.restoringStandards(in: decoded)
        }
        promptCardID = defaults.string(forKey: "promptCardID") ?? ""
        recognitionLogEnabled = defaults.object(forKey: "recognitionLogEnabled") as? Bool ?? true
        inputContextProfilesChanged = false
        loadingInputContextProfiles = false
    }
    private func save() {
        guard savesEnabled else { return }
        defaults.set(language, forKey: "language"); defaults.set(liveInput, forKey: "liveInput")
        defaults.set(commands, forKey: "commands"); defaults.set(holdToTalk, forKey: "holdToTalk")
        defaults.set(holdModifier, forKey: "holdModifier")
        defaults.set(windowShortcut.rawValue, forKey: "windowShortcut")
        defaults.set(continuousShortcut.rawValue, forKey: "continuousShortcut")
        defaults.set(voiceEditShortcut.rawValue, forKey: "voiceEditShortcut")
        defaults.set(try? JSONEncoder().encode(rules), forKey: "rules")
        defaults.set(writingPurpose.rawValue, forKey: "writingPurpose")
        defaults.set(automaticWriting, forKey: "automaticWriting")
        defaults.set(contextScope.rawValue, forKey: "writingContextScope")
        defaults.set(pageContextEnabled, forKey: "pageContextEnabled")
        defaults.set(try? JSONEncoder().encode(appProfiles), forKey: "appWritingProfiles")
        if inputContextProfilesChanged {
            defaults.set(try? JSONEncoder().encode(inputContextProfiles.prefix(InputContextProfile.maximumProfiles).compactMap(\.normalized)), forKey: "inputContextProfiles")
            inputContextProfilesChanged = false
        }
        defaults.set(try? JSONEncoder().encode(promptCards), forKey: "promptCards")
        defaults.set(promptCardID, forKey: "promptCardID")
        defaults.set(recognitionLogEnabled, forKey: "recognitionLogEnabled")
    }
    func scope(for bundleID: String) -> ContextScope { appProfiles.first { $0.bundleID == bundleID }?.scope ?? contextScope }
    func plan(for context: WritingContext) -> WritingPlan {
        var context = context
        if !pageContextEnabled { context.recentPages = "" }
        let plan = ContextRouter.resolve(context, profiles: appProfiles, defaultPurpose: writingPurpose, automatic: automaticWriting, scope: contextScope)
        let id = appProfiles.first { $0.bundleID == context.bundleID }?.promptCardID ?? promptCardID
        return PromptCardRouter.apply(plan, cards: promptCards, cardID: id)
    }
    func addWordRule(_ rule: WordRule) {
        if let index = rules.firstIndex(where: { $0.hasSameKey(as: rule) }) {
            var updated = rule; updated.id = rules[index].id
            rules[index] = updated
        } else { rules.append(rule) }
    }
    var shortcutDescription: String {
        switch holdModifier { case "leftControl": return "左Control長押し"; case "leftOption": return "左Option長押し"; default: return "⌃⌥Space" }
    }
}
