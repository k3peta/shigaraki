import Foundation
import Darwin
import KoeFlowCore

final class HaasCancellation: @unchecked Sendable {
    private let lock = NSLock(); private var value = false
    var cancelled: Bool { lock.lock(); defer { lock.unlock() }; return value }
    func cancel() { lock.lock(); value = true; lock.unlock() }
}

/// The only pipe writer. Serialization, PCM packing and potentially slow writes never run on the tap or main actor.
final class HaasPipeWriter: @unchecked Sendable {
    private let queue = DispatchQueue(label: "jp.ys.shigaraki.haas.pipe", qos: .userInitiated)
    private let handle: FileHandle
    private let cancelled = HaasCancellation()
    private var sequence: UInt64 = 0
    private let pendingLock = NSLock()
    private var pending = 0
    private var pendingBytes = 0
    private let failure: @Sendable (String) -> Void
    init(handle: FileHandle, failure: @escaping @Sendable (String) -> Void) throws {
        self.handle = handle; self.failure = failure
        let fd = handle.fileDescriptor
        guard fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK) == 0,
              fcntl(fd, F_SETNOSIGPIPE, 1) == 0 else { throw CocoaError(.fileWriteUnknown) }
    }
    private func write(_ message: [String: Any]) throws {
        var data = try JSONSerialization.data(withJSONObject: message); data.append(10)
        let deadline = ProcessInfo.processInfo.systemUptime + 8
        try data.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                guard !cancelled.cancelled else { throw CancellationError() }
                let n = Darwin.write(handle.fileDescriptor, raw.baseAddress!.advanced(by: offset), raw.count - offset)
                if n > 0 { offset += n; continue }
                if n < 0 && errno == EINTR { continue }
                guard n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK), ProcessInfo.processInfo.systemUptime < deadline else {
                    throw CocoaError(.fileWriteUnknown)
                }
                var descriptor = pollfd(fd: handle.fileDescriptor, events: Int16(POLLOUT), revents: 0)
                _ = poll(&descriptor, 1, 25)
            }
        }
    }
    func command(_ message: [String: Any]) {
        queue.async { [self] in
            guard !cancelled.cancelled else { return }
            do { try write(message) } catch { if !cancelled.cancelled { failure("音声認識への送信に失敗しました。") } }
        }
    }
    func begin(id: UInt64, keywords: String) {
        queue.async { [self] in
            sequence = 0
            do { try write(["type": "start", "id": id, "keywords": keywords, "preview": true]) }
            catch { if !cancelled.cancelled { failure("音声認識を開始できませんでした。") } }
        }
    }
    func audio(_ pcm: Data, id: UInt64, token: HaasCancellation) {
        guard !cancelled.cancelled, !token.cancelled else { return }
        pendingLock.lock()
        let permitted = pending < 64 && pendingBytes + pcm.count <= 524288
        if permitted { pending += 1; pendingBytes += pcm.count }
        pendingLock.unlock()
        guard permitted else { cancelled.cancel(); failure("音声認識が入力に追いつきません。一度停止してください。"); return }
        queue.async { [self] in
            defer { pendingLock.lock(); pending -= 1; pendingBytes -= pcm.count; pendingLock.unlock() }
            guard !cancelled.cancelled, !token.cancelled else { return }
            do {
                for start in stride(from: 0, to: pcm.count, by: 32000) {
                    guard !token.cancelled else { return }
                    let chunk = pcm.subdata(in: start..<min(start + 32000, pcm.count))
                    try write(["type": "audio", "id": id, "seq": sequence, "pcm": chunk.base64EncodedString()]); sequence += 1
                }
            } catch { if !cancelled.cancelled { failure("音声認識への送信に失敗しました。") } }
        }
    }
    func invalidate() {
        cancelled.cancel()
        queue.async { [self] in try? handle.close() }
    }
}

struct HaasAudioWriter: Sendable {
    let id: UInt64
    let pipe: HaasPipeWriter
    let token: HaasCancellation
    func send(_ pcm: Data) { if !token.cancelled { pipe.audio(pcm, id: id, token: token) } }
    func cancelPending() { token.cancel() }
}

