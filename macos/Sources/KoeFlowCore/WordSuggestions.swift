import Foundation

/// An occurrence, not a document-wide replacement. The snapshot rejects stale menus.
public struct WordSuggestionMatch: Equatable, Sendable {
    public let range: NSRange
    public let source: String
    public let replacements: [String]
    public let snapshot: String
    public var key: String { "\(range.location):\(range.length):\(source)" }
    public func applying(_ replacement: String, to current: String) -> String? {
        guard current == snapshot, replacements.contains(replacement),
              let span = Range(range, in: current), String(current[span]) == source else { return nil }
        var result = current; result.replaceSubrange(span, with: replacement)
        return result.count <= 200_000 ? result : nil
    }
}

public enum WordSuggestions {
    /// Local dictionary candidates only. No inferred substitutions or model calls.
    public static func matches(in text: String, rules: [WordRule], limit: Int = 200) -> [WordSuggestionMatch] {
        guard !text.isEmpty, text.count <= 100_000, limit > 0 else { return [] }
        let mappings = rules.filter { $0.mode == .suggestion && !$0.heard.isEmpty && !$0.written.isEmpty }
            .flatMap { rule in rule.heardForms.map { (heard: $0, written: rule.written) } }
        var seen = Set<String>()
        let readings = mappings.map(\.heard).sorted { $0.count > $1.count }
            .filter { seen.insert($0.lowercased()).inserted }
        guard !readings.isEmpty else { return [] }
        let patterns = readings.map { reading -> String in
            let escaped = NSRegularExpression.escapedPattern(for: reading)
            if reading.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == " ") }) {
                return "(?<![A-Za-z0-9_])" + escaped + "(?![A-Za-z0-9_])"
            }
            return escaped
        }
        guard let regex = try? NSRegularExpression(pattern: patterns.joined(separator: "|"), options: [.caseInsensitive]) else { return [] }
        let source = text as NSString
        var matches: [WordSuggestionMatch] = []
        regex.enumerateMatches(in: text, range: NSRange(location: 0, length: source.length)) { match, _, stop in
            guard let match, Range(match.range, in: text) != nil else { return }
            let word = source.substring(with: match.range)
            var choices = Set<String>()
            let replacements = mappings.filter { $0.heard.caseInsensitiveCompare(word) == .orderedSame }
                .map(\.written).filter { $0 != word && choices.insert($0).inserted }
            if !replacements.isEmpty {
                matches.append(WordSuggestionMatch(range: match.range, source: word, replacements: replacements, snapshot: text))
            }
            if matches.count >= limit { stop.pointee = true }
        }
        return matches
    }
}
