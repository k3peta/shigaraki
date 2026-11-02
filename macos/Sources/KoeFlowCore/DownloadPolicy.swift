import Foundation

public enum DownloadPolicy {
    public static func permits(_ url: URL) -> Bool {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return false }
        return c.scheme?.lowercased() == "https" && !(c.host ?? "").isEmpty &&
            c.user == nil && c.password == nil && c.fragment == nil
    }
    /// File operations also reject symlinks at runtime. This check never grants ownership to an arbitrary cache.
    public static func mayRepair(_ candidate: URL, ownedURL: URL) -> Bool {
        candidate.isFileURL && ownedURL.isFileURL &&
            candidate.standardizedFileURL.path == ownedURL.standardizedFileURL.path &&
            candidate.resolvingSymlinksInPath().path == ownedURL.standardizedFileURL.path
    }
}

/// Counts the decoded bytes that will reach disk, independent of Content-Length or compression.
public struct DownloadByteBudget: Sendable {
    public let maximum: Int64
    public private(set) var received: Int64 = 0
    public init(maximum: Int64) { self.maximum = max(0, maximum) }
    public func permitsDeclaredLength(_ length: Int64) -> Bool { length < 0 || length <= maximum }
    /// A rejected chunk leaves the counter unchanged and must never be written.
    public mutating func accept(_ count: Int) -> Bool {
        guard count >= 0, Int64(count) <= maximum - received else { return false }
        received += Int64(count); return true
    }
    public var complete: Bool { received == maximum }
}
