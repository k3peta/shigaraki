import Foundation
import Darwin

public struct TranscriptLog {
    public let url: URL
    public init(url: URL) { self.url = url }
    public static var defaultURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/信楽/音声入力ログ.txt")
    }
    public static var snapshotURL: URL { defaultURL.deletingLastPathComponent().appendingPathComponent("最後の認識.txt") }
    public var structuredURL: URL { url.deletingPathExtension().appendingPathExtension("jsonl") }
    /// Read one small recovery file without following substituted directories,
    /// links, or special files. History remains an append-only separate file.
    public func readSnapshot(maximumBytes: Int = 1_048_576) throws -> String? {
        guard maximumBytes > 0 && maximumBytes <= 1_048_576 else { throw CocoaError(.fileReadTooLarge) }
        let directory = open(url.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        if directory < 0 && errno == ENOENT { return nil }
        guard directory >= 0 else { throw CocoaError(.fileReadNoPermission) }
        defer { Darwin.close(directory) }
        let fd = openat(directory, url.lastPathComponent, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        if fd < 0 && errno == ENOENT { return nil }
        guard fd >= 0 else { throw CocoaError(.fileReadNoPermission) }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        var metadata = stat()
        guard fstat(fd, &metadata) == 0, metadata.st_mode & S_IFMT == S_IFREG,
              metadata.st_uid == geteuid(), metadata.st_nlink == 1,
              metadata.st_size >= 0, metadata.st_size <= maximumBytes else { throw CocoaError(.fileReadTooLarge) }
        let data = try handle.read(upToCount: maximumBytes + 1) ?? Data()
        guard data.count <= maximumBytes, let value = String(data: data, encoding: .utf8) else { throw CocoaError(.fileReadCorruptFile) }
        return value
    }
    public func writeSnapshot(_ text: String) throws {
        let directoryURL = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let directory = open(directoryURL.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard directory >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        defer { Darwin.close(directory) }
        var existing = stat()
        if fstatat(directory, url.lastPathComponent, &existing, AT_SYMLINK_NOFOLLOW) == 0 {
            guard existing.st_mode & S_IFMT == S_IFREG, existing.st_uid == geteuid(), existing.st_nlink == 1 else { throw CocoaError(.fileWriteNoPermission) }
        } else if errno != ENOENT { throw CocoaError(.fileWriteNoPermission) }
        let name = ".shigaraki-draft-\(UUID().uuidString)"
        let fd = openat(directory, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close(); unlinkat(directory, name, 0) }
        try handle.write(contentsOf: Data(text.utf8)); try handle.synchronize()
        guard renameat(directory, name, directory, url.lastPathComponent) == 0 else { throw CocoaError(.fileWriteUnknown) }
        fsync(directory)
    }
    public func prepare() throws {
        let handle = try openForAppend()
        try handle.close()
    }
    private func openForAppend() throws -> FileHandle {
        // Never follow a substituted log directory/file or append dictation to another file.
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let directory = open(url.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard directory >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        defer { Darwin.close(directory) }
        let fd = openat(directory, url.lastPathComponent, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        var metadata = stat()
        guard fstat(fd, &metadata) == 0, metadata.st_mode & S_IFMT == S_IFREG,
              metadata.st_uid == geteuid(), metadata.st_nlink == 1, fchmod(fd, 0o600) == 0 else {
            Darwin.close(fd); throw CocoaError(.fileWriteNoPermission)
        }
        return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }
    public func append(_ record: TranscriptRecord) throws {
        guard record.kind == .retry || !record.recognized.isEmpty || !record.final.isEmpty else { return }
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601; encoder.outputFormatting = [.sortedKeys]
        var data = try encoder.encode(record); data.append(0x0a)
        var failure: Error?
        // Independent appends: failure of either destination must not discard the other copy.
        do { try TranscriptLog(url: structuredURL).appendBytes(data) } catch { failure = error }
        do { try appendBytes(Data(Self.readable(record).utf8)) } catch { if failure == nil { failure = error } }
        if let failure { throw failure }
    }
    private static func readable(_ record: TranscriptRecord) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(identifier: record.timeZoneID) ?? .current
        func line(_ value: String) -> String { value.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ") }
        let kind = record.kind == .retry ? "再貼り付け" : record.kind == .voiceEdit ? "音声編集" : (record.partial ? "途中まで" : "確定")
        var text = "\n--- \(formatter.string(from: record.timestamp)) \(kind) ---\n"
        text += "[入力ID] \(record.dictationID.uuidString)\n[開始] \(formatter.string(from: record.startedAt))\n[タイムゾーン] \(line(record.timeZoneID))\n"
        let destination = record.destination
        text += "[入力先アプリ] \(destination.appName.isEmpty ? "字幕のみ・入力先不明" : line(destination.appName))"
        if !destination.bundleID.isEmpty { text += " (\(line(destination.bundleID)))" }
        text += "\n"
        if let host = destination.siteHost { text += "[サイト] \(line(host))\n" }
        if !destination.windowTitle.isEmpty { text += "[ウィンドウ] \(line(destination.windowTitle))\n" }
        text += "[用途] \(record.purpose.title)\n"
        if !record.cardName.isEmpty { text += "[カード] \(line(record.cardName))\n" }
        text += "[転記結果] \(record.outcome.title)\n"
        if record.kind == .voiceEdit {
            text += "[音声指示]\n" + record.recognized + "\n"
            if let scope = record.editScope { text += "[編集対象] " + (scope == .document ? "文書全体" : "選択範囲") + "\n" }
            if let count = record.editedCharacters { text += "[変更後の文字数] \(count)\n" }
        }
        if record.kind == .dictation {
            text += "[認識した文字]\n" + record.recognized + "\n"
            if record.recognized != record.final { text += "[入力する文字]\n" + record.final + "\n" }
        }
        return text
    }
    private func appendBytes(_ data: Data) throws {
        let handle = try openForAppend()
        defer { try? handle.close() }
        try handle.write(contentsOf: data)
        try handle.synchronize()
    }
}
