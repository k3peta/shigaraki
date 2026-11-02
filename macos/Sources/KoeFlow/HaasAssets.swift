import Foundation
import CryptoKit
import Darwin
import KoeFlowCore

private struct ModelIdentity: Equatable, Sendable {
    let device: UInt64, inode: UInt64, size: Int64, seconds: Int, nanos: Int
    init(_ s: stat) { device = UInt64(s.st_dev); inode = s.st_ino; size = s.st_size; seconds = s.st_mtimespec.tv_sec; nanos = s.st_mtimespec.tv_nsec }
    static func read(_ url: URL) -> Self? {
        var value = stat()
        guard lstat(url.path, &value) == 0, value.st_mode & S_IFMT == S_IFREG else { return nil }
        return Self(value)
    }
}

private enum ModelFiles {
    static func verified(_ url: URL, pin: ModelPin) async throws -> ModelIdentity? {
        let job = Task.detached(priority: .utility) { () throws -> ModelIdentity? in
            try Task.checkCancellation()
            let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
            guard fd >= 0 else { return nil }
            let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            defer { try? handle.close() }
            var before = stat()
            guard fstat(fd, &before) == 0, before.st_mode & S_IFMT == S_IFREG, before.st_size == pin.bytes else { return nil }
            var sha = SHA256()
            while let data = try handle.read(upToCount: 4 * 1024 * 1024), !data.isEmpty {
                try Task.checkCancellation(); sha.update(data: data)
            }
            var after = stat()
            guard fstat(fd, &after) == 0, ModelIdentity(before) == ModelIdentity(after),
                  ModelIdentity.read(url) == ModelIdentity(after),
                  sha.finalize().map({ String(format: "%02x", $0) }).joined() == pin.sha256 else { return nil }
            return ModelIdentity(after)
        }
        return try await withTaskCancellationHandler(operation: { try await job.value }, onCancel: { job.cancel() })
    }
    static func publish(_ temporary: URL, target: URL, owned: URL, pin: ModelPin) async throws {
        let job = Task.detached(priority: .utility) {
            try Task.checkCancellation()
            guard DownloadPolicy.mayRepair(target, ownedURL: owned) else { throw FlowError.message("モデルの保存先を確認できません。") }
            let directory = target.deletingLastPathComponent()
            guard directory.resolvingSymlinksInPath().path == directory.standardizedFileURL.path else { throw CocoaError(.fileWriteNoPermission) }
            let dfd = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            guard dfd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
            defer { Darwin.close(dfd) }
            let name = ".haas-\(UUID().uuidString).part"
            let fd = openat(dfd, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
            guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
            let output = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            defer { try? output.close(); unlinkat(dfd, name, 0) }
            let input = try FileHandle(forReadingFrom: temporary); defer { try? input.close() }
            var sha = SHA256(); var count: Int64 = 0
            while let data = try input.read(upToCount: 4 * 1024 * 1024), !data.isEmpty {
                try Task.checkCancellation(); count += Int64(data.count)
                guard count <= pin.bytes else { throw FlowError.message("モデルのサイズが正しくありません。") }
                sha.update(data: data); try output.write(contentsOf: data)
            }
            guard count == pin.bytes, sha.finalize().map({ String(format: "%02x", $0) }).joined() == pin.sha256 else {
                throw FlowError.message("モデルの検証に失敗しました。再試行してください。")
            }
            try Task.checkCancellation(); try output.synchronize()
            // linkat refuses a concurrent writer; no existing model is overwritten.
            guard linkat(dfd, name, dfd, target.lastPathComponent, 0) == 0 else { throw CocoaError(.fileWriteFileExists) }
            fsync(dfd)
        }
        try await withTaskCancellationHandler(operation: { try await job.value }, onCancel: { job.cancel() })
    }
}

/// The session owns this serial delegate; every body chunk is checked before its bounded disk write.
private final class ModelDownloadDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let pin: ModelPin
    private let progress: @Sendable (Int64) -> Void
    private let lock = NSLock()
    private var cancelled = false
    private var task: URLSessionDataTask?
    private var continuation: CheckedContinuation<URL, Error>?
    // The fields below are used only by the serial delegate queue, after initialization.
    private var output: FileHandle?
    private var temporary: URL?
    private var budget: DownloadByteBudget
    private var failure: Error?
    private var lastProgress: TimeInterval = 0
    init(pin: ModelPin, progress: @escaping @Sendable (Int64) -> Void) {
        self.pin = pin; self.progress = progress; self.budget = DownloadByteBudget(maximum: pin.bytes)
    }
    func download() async throws -> URL {
        try Task.checkCancellation()
        guard DownloadPolicy.permits(pin.url) else { throw URLError(.badURL) }
        let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(".shigaraki-model-\(UUID().uuidString).part")
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        self.temporary = temporary; self.output = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil; configuration.urlCache = nil; configuration.httpShouldSetCookies = false
        configuration.timeoutIntervalForRequest = 30; configuration.timeoutIntervalForResource = 1800
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1; queue.qualityOfService = .utility
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
        var completed = false
        defer {
            session.invalidateAndCancel()
            lock.lock(); task = nil; lock.unlock()
            try? output?.close(); output = nil
            if !completed { unlink(temporary.path) }
        }
        let result = try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
                lock.lock()
                if cancelled || Task.isCancelled {
                    lock.unlock(); continuation.resume(throwing: CancellationError()); return
                }
                self.continuation = continuation
                let task = session.dataTask(with: URLRequest(url: pin.url)); self.task = task
                lock.unlock(); task.resume()
            }
        }, onCancel: { [self] in cancel() })
        completed = true; return result
    }
    private func cancel() {
        lock.lock(); cancelled = true; let current = task; lock.unlock(); current?.cancel()
    }
    private func finish(_ result: Result<URL, Error>) {
        lock.lock(); let waiter = continuation; continuation = nil; lock.unlock()
        waiter?.resume(with: result)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let url = request.url, DownloadPolicy.permits(url) else {
            failure = URLError(.unsupportedURL); completionHandler(nil); return
        }
        completionHandler(request)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              let url = response.url, DownloadPolicy.permits(url) else {
            failure = URLError(.badServerResponse); completionHandler(.cancel); return
        }
        guard budget.permitsDeclaredLength(response.expectedContentLength) else {
            failure = FlowError.message("モデルのサイズが正しくありません。"); completionHandler(.cancel); return
        }
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard failure == nil else { return }
        guard budget.accept(data.count) else {
            failure = FlowError.message("モデルのサイズが正しくありません。"); dataTask.cancel(); return
        }
        do {
            guard let output else { throw CocoaError(.fileWriteUnknown) }
            try output.write(contentsOf: data)
            let now = ProcessInfo.processInfo.systemUptime
            if now - lastProgress >= 0.1 || budget.complete {
                lastProgress = now; progress(budget.received)
            }
        } catch { failure = error; dataTask.cancel() }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let failure { finish(.failure(failure)); return }
        lock.lock(); let wasCancelled = cancelled; lock.unlock()
        if wasCancelled { finish(.failure(CancellationError())); return }
        if let error { finish(.failure(error)); return }
        guard budget.complete, let temporary else {
            finish(.failure(FlowError.message("モデルのサイズが正しくありません。"))); return
        }
        do { try output?.synchronize(); finish(.success(temporary)) }
        catch { finish(.failure(error)) }
    }
}

