public enum WindowShortcut: String, CaseIterable, Sendable {
    case controlDoubleTap, optionDoubleTap, none
    public var title: String {
        switch self {
        case .controlDoubleTap: return "左Controlを2回（標準）"
        case .optionDoubleTap: return "左Optionを2回"
        case .none: return "ショートカットを使わない"
        }
    }
    public var keyCode: UInt16? {
        switch self { case .controlDoubleTap: return 59; case .optionDoubleTap: return 58; case .none: return nil }
    }
}

/// Two short, isolated taps; holds, typing, chords, and active input invalidate the sequence.
public struct WindowTapGesture: Sendable {
    private var pressedAt: Double?
    private var firstRelease: Double?
    public init() {}
    public mutating func reset() { pressedAt = nil; firstRelease = nil }
    public mutating func update(isTarget: Bool, targetDown: Bool, isolated: Bool, time: Double, busy: Bool) -> Bool {
        guard !busy, isTarget, isolated, time.isFinite else { reset(); return false }
        if targetDown {
            if pressedAt == nil { pressedAt = time }
            return false
        }
        guard let pressedAt, time >= pressedAt, time - pressedAt < 0.23 else { reset(); return false }
        self.pressedAt = nil
        if let firstRelease, pressedAt >= firstRelease, pressedAt - firstRelease <= 0.35 {
            reset(); return true
        }
        firstRelease = time; return false
    }
}
