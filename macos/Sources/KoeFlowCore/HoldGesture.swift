/// Separates arming a hold gesture from stopping an already active gesture.
public struct HoldGesture: Sendable {
    public enum Action: Equatable, Sendable { case none, arm, cancelPending, start, confirmRelease, stop }
    public enum State: Equatable, Sendable { case idle, pending, recording }
    public private(set) var state: State = .idle
    public init() {}
    public mutating func modifierChanged(isTarget: Bool, targetDown: Bool, onlyTarget: Bool) -> Action {
        switch state {
        case .recording:
            // A modifier event is a release candidate, not proof that the key is up.
            guard isTarget && !targetDown else { return .none }
            return .confirmRelease
        case .pending:
            guard isTarget && targetDown && onlyTarget else {
                state = .idle; return .cancelPending
            }
            return .none
        case .idle:
            guard isTarget && targetDown && onlyTarget else { return .none }
            state = .pending; return .arm
        }
    }
    public mutating func interaction() -> Action {
        guard state == .pending else { return .none }
        state = .idle; return .cancelPending
    }
    public mutating func holdElapsed() -> Action {
        guard state == .pending else { return .none }
        state = .recording; return .start
    }
    public mutating func releaseConfirmed(targetDown: Bool) -> Action {
        guard state == .recording, !targetDown else { return .none }
        state = .idle; return .stop
    }
    public mutating func reset() -> Action {
        let action: Action = state == .recording ? .stop : state == .pending ? .cancelPending : .none
        state = .idle; return action
    }
}