@MainActor final class HaasAssets {
    static let shared = HaasAssets()
    static let pin = ModelPin.haasJapanese
    static var ownedModelURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/信楽/models")
            .appendingPathComponent(pin.fileName)
    }
    let modelURL: URL
    private let ownedURL: URL
    private let verifiesBundledNative: Bool
    let helperURL: URL
    let libraryURL: URL
    let worker = HaasWorker()
    private var identity: ModelIdentity?
    private var preparation: Task<Void, Error>?
    private var preparationID: UUID?
    private var preparationWaiters: [UUID: CheckedContinuation<Void, Error>] = [:]
    private var lastPreparationError: Error?
    var isReady: Bool { identity != nil && worker.isReady }

    init(modelURL: URL? = nil, helperURL: URL? = nil, libraryURL: URL? = nil) {
        self.ownedURL = Self.ownedModelURL
        self.verifiesBundledNative = helperURL == nil && libraryURL == nil
        self.modelURL = modelURL ?? Self.ownedModelURL
        self.helperURL = helperURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/shigaraki-haas-flow")
        self.libraryURL = libraryURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Frameworks/libspeech.4.dylib")
    }
    /// Explicit test-only ownership injection. Production callers use the initializer above.
    init(ownedDirectory: URL, helperURL: URL? = nil, libraryURL: URL? = nil) {
        self.ownedURL = ownedDirectory.standardizedFileURL.appendingPathComponent(Self.pin.fileName)
        self.verifiesBundledNative = helperURL == nil && libraryURL == nil
        self.modelURL = self.ownedURL
        self.helperURL = helperURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/shigaraki-haas-flow")
        self.libraryURL = libraryURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Frameworks/libspeech.4.dylib")
    }
    /// Read-only. A verified unchanged file is reused without hashing 352 MB on every UI refresh.
    func check() async throws -> Bool {
        if let identity, ModelIdentity.read(modelURL) == identity { return true }
        identity = try await ModelFiles.verified(modelURL, pin: Self.pin)
        return identity != nil
    }
    func prepare(progress: @escaping (HaasPreparationProgress) -> Void = { _ in }) async throws {
        if preparation != nil { try await waitForPreparation(); return }
        lastPreparationError = nil
        let id = UUID(); preparationID = id
        let task = Task { @MainActor [weak self] in
            guard let self else { throw CancellationError() }
            progress(.init(stage: .checking))
            if self.verifiesBundledNative { try await self.verifyNativeManifest() }
            if !(try await self.check()) {
                try Task.checkCancellation()
                guard DownloadPolicy.mayRepair(self.modelURL, ownedURL: self.ownedURL) else {
                    throw FlowError.message("指定したモデルが見つからないか、検証に失敗しました。")
                }
                let directory = self.modelURL.deletingLastPathComponent()
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                guard directory.resolvingSymlinksInPath().path == directory.standardizedFileURL.path else { throw CocoaError(.fileWriteNoPermission) }
                var current = stat()
                if lstat(self.modelURL.path, &current) == 0 {
                    guard current.st_mode & S_IFMT == S_IFREG, current.st_uid == geteuid(), current.st_nlink == 1 else { throw CocoaError(.fileWriteNoPermission) }
                    // Preserve a damaged owned file instead of silently deleting it.
                    try FileManager.default.moveItem(at: self.modelURL, to: self.modelURL.appendingPathExtension("invalid-" + UUID().uuidString))
                }
                guard DownloadPolicy.permits(Self.pin.url) else { throw URLError(.badURL) }
                let reports = HaasCancellation()
                defer { reports.cancel() }
                let delegate = ModelDownloadDelegate(pin: Self.pin) { count in
                    Task { @MainActor [weak self] in
                        guard self?.preparationID == id, !reports.cancelled else { return }
                        progress(.init(stage: .downloading, bytes: count))
                    }
                }
                progress(.init(stage: .downloading))
                let temporary = try await delegate.download()
                defer { try? FileManager.default.removeItem(at: temporary) }
                reports.cancel()
                progress(.init(stage: .downloading, bytes: Self.pin.bytes))
                try Task.checkCancellation(); progress(.init(stage: .verifying, bytes: Self.pin.bytes))
                try await ModelFiles.publish(temporary, target: self.modelURL, owned: self.ownedURL, pin: Self.pin)
                self.identity = try await ModelFiles.verified(self.modelURL, pin: Self.pin)
                guard self.identity != nil else { throw FlowError.message("モデルの検証に失敗しました。") }
            }
            try Task.checkCancellation(); progress(.init(stage: .warming, bytes: Self.pin.bytes))
            try await self.worker.warm(model: self.modelURL, helper: self.helperURL, library: self.libraryURL)
            try Task.checkCancellation(); progress(.init(stage: .ready, bytes: Self.pin.bytes))
        }
        preparation = task
        do {
            try await withTaskCancellationHandler(operation: { try await task.value }, onCancel: { task.cancel() })
            try Task.checkCancellation()
            completePreparation(id, result: .success(()))
        } catch { completePreparation(id, result: .failure(error)); throw error }
    }
    private func waitForPreparation() async throws {
        let waiterID = UUID()
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()) }
                else if preparation == nil {
                    if isReady { continuation.resume() }
                    else { continuation.resume(throwing: lastPreparationError ?? CancellationError()) }
                }
                else { preparationWaiters[waiterID] = continuation }
            }
        }, onCancel: { [weak self] in Task { @MainActor [weak self] in
            self?.preparationWaiters.removeValue(forKey: waiterID)?.resume(throwing: CancellationError())
        } })
    }
    private func completePreparation(_ id: UUID, result: Result<Void, Error>) {
        guard preparationID == id else { return }
        if case .failure(let error) = result { lastPreparationError = error }
        preparation = nil; preparationID = nil
        let waiters = Array(preparationWaiters.values); preparationWaiters.removeAll()
        for waiter in waiters { waiter.resume(with: result) }
    }
    func cancelPreparation() { preparation?.cancel() }
    private func verifyNativeManifest() async throws {
        struct Entry: Decodable { let bytes: Int64; let sha256: String }
        struct Manifest: Decodable { let files: [String: Entry] }
        guard let resource = Bundle.main.resourceURL?.appendingPathComponent("MacFiles/native-manifest.json"),
              let identity = ModelIdentity.read(resource), identity.size <= 65536 else {
            throw FlowError.message("認識エンジンの検証情報がありません。アプリを入れ直してください。")
        }
        let manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: resource))
        for (name, url) in [("shigaraki-haas-flow", helperURL), ("libspeech.4.dylib", libraryURL)] {
            guard let entry = manifest.files[name], entry.bytes > 0,
                  entry.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else {
                throw FlowError.message("認識エンジンの検証情報が正しくありません。")
            }
            let pin = ModelPin(fileName: name, bytes: entry.bytes, sha256: entry.sha256, revision: "bundle", url: Self.pin.url)
            guard try await ModelFiles.verified(url, pin: pin) != nil else {
                throw FlowError.message("認識エンジンの検証に失敗しました。アプリを入れ直してください。")
            }
        }
    }
    /// App termination only. Ordinary preparation cancellation never tears down a ready recording worker.
    func shutdown() async {
        preparation?.cancel()
        if let id = preparationID { completePreparation(id, result: .failure(CancellationError())) }
        await worker.shutdown()
    }
}
