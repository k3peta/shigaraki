/// The visible editor's keyboard context. Finishing permits only a double tap.
public enum WindowInputMode: Equatable, Sendable {
    case inactive, idle, recording, finishing
}

/// Value-only single/double classifier; physical input and timers belong to the adapter.
public struct WindowInputGesture: Sendable {
    public enum Action: Equatable, Sendable { case none, singleTap, doubleTap }
    public static let doubleInterval = 0.35
    public static let shortInterval = 0.23
    public static let releaseInterval = 0.07
    public private(set) var pressedAt: Double?
    public private(set) var blocked = false
    private var releasedAt: Double?
    private var firstRelease: Double?
    private var waitingForUp = false
    private var pendingSingle = false
    private var pressCanToggle = false
    private var pressRecording = false
    private var second = false
    private var lastTime: Double?

    public init() {}
    public var needsPolling: Bool { waitingForUp || pressedAt != nil || firstRelease != nil }

    public mutating func reset(blockUntilUp: Bool = false) {
        pressedAt = nil; releasedAt = nil; firstRelease = nil
        blocked = blockUntilUp; waitingForUp = blockUntilUp
        pendingSingle = false; pressCanToggle = false; pressRecording = false
        second = false; lastTime = nil
    }

    public mutating func interaction() {
        clearTap()
        if pressedAt != nil { blocked = true }
    }

    private mutating func clearTap() { firstRelease = nil; pendingSingle = false }

    private mutating func expire(time: Double, canToggle: Bool, recording: Bool) -> Action {
        guard let firstRelease, time - firstRelease > Self.doubleInterval else { return .none }
        let emit = pendingSingle && canToggle && !recording
        clearTap()
        return emit ? .singleTap : .none
    }

    private mutating func finishPress(canToggle: Bool, recording: Bool) -> Action {
        guard let pressedAt, let releasedAt else { return .none }
        let short = releasedAt - pressedAt < Self.shortInterval
        let isSecond = second && short && firstRelease != nil
        let eligible = pressCanToggle && canToggle && pressRecording == recording
        let wasBlocked = blocked
        self.pressedAt = nil; self.releasedAt = nil; blocked = false; second = false
        clearTap()
        guard !wasBlocked else { return .none }
        if isSecond { return .doubleTap }
        guard eligible else {
            if short && !recording && pressRecording == recording { firstRelease = releasedAt }
            return .none
        }
        guard short else { return .singleTap }
        firstRelease = releasedAt; pendingSingle = !recording
        return recording ? .singleTap : .none
    }

    public mutating func update(targetDown: Bool, isolated: Bool, time: Double,
                                canToggle: Bool, recording: Bool) -> Action {
        guard time.isFinite, lastTime.map({ time >= $0 }) ?? true else {
            reset(blockUntilUp: targetDown); return .none
        }
        lastTime = time
        if waitingForUp {
            if targetDown { releasedAt = nil }
            else if releasedAt == nil { releasedAt = time }
            else if let releasedAt, time - releasedAt >= Self.releaseInterval {
                self.releasedAt = nil; waitingForUp = false; blocked = false
            }
            return .none
        }
        if !isolated { interaction() }
        if pendingSingle && (!canToggle || recording) { clearTap() }
        if pressedAt != nil && (!canToggle || recording != pressRecording) { pressCanToggle = false }
        var action: Action = .none
        if let releasedAt, time - releasedAt >= Self.releaseInterval {
            action = finishPress(canToggle: canToggle, recording: recording)
        }
        if targetDown {
            if pressedAt == nil {
                let expired = expire(time: time, canToggle: canToggle, recording: recording)
                if action == .none { action = expired }
                pressedAt = time; pressCanToggle = canToggle; pressRecording = recording
                second = firstRelease.map { time - $0 <= Self.doubleInterval } ?? false
                blocked = !isolated
            }
            releasedAt = nil
        } else if pressedAt != nil {
            if releasedAt == nil { releasedAt = time }
        } else {
            let expired = expire(time: time, canToggle: canToggle, recording: recording)
            if action == .none { action = expired }
        }
        return action
    }
}
