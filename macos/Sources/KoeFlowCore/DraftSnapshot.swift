import Foundation

/// Recovery state is independent of the JSONL history and carries no target
/// handle. Restoring text can never restore permission to send it elsewhere.
public struct DraftSnapshot: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let text: String
    public let rawText: String
    public let original: String
    public let pending: Bool
    public init(text: String, rawText: String, original: String, pending: Bool) {
        self.schemaVersion = 1; self.text = text; self.rawText = rawText
        self.original = original; self.pending = pending
    }
    public func encoded() throws -> String {
        let data = try JSONEncoder().encode(self)
        guard data.count <= 1_048_576, let value = String(data: data, encoding: .utf8) else {
            throw CocoaError(.fileWriteUnknown)
        }
        return value
    }
    public static func decode(_ value: String) throws -> Self {
        guard value.utf8.count <= 1_048_576 else { throw CocoaError(.fileReadTooLarge) }
        let result = try JSONDecoder().decode(Self.self, from: Data(value.utf8))
        guard result.schemaVersion == 1 else { throw CocoaError(.fileReadCorruptFile) }
        return result
    }
}
