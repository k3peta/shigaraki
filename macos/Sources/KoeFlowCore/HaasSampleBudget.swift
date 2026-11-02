/// A sample boundary independent of UI timers. Rust accepts at most 305 seconds; capture seals at 300.
public struct HaasSampleBudget: Sendable {
    public static let maximum = 300 * 16000
    public private(set) var accepted = 0
    public var exhausted: Bool { accepted == Self.maximum }
    public init() {}
    public mutating func take(_ requested: Int) -> Int {
        guard requested > 0 else { return 0 }
        let count = min(requested, Self.maximum - accepted)
        accepted += count
        return count
    }
}
