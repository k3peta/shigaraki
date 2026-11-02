import Foundation

/// Foreground page titles observed since launch. Never persisted or used as instructions.
public struct PageContext {
    private var pages: [(title: String, at: TimeInterval)] = []
    public init() {}
    public mutating func clear() { pages.removeAll() }
    public static func excluded(_ title: String) -> Bool {
        let text = title.lowercased()
        return ["incognito", "inprivate", "private browsing", "private window", "シークレット", "プライベート", "password", "パスワード"].contains { text.contains($0) }
    }
    public mutating func observe(title: String, now: TimeInterval, blocked: Bool = false) {
        guard !blocked, !Self.excluded(title) else { clear(); return }
        let clean = String(title.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.map(String.init).joined().prefix(160))
        guard !clean.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        pages.removeAll { $0.title == clean || now - $0.at < 0 || now - $0.at > 180 }
        pages.insert((clean, now), at: 0)
        pages = Array(pages.prefix(3))
    }
    public mutating func reference(now: TimeInterval) -> String {
        pages.removeAll { now - $0.at < 0 || now - $0.at > 180 }
        return String(pages.map(\.title).joined(separator: "\n").prefix(240))
    }
}
