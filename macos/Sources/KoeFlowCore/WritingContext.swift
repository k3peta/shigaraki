import Foundation

public enum WritingPurpose: String, Codable, CaseIterable, Sendable {
    case automatic, plain, email, chat, document
    public var title: String {
        switch self { case .automatic: return "自動判定"; case .plain: return "原文"; case .email: return "メール"; case .chat: return "チャット"; case .document: return "文書" }
    }
}
public enum WritingMode: String, Codable, CaseIterable, Sendable {
    case inherit, manual, automatic, verbatim
    public var title: String {
        switch self { case .inherit: return "共通設定"; case .manual: return "手動で整形"; case .automatic: return "停止後に整形"; case .verbatim: return "整形しない" }
    }
}
public enum ContextScope: String, Codable, CaseIterable, Sendable {
    case appOnly, window, nearby
    public var title: String {
        switch self { case .appOnly: return "アプリ名のみ"; case .window: return "ウィンドウ・入力欄の情報"; case .nearby: return "入力欄の前後の文章も使う" }
    }
}
public struct AppWritingProfile: Codable, Equatable, Identifiable, Sendable {
    public var id: String { bundleID }
    public var bundleID: String
    public var name: String
    public var purpose: WritingPurpose
    public var mode: WritingMode
    public var scope: ContextScope
    public var promptCardID: String?
    public init(bundleID: String, name: String, purpose: WritingPurpose = .automatic, mode: WritingMode = .inherit, scope: ContextScope = .nearby, promptCardID: String? = nil) {
        self.bundleID = bundleID; self.name = name; self.purpose = purpose; self.mode = mode; self.scope = scope
        self.promptCardID = promptCardID
    }
}
/// An ephemeral, bounded snapshot. Never put its contents in diagnostics or defaults.
public struct WritingContext: Codable, Equatable, Sendable {
    public var bundleID: String
    public var appName: String
    public var windowTitle: String
    public var fieldHint: String
    public var before: String
    public var selected: String
    public var after: String
    public var recentPages: String
    public var supplement: String?
    public init(bundleID: String = "", appName: String = "", windowTitle: String = "", fieldHint: String = "", before: String = "", selected: String = "", after: String = "", recentPages: String = "", supplement: String? = nil) {
        self.bundleID = bundleID; self.appName = appName; self.windowTitle = String(windowTitle.prefix(160)); self.fieldHint = String(fieldHint.prefix(120))
        self.before = String(before.suffix(300)); self.selected = String(selected.prefix(200)); self.after = String(after.prefix(200))
        self.recentPages = String(recentPages.prefix(240))
        self.supplement = supplement.flatMap { $0.isEmpty ? nil : String($0.prefix(800)) }
    }
    public func limited(to scope: ContextScope) -> WritingContext {
        WritingContext(bundleID: bundleID, appName: appName, windowTitle: scope == .appOnly ? "" : windowTitle,
                       fieldHint: scope == .appOnly ? "" : fieldHint, before: scope == .nearby ? before : "",
                       selected: scope == .nearby ? selected : "", after: scope == .nearby ? after : "",
                       recentPages: scope == .appOnly ? "" : recentPages, supplement: supplement)
    }
    public static func surrounding(_ text: String, range: NSRange) -> (String, String, String)? {
        let value = text as NSString
        guard range.location >= 0, range.length >= 0, range.location <= value.length, range.length <= value.length - range.location else { return nil }
        // Clamp the range before slicing: a large editor must not create a large context copy.
        let start = max(0, range.location - 600), end = min(value.length, range.location + range.length + 400)
        return (String(value.substring(with: NSRange(location: start, length: range.location - start)).suffix(300)),
                String(value.substring(with: NSRange(location: range.location, length: min(range.length, 400))).prefix(200)),
                String(value.substring(with: NSRange(location: range.location + range.length, length: end - range.location - range.length)).prefix(200)))
    }
}
public struct WritingPlan: Equatable, Sendable {
    public var purpose: WritingPurpose
    public var mode: WritingMode
    public var context: WritingContext
    public var cardName = ""
    public var cardInstructions = ""
    public var cardExample: PromptExample?
    public var standardInstructions: [WritingPurpose: String] = [:]
    public init(purpose: WritingPurpose = .automatic, mode: WritingMode = .manual, context: WritingContext = .init()) {
        self.purpose = purpose; self.mode = mode; self.context = context
    }
}
public enum ContextRouter {
    public static func resolve(_ context: WritingContext, profiles: [AppWritingProfile], defaultPurpose: WritingPurpose, automatic: Bool, scope: ContextScope) -> WritingPlan {
        let profile = profiles.first { $0.bundleID == context.bundleID }
        let bounded = context.limited(to: profile?.scope ?? scope)
        var purpose = profile?.purpose ?? defaultPurpose
        if purpose == .automatic {
            let bundle = context.bundleID.lowercased()
            if ["com.apple.mail", "com.microsoft.outlook", "com.readdle.smartemail-macos", "com.freron.mailmate", "com.airmail.airmail2"].contains(bundle) { purpose = .email }
            else if ["com.tinyspeck.slackmacgap", "com.hnc.discord", "com.apple.ichat", "ru.keepcoder.telegram", "com.microsoft.teams2", "jp.naver.line.mac"].contains(bundle) { purpose = .chat }
            else if ["com.apple.iwork.pages", "com.microsoft.word", "md.obsidian"].contains(bundle) { purpose = .document }
            else if ["com.apple.terminal", "com.googlecode.iterm2", "com.microsoft.vscode", "com.todesktop.230313mzl4w4u92"].contains(bundle) { purpose = .plain }
            else {
                // Web services vary within one browser. Conflicting evidence stays automatic.
                let hint = (bounded.windowTitle + " " + bounded.fieldHint).lowercased()
                let email = ["gmail", "outlook", "proton mail", "メールを作成", "受信トレイ"].contains { hint.contains($0) }
                let chat = ["slack", "discord", "microsoft teams", "chatgpt", "claude", "codex", "メッセージを入力"].contains { hint.contains($0) }
                if email != chat { purpose = email ? .email : .chat }
            }
        }
        let mode: WritingMode = purpose == .plain ? .verbatim : (profile?.mode == .inherit || profile == nil ? (automatic ? .automatic : .manual) : profile!.mode)
        return WritingPlan(purpose: purpose, mode: mode, context: bounded)
    }
}
public enum WritingValidation {
    public static func rejection(original: String, result: String, rules: [WordRule] = []) -> String? {
        guard !result.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              result.count >= max(1, Int(Double(original.count) * 0.65)), result.count <= original.count * 2 + 40 else { return "length" }
        for artifact in ["```", "<|", "</", "{", "}"] where !original.contains(artifact) && result.contains(artifact) { return "structure" }
        for pair in [("「", "」"), ("（", "）"), ("(", ")")] {
            if result.components(separatedBy: pair.0).count != result.components(separatedBy: pair.1).count { return "structure" }
        }
        // Protect numbers/URLs/mail addresses and registered spelling. This is conservative,
        // not a proof of semantic equivalence; ambiguous rewrites fall back to the original.
        let pattern = #"https?://[^\s<>「」]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|[0-9０-９]+(?:[.,:/．，：／][0-9０-９]+)*(?:万円|千円|億円|円|ドル|ユーロ|年|月|日|時|分|秒|人|個|台|本|件|%|％|℃)?"#
        func facts(_ text: String) -> [String] {
            let ns = text as NSString
            return (try? NSRegularExpression(pattern: pattern))?.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ns.substring(with: $0.range) } ?? []
        }
        guard facts(original).sorted() == facts(result).sorted() else { return "facts" }
        let quotations = #"「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"|`[^`\n]+`"#
        func quoted(_ text: String) -> [String] {
            let ns = text as NSString
            return (try? NSRegularExpression(pattern: quotations))?.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ns.substring(with: $0.range) } ?? []
        }
        guard quoted(original) == quoted(result) else { return "quotation" }
        // Numeric self-correction needs an explicit editing policy. Preserve its wording
        // rather than letting punctuation cleanup turn a correction into alternatives.
        let correction = #"[0-9０-９]+[^。！？\n]{0,12}いや"#
        if original.range(of: correction, options: .regularExpression) != nil {
            func wording(_ text: String) -> String {
                text.filter { !$0.isWhitespace && !$0.unicodeScalars.allSatisfy { CharacterSet.punctuationCharacters.contains($0) } }
            }
            guard wording(original) == wording(result) else { return "selfCorrection" }
        }
        // Suggestions stay as written until the user chooses a replacement in the editor.
        let spellings = rules.flatMap { $0.mode == .automatic ? [$0.written] : $0.heardForms + [$0.written] }
        for word in spellings where !word.isEmpty && original.contains(word) {
            guard result.components(separatedBy: word).count >= original.components(separatedBy: word).count else { return "dictionary" }
        }
        for negative in ["しない", "できない", "ない", "なかった", "ません", "不要", "禁止", "未定", "不可", "拒否", "中止", "取り消"] {
            guard original.components(separatedBy: negative).count == result.components(separatedBy: negative).count else { return "negation" }
        }
        return nil
    }
}
