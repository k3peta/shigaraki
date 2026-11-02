/// Rejects recognition from silent sessions. This is an energy floor, not speaker detection.
public struct AudioActivity: Sendable {
    public private(set) var audibleSeconds: Double = 0
    public var hasAudibleInput: Bool { audibleSeconds >= 0.1 }
    public init() {}
    public mutating func receive(rms: Float, frames: Int, sampleRate: Double) {
        guard rms.isFinite, rms >= 0.001, frames > 0, sampleRate.isFinite, sampleRate > 0 else { return }
        audibleSeconds = min(0.1, audibleSeconds + Double(frames) / sampleRate)
    }
}
