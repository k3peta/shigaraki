import Foundation

public enum VoiceEditError: Error { case unsupported, emptySelection, tooLong, notFound, noChange, sourceChanged }
public enum VoiceEditStyle: String, Sendable {
    case polite, plain, bullets, punctuation
    public var instructions: String {
        switch self {
        case .polite: return "必要最小限の変更で、敬語とですます調に整えてください。"
        case .plain: return "必要最小限の変更で、だ・である調の常体に整えてください。"
        case .bullets: return "内容を省略せず、項目ごとの箇条書きにしてください。"
        case .punctuation: return "語句と意味を変えず、句読点と改行だけを整えてください。"
        }
    }
}
public enum VoiceEditCommand: Equatable, Sendable {
    case replace(String, String), replaceSelection(String), style(VoiceEditStyle)
    public init(instruction: String) throws {
        guard instruction.count <= 300 else { throw VoiceEditError.tooLong }
        let value = instruction.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "。.!！?？"))
        let ending = "(?:置き換えて|置換して|変換して|変更して|変えて|直して)(?:ください)?"
        func groups(_ pattern: String) -> [String]? {
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) else { return nil }
            let ns = value as NSString
            return (1..<match.numberOfRanges).map { ns.substring(with: match.range(at: $0)).trimmingCharacters(in: .whitespaces) }
        }
        if let pair = groups("^「([^「」]+)」を「([^「」]+)」に" + ending + "$") ??
            groups("^([^「」]+?)を([^「」]+?)に" + ending + "$") {
            guard pair.allSatisfy({ !$0.isEmpty && $0.count <= 200 }) else { throw VoiceEditError.unsupported }
            self = .replace(pair[0], pair[1]); return
        }
        if let replacement = groups("^「([^「」]+)」に" + ending + "$") ??
            groups("^([^「」を]+)に" + ending + "$") {
            guard !replacement[0].isEmpty, replacement[0].count <= 200 else { throw VoiceEditError.unsupported }
            self = .replaceSelection(replacement[0]); return
        }
        let styles: [(VoiceEditStyle, [String])] = [
            (.polite, ["敬語にして", "丁寧にして", "丁寧な文章にして", "ですます調にして"]),
            (.plain, ["常体にして", "だである調にして", "だ・である調にして"]),
            (.bullets, ["箇条書きにして"]),
            (.punctuation, ["句読点を整えて", "改行を整えて"])
        ]
        for (style, phrases) in styles where phrases.contains(where: { value == $0 || value == $0 + "ください" }) {
            self = .style(style); return
        }
        throw VoiceEditError.unsupported
    }
    /// Returns nil only for a supported style operation that needs the local model.
    public func literalResult(original: String) throws -> String? {
        guard !original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw VoiceEditError.emptySelection }
        guard original.count <= 100_000 else { throw VoiceEditError.tooLong }
        let result: String
        switch self {
        case let .replace(before, after):
            guard original.contains(before) else { throw VoiceEditError.notFound }
            result = original.replacingOccurrences(of: before, with: after)
        case let .replaceSelection(after): result = after
        case .style:
            guard original.count <= 1800 else { throw VoiceEditError.tooLong }
            return nil
        }
        guard result.count <= 200_000 else { throw VoiceEditError.tooLong }
        guard result != original else { throw VoiceEditError.noChange }
        return result
    }
}
public enum VoiceEditShortcut: String, Codable, CaseIterable, Sendable {
    case controlOptionE, optionShiftE, none
    public var symbol: String {
        switch self { case .controlOptionE: return "⌃⌥E"; case .optionShiftE: return "⌥⇧E"; case .none: return "なし" }
    }
    public var title: String {
        switch self { case .controlOptionE: return "Control＋Option＋E（標準）"; case .optionShiftE: return "Option＋Shift＋E"; case .none: return "使わない" }
    }
}
