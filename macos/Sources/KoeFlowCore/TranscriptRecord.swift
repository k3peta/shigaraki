import Foundation

/// Persistent destination metadata; never contains surrounding editor text or a full URL.
public struct LogDestination: Codable, Equatable, Sendable {
    public var bundleID: String
    public var appName: String
    public var windowTitle: String
    public var siteHost: String?
    public init(bundleID: String = "", appName: String = "", windowTitle: String = "", siteURL: String? = nil) {
        self.bundleID = String(bundleID.prefix(160))
        self.appName = String(appName.prefix(160))
        let title = String(windowTitle.prefix(160))
        let source = title as NSString
        let cleaned = NSMutableString(string: title)
        // Titles sometimes contain a URL instead of a document name.
        if let regex = try? NSRegularExpression(pattern: #"https?://[^\s<>]+"#, options: [.caseInsensitive]) {
            for match in regex.matches(in: title, range: NSRange(location: 0, length: source.length)).reversed() {
                cleaned.replaceCharacters(in: match.range, with: Self.host(from: source.substring(with: match.range)) ?? "[URL]")
            }
        }
        self.windowTitle = cleaned as String
        self.siteHost = siteURL.flatMap { Self.host(from: $0) }
    }
    public static func host(from url: String) -> String? {
        guard url.utf8.count <= 4096, let parsed = URLComponents(string: url),
              ["https", "http"].contains(parsed.scheme?.lowercased() ?? ""),
              let host = parsed.host?.lowercased(), !host.isEmpty,
              !host.contains(where: { $0.isWhitespace || $0.isNewline }) else { return nil }
        return host
    }
}

public enum TransferOutcome: String, Codable, Sendable {
    case confirmed, unconfirmed, notAttempted, cancelled
    public var title: String {
        switch self {
        case .confirmed: return "入力欄への転記を確認"
        case .unconfirmed: return "転記未確認（入力欄を確認してください）"
        case .notAttempted: return "認識のみ（転記していません）"
        case .cancelled: return "中止（転記していません）"
        }
    }
}

/// One JSONL row per dictation or retry. Retries reference the original dictation ID.
public struct TranscriptRecord: Codable, Equatable, Sendable {
    public enum EditScope: String, Codable, Sendable { case selection, document }
    public enum Kind: String, Codable, Sendable { case dictation, retry, voiceEdit }
    public var editScope: EditScope?
    public var editedCharacters: Int?
    public var schemaVersion = 1
    public var id: UUID
    public var dictationID: UUID
    public var kind: Kind
    public var startedAt: Date
    public var timestamp: Date
    public var timeZoneID: String
    public var destination: LogDestination
    public var outcome: TransferOutcome
    public var partial: Bool
    public var recognized: String
    public var final: String
    public var purpose: WritingPurpose
    public var cardName: String
    public init(id: UUID = UUID(), dictationID: UUID? = nil, kind: Kind = .dictation,
                startedAt: Date, timestamp: Date = Date(), timeZone: TimeZone = .current,
                destination: LogDestination = .init(), outcome: TransferOutcome = .notAttempted,
                partial: Bool = false, recognized: String = "", final: String = "",
                purpose: WritingPurpose = .plain, cardName: String = "", editScope: EditScope? = nil, editedCharacters: Int? = nil) {
        self.editScope = editScope; self.editedCharacters = editedCharacters
        self.schemaVersion = kind == .voiceEdit ? 2 : 1
        self.id = id; self.dictationID = dictationID ?? id; self.kind = kind
        self.startedAt = startedAt; self.timestamp = timestamp; self.timeZoneID = timeZone.identifier
        self.destination = destination; self.outcome = outcome; self.partial = partial
        self.recognized = recognized; self.final = final; self.purpose = purpose
        self.cardName = String(cardName.prefix(160))
    }
}
