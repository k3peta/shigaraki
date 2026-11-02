import Foundation

public struct SpeechPiece: Equatable, Sendable {
    public var start: Double
    public var end: Double
    public var text: String
    public var isFinal: Bool
    public init(start: Double, end: Double, text: String, isFinal: Bool) {
        self.start = start; self.end = end; self.text = text; self.isFinal = isFinal
    }
}

/// Results replace the audio range they describe, never append successive guesses.
public struct Transcript: Sendable {
    public private(set) var pieces: [SpeechPiece] = []
    public init() {}
    public mutating func receive(_ piece: SpeechPiece) {
        guard piece.start.isFinite, piece.end.isFinite, piece.end >= piece.start else { return }
        let overlaps: (SpeechPiece) -> Bool = {
            abs($0.start - piece.start) < 0.001 || ($0.start < piece.end && $0.end > piece.start)
        }
        // Once committed, an older volatile guess must not undo it.
        if !piece.isFinal && pieces.contains(where: { $0.isFinal && overlaps($0) }) { return }
        pieces.removeAll(where: overlaps)
        pieces.append(piece)
        pieces.sort { $0.start < $1.start }
    }
    public var text: String { pieces.reduce("") { Self.join($0, $1.text) } }
    public var finalText: String { pieces.filter(\.isFinal).reduce("") { Self.join($0, $1.text) } }
    private static func join(_ left: String, _ right: String) -> String {
        guard !left.isEmpty else { return right.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard !right.isEmpty else { return left }
        let trimmed = right.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let a = left.last, let b = trimmed.first else { return left }
        let latin: (Character) -> Bool = { $0.isASCII && ($0.isLetter || $0.isNumber) }
        return left + (latin(a) && latin(b) ? " " : "") + trimmed
    }
}

public enum WordRuleMode: String, Codable, CaseIterable, Sendable {
    case automatic, suggestion
    public var title: String { self == .automatic ? "自動置換" : "選択候補" }
}

public struct WordRule: Codable, Equatable, Identifiable, Sendable {
    public var id: UUID
    public var heard: String
    public var written: String
    public var bundleID: String?
    public var matchKana: Bool
    public var mode: WordRuleMode
    public init(heard: String, written: String, id: UUID = UUID(), bundleID: String? = nil, matchKana: Bool = false, mode: WordRuleMode = .automatic) {
        self.id = id; self.heard = heard; self.written = written
        self.bundleID = bundleID?.isEmpty == true ? nil : bundleID; self.matchKana = matchKana; self.mode = mode
    }
    private enum CodingKeys: String, CodingKey { case id, heard, written, bundleID, matchKana, mode }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.init(heard: try values.decode(String.self, forKey: .heard),
            written: try values.decode(String.self, forKey: .written),
            id: try values.decode(UUID.self, forKey: .id),
            bundleID: try values.decodeIfPresent(String.self, forKey: .bundleID),
            matchKana: try values.decodeIfPresent(Bool.self, forKey: .matchKana) ?? false,
            mode: try values.decodeIfPresent(WordRuleMode.self, forKey: .mode) ?? .automatic)
    }
    /// Snapshot only the target application's vocabulary, before any model work.
    public static func resolve(_ rules: [WordRule], bundleID: String) -> [WordRule] {
        let local = rules.filter { $0.bundleID == bundleID && !bundleID.isEmpty }
        // An app-specific choice of mode also overrides the shared rule for that key.
        return local + rules.filter { shared in
            shared.bundleID == nil && !local.contains { $0.hasSameReading(as: shared) }
        }
    }
    public func hasSameKey(as other: WordRule) -> Bool {
        guard bundleID == other.bundleID else { return false }
        return hasSameReading(as: other)
    }
    private func hasSameReading(as other: WordRule) -> Bool {
        if heard.caseInsensitiveCompare(other.heard) == .orderedSame { return true }
        return matchKana && other.matchKana && GeographyDictionary.normalize(heard) == GeographyDictionary.normalize(other.heard)
    }
    /// Expand only explicitly enabled, all-kana readings. No fuzzy/homophone guessing.
    public var heardForms: [String] {
        guard matchKana, !heard.isEmpty, heard.unicodeScalars.allSatisfy({ scalar in
            (0x3041...0x3096).contains(scalar.value) || (0x3099...0x309A).contains(scalar.value) ||
            (0x30A1...0x30FA).contains(scalar.value) || scalar.value == 0x30FC ||
            (0xFF66...0xFF9F).contains(scalar.value) || scalar.value == 0x20
        }) else { return [heard] }
        let hiragana = GeographyDictionary.normalize(heard).precomposedStringWithCanonicalMapping
        let katakana = hiragana.applyingTransform(.hiraganaToKatakana, reverse: false) ?? hiragana
        let halfWidth = katakana.applyingTransform(.fullwidthToHalfwidth, reverse: false) ?? katakana
        var seen = Set<String>()
        return [heard, hiragana, katakana, halfWidth].filter { seen.insert($0).inserted }
    }
}

