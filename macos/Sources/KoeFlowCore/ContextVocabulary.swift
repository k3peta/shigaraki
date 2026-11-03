import Foundation

/// A fresh selection from the resolved dictionary, never a learned or persistent topic.
public enum ContextVocabulary {
    public struct Entry: Codable, Equatable, Sendable {
        public let written: String
        public let readings: [String]
    }

    private static func valid(_ word: String) -> Bool {
        !word.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && word.count <= 80 &&
            !word.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }
    private static func contains(_ text: String, _ word: String) -> Bool {
        guard valid(word), word.count >= 2 else { return false }
        if word.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == " ") }) {
            let pattern = "(?<![A-Za-z0-9_])" + NSRegularExpression.escapedPattern(for: word) + "(?![A-Za-z0-9_])"
            return text.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
        }
        return text.range(of: word, options: .caseInsensitive) != nil
    }
    private static func reference(_ context: WritingContext) -> String {
        // The selected text is being replaced; it must not steer the next topic.
        [context.windowTitle, context.fieldHint, context.before, context.after, context.recentPages, context.supplement ?? ""].joined(separator: "\n")
    }
    private static func relevant(_ rule: WordRule, to text: String) -> Bool {
        ([rule.written] + rule.heardForms).contains { contains(text, $0) }
    }
    public static func topicReference(in text: String, rules: [WordRule]) -> String {
        let terms = rules.filter { $0.mode == .automatic && valid($0.written) && contains(text, $0.written) }
            .map(\.written).sorted { $0.count > $1.count }
        var chosen: [String] = []
        for term in terms where !chosen.contains(where: { $0.contains(term) }) {
            guard (chosen + [term]).joined(separator: "、").count <= 80 else { continue }
            chosen.append(term)
            if chosen.count == 8 { break }
        }
        return chosen.joined(separator: "、")
    }
    public static func recognitionHints(rules: [WordRule], context: WritingContext, limit: Int = 64) -> [String] {
        let candidates = rules.filter { $0.mode == .automatic && valid($0.written) }
        let text = reference(context)
        let ranked = candidates.filter { relevant($0, to: text) } + candidates.filter { !relevant($0, to: text) }
        var seen = Set<String>()
        return Array(ranked.map(\.written).filter { seen.insert($0).inserted }.prefix(max(0, min(limit, 64))))
    }
    public static func glossary(rules: [WordRule], original: String, context: WritingContext) -> [Entry] {
        let text = reference(context)
        let candidates = rules.filter { $0.mode == .automatic && valid($0.written) }
        let ranked = candidates.filter { relevant($0, to: original) } +
            candidates.filter { !relevant($0, to: original) && relevant($0, to: text) }
        var result: [Entry] = [], seen = Set<String>(), characters = 0
        for rule in ranked {
            guard seen.insert(rule.written).inserted else { continue }
            let readings = Array(rule.heardForms.filter { valid($0) && $0 != rule.written }.prefix(3))
            let cost = rule.written.count + readings.reduce(0) { $0 + $1.count }
            guard characters + cost <= 600 else { continue }
            result.append(Entry(written: rule.written, readings: readings)); characters += cost
            if result.count == 16 { break }
        }
        return result.filter { entry in !result.contains { other in entry.written != other.written && other.written.contains(entry.written) } }
    }
    /// A reference is not an alternative draft. Prefer abstention to copying its
    /// predicate or emitting the reading instead of the registered spelling.
    public static func rejection(original: String, result: String, glossary: [Entry]) -> String? {
        guard !glossary.isEmpty else { return nil }
        for entry in glossary {
            for reading in entry.readings where !original.contains(reading) && result.contains(reading) {
                return "dictionaryReading"
            }
        }
        func wording(_ text: String) -> [Character] {
            Array(text.filter { !$0.isWhitespace && !$0.unicodeScalars.allSatisfy { CharacterSet.punctuationCharacters.contains($0) } })
        }
        let source = wording(original), revised = wording(result)
        let changes = revised.difference(from: source).count
        return changes > max(4, min(8, source.count / 5)) ? "contextRewrite" : nil
    }
}
