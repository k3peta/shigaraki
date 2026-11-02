import Foundation

public struct HaasEvent: Decodable, Sendable {
    public let type: String
    public let id: UInt64?
    public let text: String?
    public let `final`: Bool?
    public let code: String?
    public let error: String?
    public let `protocol`: Int?
    public let engine: String?
    public let device: String?
    public let threads: Int?
    public static let maximumLineBytes = 1_048_576
    public static func decode(_ data: Data) throws -> HaasEvent {
        guard !data.isEmpty, data.count <= maximumLineBytes else { throw HaasProtocolError.invalidMessage }
        let event = try JSONDecoder().decode(Self.self, from: data)
        guard ["ready", "caption", "complete", "cancelled", "error", "fatal"].contains(event.type) else {
            throw HaasProtocolError.invalidMessage
        }
        if event.type == "ready" {
            guard event.protocol == 1, event.engine == "haas-rust", event.device == "CPU",
                  (1...8).contains(event.threads ?? 0) else { throw HaasProtocolError.invalidMessage }
        } else if event.type != "fatal" {
            guard (event.id ?? 0) > 0 else { throw HaasProtocolError.invalidMessage }
        }
        return event
    }
}

public enum HaasProtocolError: Error { case invalidMessage, emptyFinal, busy }

public struct HaasTranscriptState: Sendable {
    public private(set) var session: UInt64?
    public private(set) var text = ""
    public private(set) var finalText = ""
    public private(set) var events = 0
    public private(set) var volatileEvents = 0
    public init() {}
    public mutating func begin(_ id: UInt64) throws {
        guard session == nil, id > 0 else { throw HaasProtocolError.busy }
        session = id; text = ""; finalText = ""; events = 0; volatileEvents = 0
    }
    /// Returns false for old sessions. A failed final preserves the visible draft and remains an error.
    public mutating func receive(_ event: HaasEvent) throws -> Bool {
        guard event.id == session, session != nil else { return false }
        if event.type == "caption" || event.type == "complete" {
            guard let value = event.text else { throw HaasProtocolError.invalidMessage }
            if event.type == "complete" && value.isEmpty && !text.isEmpty { throw HaasProtocolError.emptyFinal }
            text = value; events += 1
            if event.type == "complete" || event.final == true { finalText = value }
            else { volatileEvents += 1 }
        }
        if event.type == "error", let value = event.text, !value.isEmpty { text = value }
        if ["complete", "cancelled", "error"].contains(event.type) { session = nil }
        return true
    }
    public mutating func cancel() { session = nil }
}