public enum TextFormatter {
    /// Prepare once per dictation, then reuse for every volatile transcript revision.
    public struct Prepared {
        private let mappings: [WordRule]
        private let regex: NSRegularExpression?
        public init(rules: [WordRule], commands: Bool = false) {
            var mappings = rules.filter { $0.mode == .automatic && !$0.heard.isEmpty && !$0.written.isEmpty }.flatMap { rule in
                rule.heardForms.map { WordRule(heard: $0, written: rule.written) }
            }
            if commands {
                mappings += [WordRule(heard: "段落を変えて", written: "\n\n"), WordRule(heard: "改行して", written: "\n")]
            }
            mappings = mappings.enumerated().sorted {
                $0.element.heard.count == $1.element.heard.count ? $0.offset < $1.offset : $0.element.heard.count > $1.element.heard.count
            }.map(\.element)
            var seen = Set<String>()
            mappings = mappings.filter { seen.insert($0.heard.lowercased()).inserted }
            self.mappings = mappings
            let parts = mappings.map { rule -> String in
                let word = NSRegularExpression.escapedPattern(for: rule.heard)
                if rule.heard.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == " ") }) {
                    return "(?<![A-Za-z0-9_])" + word + "(?![A-Za-z0-9_])"
                }
                return word
            }
            regex = parts.isEmpty ? nil : try? NSRegularExpression(pattern: parts.joined(separator: "|"), options: [.caseInsensitive])
        }
        /// One pass: replacement output can never match another dictionary entry.
        public func format(_ text: String) -> String {
            guard let regex else { return text }
            let source = text as NSString
            let result = NSMutableString(string: text)
            for match in regex.matches(in: text, range: NSRange(location: 0, length: source.length)).reversed() {
                let heard = source.substring(with: match.range)
                if let rule = mappings.first(where: { $0.heard.caseInsensitiveCompare(heard) == .orderedSame }) {
                    result.replaceCharacters(in: match.range, with: rule.written)
                }
            }
            return result as String
        }
    }
    public static func format(_ text: String, rules: [WordRule], commands: Bool = false) -> String {
        Prepared(rules: rules, commands: commands).format(text)
    }
}

/// Computes the expected paste result using AX UTF-16 selections.
public struct TextInsertion: Sendable {
    public let prefix: String
    public let suffix: String
    public private(set) var inserted = ""
    public init?(original: String, selection: NSRange) {
        let ns = original as NSString
        guard selection.location >= 0, selection.length >= 0,
              selection.location <= ns.length, selection.length <= ns.length - selection.location else { return nil }
        prefix = ns.substring(to: selection.location)
        suffix = ns.substring(from: selection.location + selection.length)
        inserted = ns.substring(with: selection)
    }
    public var expected: String { prefix + inserted + suffix }
    public mutating func commit(_ text: String) { inserted = text }
}
