import AVFoundation
import Foundation
import KoeFlowCore

final class HaasAudioStatistics: @unchecked Sendable {
    private let lock = NSLock()
    private var activity = AudioActivity()
    private var maximum: Float = 0
    var audibleSeconds: Double { lock.lock(); defer { lock.unlock() }; return activity.audibleSeconds }
    var maximumRMS: Float { lock.lock(); defer { lock.unlock() }; return maximum }
    var hasAudibleInput: Bool { lock.lock(); defer { lock.unlock() }; return activity.hasAudibleInput }
    func receive(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return 0 }
        var energy: Double = 0
        for i in 0..<Int(buffer.frameLength) { energy += Double(samples[i]) * Double(samples[i]) }
        let rms = Float(sqrt(energy / Double(buffer.frameLength)))
        lock.lock()
        activity.receive(rms: rms, frames: Int(buffer.frameLength), sampleRate: buffer.format.sampleRate)
        if rms.isFinite { maximum = max(maximum, rms) }
        lock.unlock()
        guard rms.isFinite, rms > 0 else { return 0 }
        return min(1, max(0, (20 * log10(rms) + 60) / 48))
    }
}

/// Owns tap buffers before returning; only this queue converts and packs them. Accepted buffers precede the stop barrier.
final class HaasAudioIngress: @unchecked Sendable {
    private let queue = DispatchQueue(label: "jp.ys.shigaraki.haas.audio", qos: .userInitiated)
    private let lock = NSLock()
    private var accepting = true
    private var pending = 0
    private var error: Error?
    private var budget = HaasSampleBudget()
    private var sealed = false
    var limitReached: Bool { lock.lock(); defer { lock.unlock() }; return sealed }
    private let writer: HaasAudioWriter
    private let converter: AudioConverter
    private let statistics: HaasAudioStatistics
    private let meter: @Sendable (Float) -> Void
    private let failure: @Sendable (String) -> Void
    private let limit: @Sendable () -> Void
    init(writer: HaasAudioWriter, converter: AudioConverter, statistics: HaasAudioStatistics,
         meter: @escaping @Sendable (Float) -> Void, failure: @escaping @Sendable (String) -> Void,
         limit: @escaping @Sendable () -> Void) {
        self.writer = writer; self.converter = converter; self.statistics = statistics; self.meter = meter; self.failure = failure; self.limit = limit
    }
    func offer(_ input: AVAudioPCMBuffer) {
        guard input.frameLength > 0 else { return }
        lock.lock()
        guard accepting else { lock.unlock(); return }
        guard pending < 64, let owned = AVAudioPCMBuffer(pcmFormat: input.format, frameCapacity: input.frameLength) else {
            accepting = false; error = FlowError.message("音声認識が入力に追いつきません。一度停止してください。")
            lock.unlock(); failure("音声認識が入力に追いつきません。一度停止してください。"); return
        }
        owned.frameLength = input.frameLength
        let source = UnsafeMutableAudioBufferListPointer(input.mutableAudioBufferList)
        let destination = UnsafeMutableAudioBufferListPointer(owned.mutableAudioBufferList)
        guard source.count == destination.count,
              zip(source, destination).allSatisfy({ $0.mDataByteSize >= $1.mDataByteSize }) else {
            accepting = false; error = FlowError.message("マイクの音声データが正しくありません。")
            lock.unlock(); failure("マイクの音声データが正しくありません。"); return
        }
        for i in 0..<source.count {
            if let from = source[i].mData, let to = destination[i].mData { memcpy(to, from, Int(destination[i].mDataByteSize)) }
        }
        pending += 1
        queue.async { [self] in
            defer { lock.lock(); pending -= 1; lock.unlock() }
            guard !writer.token.cancelled else { return }
            do { try send(converter.convert(owned)) }
            catch { fail(error) }
        }
        lock.unlock()
    }
    private func send(_ buffer: AVAudioPCMBuffer) throws {
        guard buffer.frameLength > 0, let samples = buffer.floatChannelData?[0] else { return }
        let frames = budget.take(Int(buffer.frameLength))
        guard frames > 0 else { return }
        var data = Data(count: frames * 2)
        try data.withUnsafeMutableBytes { raw in
            let output = raw.bindMemory(to: Int16.self)
            for i in 0..<frames {
                guard samples[i].isFinite else { throw FlowError.message("マイクの音声データが正しくありません。") }
                output[i] = Int16(min(1, max(-1, samples[i])) * 32767).littleEndian
            }
        }
        meter(statistics.receive(buffer)); writer.send(data)
        if budget.exhausted {
            lock.lock(); let first = !sealed; sealed = true; accepting = false; lock.unlock()
            if first { limit() }
        }
    }
    private func fail(_ value: Error) {
        lock.lock(); let first = error == nil; error = value; accepting = false; lock.unlock()
        if first { failure(value.localizedDescription) }
    }
    func finish() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            lock.lock(); accepting = false
            queue.async { [self] in
                do {
                    lock.lock(); let existing = error; lock.unlock()
                    if let existing { throw existing }
                    if !writer.token.cancelled { for buffer in try converter.finish() { try send(buffer) } }
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
            lock.unlock()
        }
    }
    func cancel() { lock.lock(); accepting = false; writer.cancelPending(); lock.unlock() }
}
