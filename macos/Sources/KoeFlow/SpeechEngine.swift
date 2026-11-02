import AVFoundation
import KoeFlowCore

enum FlowError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(s) = self { return s }; return nil }
}

/// Conversion is confined to the audio tap (one serial callback), or the file pump.
final class AudioConverter {
    let format: AVAudioFormat
    private let converter: AVAudioConverter
    init(from: AVAudioFormat, to: AVAudioFormat) throws {
        guard let converter = AVAudioConverter(from: from, to: to) else { throw FlowError.message("マイクの音声形式を変換できません。") }
        // The default converter can select only the first channel. Preserve speech
        // on either channel when the recognizer requires mono audio.
        if from.channelCount > 1 && to.channelCount == 1 { converter.downmix = true }
        self.converter = converter; self.format = to
    }
    func convert(_ input: AVAudioPCMBuffer) throws -> AVAudioPCMBuffer {
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * format.sampleRate / input.format.sampleRate) + 32)
        guard let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { throw FlowError.message("音声バッファを作成できません。") }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true; state.pointee = .haveData; return input
        }
        if let error { throw error }
        guard status != .error else { throw FlowError.message("音声の変換に失敗しました。") }
        return output
    }
    func finish() throws -> [AVAudioPCMBuffer] {
        var buffers: [AVAudioPCMBuffer] = []
        for _ in 0..<16 {
            guard let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 2048) else { throw FlowError.message("音声バッファを作成できません。") }
            var error: NSError?
            let status = converter.convert(to: output, error: &error) { _, state in state.pointee = .endOfStream; return nil }
            if let error { throw error }
            guard status != .error else { throw FlowError.message("音声の変換に失敗しました。") }
            if output.frameLength > 0 { buffers.append(output) }
            if status == .endOfStream || output.frameLength == 0 { return buffers }
        }
        throw FlowError.message("音声の末尾を処理できませんでした。")
    }
}

@MainActor final class SpeechEngine {
    var onText: ((String, String) -> Void)?
    var onLevel: ((Float) -> Void)?
    var onError: ((String) -> Void)?
    var onStatus: ((String) -> Void)?
    var onLimit: (() -> Void)?
    private let assets: HaasAssets
    private var audioEngine: AVAudioEngine?
    private var ingress: HaasAudioIngress?
    private var writer: HaasAudioWriter?
    private var transcript = HaasTranscriptState()
    private var statistics = HaasAudioStatistics()
    private var tapInstalled = false
    private var sessionID = UUID()
    private var recognitionError: Error?
    private(set) var eventCount = 0
    private(set) var volatileCount = 0
    private(set) var finalizationSeconds: TimeInterval = 0
    var maximumRMS: Float { statistics.maximumRMS }
    private(set) var inputChannelCount: UInt32 = 0
    var audibleInputSeconds: Double { statistics.audibleSeconds }
    var text: String { statistics.hasAudibleInput ? transcript.text : "" }
    init(assets: HaasAssets? = nil) { self.assets = assets ?? .shared }

