import XCTest
@testable import KoeFlowCore

final class TranscriptRecordTests: XCTestCase {
    func testDestinationKeepsOnlyHostAndRejectsNonWebURLs() {
        let destination = LogDestination(bundleID: "com.apple.Safari", appName: "Safari",
            windowTitle: "ページ https://example.com/private?token=secret",
            siteURL: "https://user:password@EXAMPLE.com/private/document?token=secret#selection")
        XCTAssertEqual(destination.siteHost, "example.com")
        XCTAssertEqual(destination.windowTitle, "ページ example.com")
        for url in ["file:///Users/private/file.txt", "javascript:alert(1)", "about:blank", "not a URL"] {
            XCTAssertNil(LogDestination.host(from: url))
        }
    }

    func testJSONLRoundTripRetainsTimezoneContextAndRetryIdentityWithoutDuplicatingText() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiRecordTest-\(UUID())")
        let log = TranscriptLog(url: folder.appendingPathComponent("log.txt"))
        let zone = TimeZone(identifier: "Asia/Tokyo")!
        // UTC Oct 5 15:01 is Oct 6 in Japan; daily consumers must use the recorded zone.
        let date = ISO8601DateFormatter().date(from: "2026-10-05T15:01:00Z")!
        let original = TranscriptRecord(startedAt: date.addingTimeInterval(-20), timestamp: date, timeZone: zone,
            destination: LogDestination(bundleID: "com.google.Chrome", appName: "Chrome", windowTitle: "文書\n次の行", siteURL: "https://example.com/private?token=secret"),
            outcome: .unconfirmed, recognized: "一行目\n二行目🙂", final: "一行目。\n二行目🙂", purpose: .document, cardName: "一文ずつ改行")
        try log.append(original)
        let retry = TranscriptRecord(dictationID: original.id, kind: .retry, startedAt: date.addingTimeInterval(2),
            timestamp: date.addingTimeInterval(3), timeZone: zone,
            destination: LogDestination(bundleID: "md.obsidian", appName: "Obsidian"), outcome: .confirmed)
        try log.append(retry)
        let raw = try String(contentsOf: log.structuredURL, encoding: .utf8)
        let lines = raw.split(separator: "\n"); XCTAssertEqual(lines.count, 2)
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let records = try lines.map { try decoder.decode(TranscriptRecord.self, from: Data($0.utf8)) }
        XCTAssertEqual(records, [original, retry])
        XCTAssertEqual(records.filter { $0.kind == .dictation }.count, 1)
        XCTAssertEqual(records[1].dictationID, records[0].id)
        XCTAssertNotEqual(records[1].id, records[0].id)
        XCTAssertEqual(records[1].outcome, .confirmed)
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = zone
        XCTAssertEqual(calendar.component(.day, from: records[0].timestamp), 6)
        XCTAssertFalse(raw.contains("token")); XCTAssertFalse(raw.contains("/private"))
        let human = try String(contentsOf: log.url, encoding: .utf8)
        XCTAssertTrue(human.contains("2026-10-06T00:01:00+09:00"))
        XCTAssertTrue(human.contains("[サイト] example.com"))
        XCTAssertTrue(human.contains("[転記結果] 転記未確認"))
        XCTAssertTrue(human.contains("[転記結果] 入力欄への転記を確認"))
        XCTAssertTrue(human.contains("[ウィンドウ] 文書 次の行"))
        XCTAssertEqual(human.components(separatedBy: "[認識した文字]").count, 2)
        XCTAssertEqual((try FileManager.default.attributesOfItem(atPath: log.structuredURL.path)[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }

    func testStructuredLogRedirectCannotTouchVictimAndTextLogStillSurvives() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("ShigarakiJSONAttack-\(UUID())")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let victim = folder.appendingPathComponent("victim.txt")
        try "変更禁止".write(to: victim, atomically: true, encoding: .utf8)
        for name in ["symlink", "hardlink"] {
            let log = TranscriptLog(url: folder.appendingPathComponent(name + ".txt"))
            if name == "symlink" { try FileManager.default.createSymbolicLink(at: log.structuredURL, withDestinationURL: victim) }
            else { try FileManager.default.linkItem(at: victim, to: log.structuredURL) }
            XCTAssertThrowsError(try log.append(TranscriptRecord(startedAt: Date(), recognized: "保持する文字", final: "保持する文字")))
            XCTAssertEqual(try String(contentsOf: victim, encoding: .utf8), "変更禁止")
            XCTAssertTrue(try String(contentsOf: log.url, encoding: .utf8).contains("保持する文字"))
        }
    }
}
