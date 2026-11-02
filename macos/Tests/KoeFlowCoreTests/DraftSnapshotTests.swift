import XCTest
import Darwin
@testable import KoeFlowCore

final class DraftSnapshotTests: XCTestCase {
    func testDraftAndUnverifiedResultRoundTripWithoutChangingText() throws {
        for pending in [true, false] {
            let draft = DraftSnapshot(text: "確認した🙂本文。\n次の行", rawText: "かくにんした本文", original: "確認した本文。", pending: pending)
            XCTAssertEqual(try DraftSnapshot.decode(draft.encoded()), draft)
        }
    }
    func testUnknownSchemaAndOversizedStateAreRejected() throws {
        XCTAssertThrowsError(try DraftSnapshot.decode(#"{"schemaVersion":2,"text":"x","rawText":"x","original":"x","pending":true}"#))
        XCTAssertThrowsError(try DraftSnapshot(text: String(repeating: "a", count: 1_048_577), rawText: "", original: "", pending: true).encoded())
    }
    func testRecoveryFileIsPrivateAndMissingFileDoesNotInventADraft() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let log = TranscriptLog(url: dir.appendingPathComponent("last.json"))
        XCTAssertNil(try log.readSnapshot())
        let value = try DraftSnapshot(text: "未転記の本文", rawText: "原音の文字", original: "未転記の本文", pending: true).encoded()
        try log.writeSnapshot(value)
        XCTAssertEqual(try log.readSnapshot(), value)
        let permissions = try FileManager.default.attributesOfItem(atPath: log.url.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o600)
    }
    func testLinksSpecialFilesAndOversizedRecoveryCannotBeRead() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let protected = dir.appendingPathComponent("private.txt")
        try "秘密".write(to: protected, atomically: true, encoding: .utf8)
        let link = dir.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: protected)
        XCTAssertThrowsError(try TranscriptLog(url: link).readSnapshot())
        let pipe = dir.appendingPathComponent("pipe")
        XCTAssertEqual(mkfifo(pipe.path, 0o600), 0)
        XCTAssertThrowsError(try TranscriptLog(url: pipe).readSnapshot())
        let large = dir.appendingPathComponent("large")
        try Data(repeating: 0x61, count: 17).write(to: large)
        XCTAssertThrowsError(try TranscriptLog(url: large).readSnapshot(maximumBytes: 16))
        XCTAssertEqual(try String(contentsOf: protected, encoding: .utf8), "秘密")
    }
}
