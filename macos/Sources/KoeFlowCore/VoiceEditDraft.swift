import Foundation

/// A local edit transaction. It never inserts into an external application.
public struct VoiceEditDraft: Sendable {
    public let original: String
    public let selection: NSRange
    public var wholeDocument: Bool { selection.length == 0 }
    public var target: String {
        wholeDocument ? original : String(original[Range(selection, in: original)!])
    }
    public init(text: String, selection: NSRange) throws {
        guard text.count <= 100_000 else { throw VoiceEditError.tooLong }
        guard let range = Range(selection, in: text),
              (range.lowerBound == text.endIndex || text.indices.contains(range.lowerBound)),
              (range.upperBound == text.endIndex || text.indices.contains(range.upperBound)) else { throw VoiceEditError.sourceChanged }
        self.original = text; self.selection = selection
        guard !target.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw VoiceEditError.emptySelection }
    }
    public func applying(_ replacement: String, to current: String) throws -> String {
        guard current == original else { throw VoiceEditError.sourceChanged }
        var result = original
        if wholeDocument { result = replacement }
        else { result.replaceSubrange(Range(selection, in: result)!, with: replacement) }
        guard result.count <= 200_000 else { throw VoiceEditError.tooLong }
        return result
    }
}