@MainActor final class HaasWorker {
    private var process: Process?
    private var retiring: [Process] = []
    private var pipe: HaasPipeWriter?
    private var generation = UUID()
    private var counter: UInt64 = 0
    private var active: UInt64?
    private var callback: ((HaasEvent) -> Void)?
    private var readyWaiter: CheckedContinuation<Void, Error>?
    private var terminalWaiter: CheckedContinuation<HaasEvent, Error>?
    private var lastTerminal: (UInt64, Result<HaasEvent, Error>)?
    private(set) var isReady = false
    var processIdentifier: Int32? { process?.processIdentifier }

    func warm(model: URL, helper: URL, library: URL) async throws {
        if isReady { return }
        guard readyWaiter == nil, active == nil else { throw HaasProtocolError.busy }
        try Task.checkCancellation()
        shutdown(error: CancellationError())
        let id = UUID(); generation = id
        let child = Process(), input = Pipe(), output = Pipe()
        child.executableURL = helper
        child.arguments = ["--model", model.path, "--library", library.path, "--threads", String(min(6, max(1, ProcessInfo.processInfo.activeProcessorCount)))]
        child.currentDirectoryURL = helper.deletingLastPathComponent()
        child.environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("GGML_") && !$0.key.hasPrefix("SPEECH_") && !$0.key.hasPrefix("DYLD_") }
        child.standardInput = input; child.standardOutput = output; child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] child in
            let status = child.terminationStatus
            Task { @MainActor [weak self] in
                guard self?.generation == id else { return }
                self?.shutdown(error: FlowError.message("音声認識が終了しました（\(status)）。モデルの準備から再試行してください。"))
            }
        }
        try child.run(); process = child
        try? input.fileHandleForReading.close(); try? output.fileHandleForWriting.close()
        do {
            pipe = try HaasPipeWriter(handle: input.fileHandleForWriting) { [weak self] error in
                Task { @MainActor [weak self] in if self?.generation == id { self?.shutdown(error: FlowError.message(error)) } }
            }
        } catch { shutdown(error: error); throw error }
        let reader = output.fileHandleForReading
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let slots = DispatchSemaphore(value: 32)
            defer { try? reader.close() }
            do {
                var pending = Data()
                var storage = [UInt8](repeating: 0, count: 4096)
                while true {
                    // FileHandle.read(upToCount:) can aggregate pipe reads until its requested size.
                    // One short ready/caption line must be delivered immediately, before the next event.
                    let count = storage.withUnsafeMutableBytes { Darwin.read(reader.fileDescriptor, $0.baseAddress, $0.count) }
                    if count == 0 { break }
                    if count < 0 {
                        if errno == EINTR { continue }
                        throw CocoaError(.fileReadUnknown)
                    }
                    pending.append(contentsOf: storage.prefix(count))
                    while let newline = pending.firstIndex(of: 10) {
                        let line = Data(pending[..<newline]); pending.removeSubrange(...newline)
                        let event = try HaasEvent.decode(line)
                        guard slots.wait(timeout: .now() + 5) == .success else { throw HaasProtocolError.invalidMessage }
                        Task { @MainActor [weak self] in defer { slots.signal() }; self?.receive(event, generation: id) }
                    }
                    guard pending.count <= HaasEvent.maximumLineBytes else { throw HaasProtocolError.invalidMessage }
                }
                throw FlowError.message("音声認識との接続が閉じました。")
            } catch {
                Task { @MainActor [weak self] in if self?.generation == id { self?.shutdown(error: error) } }
            }
        }
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                readyWaiter = continuation
                Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: 90_000_000_000)
                    guard let self, self.generation == id, !self.isReady else { return }
                    self.shutdown(error: FlowError.message("音声モデルの起動が時間内に終わりませんでした。"))
                }
            }
        }, onCancel: { [weak self] in
            Task { @MainActor [weak self] in
                guard let self, self.generation == id, !self.isReady else { return }
                self.shutdown(error: CancellationError())
            }
        })
    }
    func begin(keywords: [String], onEvent: @escaping (HaasEvent) -> Void) throws -> HaasAudioWriter {
        guard isReady, active == nil, let pipe else { throw HaasProtocolError.busy }
        counter += 1; let id = counter; active = id; callback = onEvent; lastTerminal = nil
        // Preserve spelling hints; reject delimiters rather than turning context into decoder commands.
        var total = 0
        let lines = keywords.filter { !$0.isEmpty && $0.count <= 80 &&
            !$0.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) &&
            !$0.contains(where: { $0 == "=" || $0 == "," }) }
            .prefix(16).filter { value in total += value.utf8.count; return total <= 2400 }
        pipe.begin(id: id, keywords: lines.joined(separator: "\n"))
        return HaasAudioWriter(id: id, pipe: pipe, token: HaasCancellation())
    }
    func finish(_ id: UInt64) async throws {
        let event = try await terminal(id, cancel: false)
        guard event.type == "complete" else { throw CancellationError() }
    }
    func cancel(_ id: UInt64) async {
        _ = try? await terminal(id, cancel: true)
    }
    func shutdown() async {
        let children = retiring + (process.map { [$0] } ?? [])
        shutdown(error: CancellationError())
        await Task.detached(priority: .utility) {
            let deadline = ProcessInfo.processInfo.systemUptime + 2
            while children.contains(where: { $0.isRunning }) && ProcessInfo.processInfo.systemUptime < deadline { usleep(20_000) }
            for child in children where child.isRunning { kill(child.processIdentifier, SIGKILL) }
            let finalDeadline = ProcessInfo.processInfo.systemUptime + 1
            while children.contains(where: { $0.isRunning }) && ProcessInfo.processInfo.systemUptime < finalDeadline { usleep(20_000) }
        }.value
        retiring.removeAll { !$0.isRunning }
    }
    private func terminal(_ id: UInt64, cancel: Bool) async throws -> HaasEvent {
        if let result = lastTerminal, result.0 == id { return try result.1.get() }
        guard active == id, let pipe else { throw FlowError.message("音声認識の状態を確認できません。") }
        if cancel { terminalWaiter?.resume(throwing: CancellationError()); terminalWaiter = nil }
        guard terminalWaiter == nil else { throw HaasProtocolError.busy }
        let expected = generation
        return try await withCheckedThrowingContinuation { continuation in
            terminalWaiter = continuation
            pipe.command(["type": cancel ? "cancel" : "stop", "id": id])
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: cancel ? 5_000_000_000 : 60_000_000_000)
                guard let self, self.generation == expected, self.active == id else { return }
                self.shutdown(error: FlowError.message("音声認識の処理が時間内に終わりませんでした。"))
            }
        }
    }
    private func receive(_ event: HaasEvent, generation id: UUID) {
        guard generation == id else { return }
        if event.type == "ready" {
            guard !isReady, let waiter = readyWaiter else { shutdown(error: HaasProtocolError.invalidMessage); return }
            isReady = true; readyWaiter = nil; waiter.resume(); return
        }
        if event.type == "fatal" { shutdown(error: FlowError.message("音声認識の起動に失敗しました。")); return }
        guard let session = event.id, active == session else { return }
        let terminal = ["complete", "cancelled", "error"].contains(event.type)
        let handler = callback
        if terminal { active = nil; callback = nil }
        handler?(event)
        if terminal {
            let result: Result<HaasEvent, Error> = event.type == "error" ? .failure(FlowError.message("音声認識を完了できませんでした。途中の文章を保持します。")) : .success(event)
            lastTerminal = (session, result)
            let waiter = terminalWaiter; terminalWaiter = nil; waiter?.resume(with: result)
        }
    }
    private func shutdown(error: Error) {
        generation = UUID(); isReady = false; pipe?.invalidate(); pipe = nil
        let old = process; process = nil
        retiring.removeAll { !$0.isRunning }
        if old?.isRunning == true {
            if let old { retiring.append(old) }
            old?.terminate()
            Task.detached { try? await Task.sleep(nanoseconds: 1_000_000_000); if let old, old.isRunning { kill(old.processIdentifier, SIGKILL) } }
        }
        let launch = readyWaiter; readyWaiter = nil; launch?.resume(throwing: error)
        let waiter = terminalWaiter; terminalWaiter = nil; waiter?.resume(throwing: error)
        if let active {
            lastTerminal = (active, .failure(error)); self.active = nil
            let handler = callback; callback = nil
            // A synthetic error event contains no recognized content. The engine keeps its own last draft.
            if let event = try? HaasEvent.decode(Data("{\"type\":\"error\",\"id\":\(active)}".utf8)) { handler?(event) }
        }
    }
}
