/// Modifier-only shortcuts commit on release, so Ctrl+Shift+letter never starts dictation.
public struct ChordGesture: Sendable {
    public private(set) var armed = false
    private var blocked = false
    private var previousChord: UInt8 = 0
    public init() {}
    public mutating func update(modifiers: UInt8, chord: UInt8) -> Bool {
        if previousChord != chord { armed = false; blocked = false; previousChord = chord }
        guard chord != 0 else { armed = false; blocked = false; return false }
        if modifiers == 0 {
            let fire = armed && !blocked
            armed = false; blocked = false
            return fire
        }
        if modifiers & ~chord != 0 { armed = false; blocked = true }
        if modifiers == chord && !blocked { armed = true }
        return false
    }
    public mutating func interaction() { armed = false; blocked = true }
}

public enum ContinuousShortcut: String, CaseIterable, Sendable {
    case controlShift, controlOption, commandShift, controlShiftSpace, none
    public var title: String {
        switch self {
        case .controlShift: return "Control＋Shift（標準）"
        case .controlOption: return "Control＋Option"
        case .commandShift: return "Command＋Shift"
        case .controlShiftSpace: return "Control＋Shift＋Space"
        case .none: return "ショートカットを使わない"
        }
    }
    public var symbol: String {
        switch self {
        case .controlShift: return "⌃⇧"
        case .controlOption: return "⌃⌥"
        case .commandShift: return "⌘⇧"
        case .controlShiftSpace: return "⌃⇧Space"
        case .none: return "ボタン"
        }
    }
    // Kept independent of AppKit so conflict and release behavior can be tested.
    public var modifierMask: UInt8 {
        switch self {
        case .controlShift: return 3
        case .controlOption: return 5
        case .commandShift: return 10
        default: return 0
        }
    }
}