    func prepare(localeID: String, hints: [String]) async throws -> AVAudioFormat {
        guard localeID.lowercased().hasPrefix("ja"), writer == nil else { throw FlowError.message("日本語の音声認識を準備できません。前の処理を確認してください。") }
        sessionID = UUID(); let generation = sessionID
        if !assets.isReady {
            onStatus?("Haas音声モデルを準備しています。")
            try await assets.prepare()
        }
        try Task.checkCancellation()
        guard sessionID == generation else { throw CancellationError() }
        transcript = HaasTranscriptState(); statistics = HaasAudioStatistics(); recognitionError = nil
        eventCount = 0; volatileCount = 0; finalizationSeconds = 0; inputChannelCount = 0
        let sender = try assets.worker.begin(keywords: hints) { [weak self] event in
            guard let self, self.sessionID == generation else { return }
            do {
                guard try self.transcript.receive(event) else { return }
                self.eventCount = self.transcript.events; self.volatileCount = self.transcript.volatileEvents
                if self.statistics.hasAudibleInput { self.onText?(self.transcript.text, self.transcript.finalText) }
                if event.type == "error" {
                    let error = FlowError.message("音声認識を完了できませんでした。途中の文章を保持します。")
                    self.recognitionError = error; self.onError?(error.localizedDescription)
                }
            } catch { self.recognitionError = error; self.onError?("音声認識の確定に失敗しました。途中の文章を保持します。") }
        }
        try transcript.begin(sender.id); writer = sender
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false) else {
            await cancel(); throw FlowError.message("音声の形式を準備できません。")
        }
        return format
    }
    private func makeIngress(from: AVAudioFormat, to: AVAudioFormat) throws -> HaasAudioIngress {
        guard let writer else { throw FlowError.message("認識エンジンが準備されていません。") }
        let generation = sessionID
        return HaasAudioIngress(writer: writer, converter: try AudioConverter(from: from, to: to), statistics: statistics,
            meter: { [weak self] level in Task { @MainActor [weak self] in if self?.sessionID == generation { self?.onLevel?(level) } } },
            failure: { [weak self] message in Task { @MainActor [weak self] in
                guard let self, self.sessionID == generation else { return }
                self.recognitionError = FlowError.message(message); self.onError?(message)
            } }, limit: { [weak self] in Task { @MainActor [weak self] in
                guard let self, self.sessionID == generation else { return }
                self.stopCapture(); self.onLimit?()
            } })
    }
    func startMicrophone(localeID: String, hints: [String]) async throws {
        guard await AVCaptureDevice.requestAccess(for: .audio) else { throw FlowError.message("マイクを許可してください。システム設定 → プライバシーとセキュリティ → マイクで変更できます。") }
        let format = try await prepare(localeID: localeID, hints: hints)
        do {
            let engine = AVAudioEngine(); let input = engine.inputNode
            let natural = input.outputFormat(forBus: 0); inputChannelCount = natural.channelCount
            guard natural.sampleRate > 0, natural.channelCount > 0 else { throw FlowError.message("マイクが見つかりません。接続と入力設定を確認してください。") }
            let ingress = try makeIngress(from: natural, to: format); self.ingress = ingress
            input.installTap(onBus: 0, bufferSize: 2048, format: natural) { buffer, _ in ingress.offer(buffer) }
            audioEngine = engine; tapInstalled = true; engine.prepare(); try engine.start()
        } catch { await cancel(); throw error }
    }
    func finish() async throws {
        let started = Date(); stopCapture()
        guard let writer else { if let recognitionError { throw recognitionError }; return }
        do {
            try await ingress?.finish(); ingress = nil
            try await assets.worker.finish(writer.id)
            if let recognitionError { throw recognitionError }
            self.writer = nil; finalizationSeconds = Date().timeIntervalSince(started)
        } catch {
            writer.cancelPending(); ingress?.cancel(); ingress = nil
            await assets.worker.cancel(writer.id); self.writer = nil
            finalizationSeconds = Date().timeIntervalSince(started); throw error
        }
    }
    func cancel() async {
        sessionID = UUID(); stopCapture(); ingress?.cancel(); ingress = nil
        let old = writer; writer = nil; old?.cancelPending(); transcript.cancel()
        if let old { await assets.worker.cancel(old.id) }
    }
    private func stopCapture() {
        audioEngine?.stop()
        if tapInstalled { audioEngine?.inputNode.removeTap(onBus: 0); tapInstalled = false }
        audioEngine = nil; onLevel?(0)
    }
    /// Real-time fixture pump; model preparation is shared with recording and never uses file batch recognition.
    func streamFile(_ url: URL, localeID: String, hints: [String] = []) async throws -> [String: Any] {
        let started = Date(); var firstResult: Double?
        let originalCallback = onText
        onText = { text, final in
            if firstResult == nil { firstResult = Date().timeIntervalSince(started) }
            originalCallback?(text, final)
        }
        defer { onText = originalCallback }
        let format = try await prepare(localeID: localeID, hints: hints)
        do {
            let file = try AVAudioFile(forReading: url); inputChannelCount = file.processingFormat.channelCount
            let input = try makeIngress(from: file.processingFormat, to: format); ingress = input
            let feedingStarted = Date()
            guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 2048) else { throw FlowError.message("音声ファイルを読み込めません。") }
            while file.framePosition < file.length && !input.limitReached {
                try Task.checkCancellation()
                if let recognitionError { throw recognitionError }
                try file.read(into: buffer); input.offer(buffer)
                try await Task.sleep(nanoseconds: UInt64(Double(buffer.frameLength) / file.processingFormat.sampleRate * 1_000_000_000))
            }
            let beforeEOF = eventCount; try await finish()
            return ["text": text, "events": eventCount, "volatileEvents": volatileCount,
                "maximumRMS": maximumRMS, "audibleInputSeconds": audibleInputSeconds,
                "finalizationSeconds": finalizationSeconds, "eventsBeforeEndOfAudio": beforeEOF,
                "firstResultSecondsIncludingPreparation": firstResult ?? -1,
                "feedingSeconds": Date().timeIntervalSince(feedingStarted), "audioSeconds": Double(file.length) / file.processingFormat.sampleRate]
        } catch { await cancel(); throw error }
    }
}
