import Foundation

/// One verified field snapshot. RAM only; a read never extends its lifetime.
public struct RecentContext<Key: Equatable> {
    private var key: Key?
    private var text = ""
    private var recordedAt: TimeInterval = 0
    public let lifetime: TimeInterval
    public init(lifetime: TimeInterval = 180) { self.lifetime = lifetime }
    public mutating func clear() { key = nil; text = ""; recordedAt = 0 }
    public mutating func remember(key: Key?, text: String, now: TimeInterval) {
        guard let key, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, now.isFinite else { clear(); return }
        self.key = key; self.text = String(text.trimmingCharacters(in: .whitespacesAndNewlines).suffix(80)); recordedAt = now
    }
    public mutating func reference(for key: Key?, now: TimeInterval) -> String {
        let elapsed = now - recordedAt
        guard let key, key == self.key, now.isFinite, elapsed >= 0, elapsed <= lifetime else { clear(); return "" }
        return text
    }
    /// A confirmed paste advances the snapshot, but does not refresh an unspoken topic.
    public mutating func advance(to key: Key?, now: TimeInterval) {
        let elapsed = now - recordedAt
        guard let key, self.key != nil, now.isFinite, elapsed >= 0, elapsed <= lifetime else { clear(); return }
        self.key = key
    }
}
