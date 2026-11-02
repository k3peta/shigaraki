import XCTest
import Darwin
@testable import KoeFlowCore

final class TranscriptLogTests: XCTestCase {
    private func record(recognized: String, final: String, partial: Bool, date: Date = Date()) -> TranscriptRecord {
        TranscriptRecord(startedAt: date, timestamp: date, partial: partial, recognized: recognized, final: final)
    }
    func testAppendPreservesEarlierUnicodeAndKeepsRecognizedAndFormattedText() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiLogTest-\(UUID())")
        let log = TranscriptLog(url: folder.appendingPathComponent("log.txt"))
        try log.append(record(recognized: "明日15時に送ります", final: "明日15時に送ります。", partial: false, date: Date(timeIntervalSince1970: 0)))
        let first = try String(contentsOf: log.url, encoding: .utf8)
        try log.append(record(recognized: "ここまで認識した", final: "ここまで認識した", partial: true, date: Date(timeIntervalSince1970: 1)))
        let content = try String(contentsOf: log.url, encoding: .utf8)
        XCTAssertTrue(content.hasPrefix(first))
        XCTAssertTrue(content.contains("[認識した文字]\n明日15時に送ります"))
        XCTAssertTrue(content.contains("[入力する文字]\n明日15時に送ります。"))
        XCTAssertTrue(content.contains("途中まで"))
        XCTAssertTrue(content.contains("ここまで認識した"))
        let permissions = try FileManager.default.attributesOfItem(atPath: log.url.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o600)
    }
    func testSymlinkAndHardlinkCannotRedirectLogWrites() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiLogAttack-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let protected = folder.appendingPathComponent("protected.txt")
        try "変更してはいけない".write(to: protected, atomically: true, encoding: .utf8)
        for name in ["symlink.txt", "hardlink.txt"] {
            let path = folder.appendingPathComponent(name)
            if name == "symlink.txt" { try FileManager.default.createSymbolicLink(at: path, withDestinationURL: protected) }
            else { try FileManager.default.linkItem(at: protected, to: path) }
            XCTAssertThrowsError(try TranscriptLog(url: path).append(record(recognized: "攻撃", final: "攻撃", partial: false)))
            XCTAssertEqual(try String(contentsOf: protected, encoding: .utf8), "変更してはいけない")
        }
    }
    func testNamedPipeIsRejectedWithoutBlockingTheApp() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiPipeAttack-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let path = folder.appendingPathComponent("log.txt")
        XCTAssertEqual(mkfifo(path.path, 0o600), 0)
        XCTAssertThrowsError(try TranscriptLog(url: path).append(record(recognized: "入力", final: "入力", partial: false)))
    }
    func testSnapshotsReplaceAtomicallyAndRejectRedirectedDestination() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiSnapshotTest-\(UUID())")
        let snapshot = TranscriptLog(url: folder.appendingPathComponent("latest.txt"))
        try snapshot.writeSnapshot("未確定の長い文章")
        try snapshot.writeSnapshot("短い修正")
        XCTAssertEqual(try String(contentsOf: snapshot.url, encoding: .utf8), "短い修正")
        let victim = folder.appendingPathComponent("victim.txt")
        try "変更禁止".write(to: victim, atomically: true, encoding: .utf8)
        let redirect = folder.appendingPathComponent("redirect.txt")
        try FileManager.default.createSymbolicLink(at: redirect, withDestinationURL: victim)
        XCTAssertThrowsError(try TranscriptLog(url: redirect).writeSnapshot("上書き攻撃"))
        XCTAssertEqual(try String(contentsOf: victim, encoding: .utf8), "変更禁止")
    }
}
