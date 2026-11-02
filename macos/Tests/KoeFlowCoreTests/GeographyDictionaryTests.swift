import Foundation
import XCTest
@testable import KoeFlowCore

final class GeographyDictionaryTests: XCTestCase {
    private func national() throws -> GeographyDictionary {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return try GeographyDictionary(tsv: String(contentsOf: root.appendingPathComponent("Sources/KoeFlowCore/Resources/geography.tsv"), encoding: .utf8))
    }
    func testNationalCoverageAndDifficultReadings() throws {
        let dictionary = try national()
        XCTAssertEqual(dictionary.entries.count, 1939)
        XCTAssertEqual(dictionary.entries.filter { $0.id.count == 2 }.count, 47)
        XCTAssertEqual(dictionary.search("そうさ").first?.name, "匝瑳市")
        XCTAssertEqual(dictionary.search("各務原市").first?.reading, "かかみがはらし")
        XCTAssertEqual(dictionary.search("ｿｳｻ").first?.name, "匝瑳市")
        XCTAssertEqual(dictionary.search("千葉 そうさ").first?.name, "匝瑳市")
        XCTAssertTrue(dictionary.search("北海道 そうさ").isEmpty)
    }
    func testHomophonesRemainDistinctAndLimitAndExactRanking() throws {
        let dictionary = try GeographyDictionary(tsv: "01\t北海道\t北海道\tホッカイドウ\n01201\t北海道\t伊達市\tダテシ\n07213\t福島県\t伊達市\tダテシ\n")
        XCTAssertEqual(dictionary.search("だてし").count, 2)
        XCTAssertEqual(dictionary.search("福島 だてし").first?.id, "07213")
        XCTAssertEqual(dictionary.search("だてし", limit: 1).count, 1)
        XCTAssertTrue(dictionary.search(" ").isEmpty)
        XCTAssertTrue(dictionary.search(String(repeating: "あ", count: 101)).isEmpty)
        XCTAssertTrue(dictionary.search("だて", limit: 0).isEmpty)
    }
    func testLookupNeverMutatesTextOrRulesAndExplicitRuleWorks() throws {
        _ = try national().search("そうさ")
        XCTAssertEqual(TextFormatter.format("そうさしへ行く", rules: [], commands: false), "そうさしへ行く")
        XCTAssertEqual(TextFormatter.format("そうさしへ行く", rules: [WordRule(heard: "そうさし", written: "匝瑳市")], commands: false), "匝瑳市へ行く")
    }
    func testInvalidDataIsRejected() {
        for text in ["", "01\t北海道\t北海道\n", "01\t北海道\t北海道\tホッカイドウ\n01\t北海道\t北海道\tホッカイドウ\n", String(repeating: "x", count: 500001)] {
            XCTAssertThrowsError(try GeographyDictionary(tsv: text))
        }
    }
}
