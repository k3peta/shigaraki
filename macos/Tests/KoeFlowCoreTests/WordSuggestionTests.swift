import XCTest
@testable import KoeFlowCore

final class WordSuggestionTests: XCTestCase {
    private let candidate = WordRule(heard: "構成", written: "校正", mode: .suggestion)
    func testLegacyRegistrationKeepsAutomaticModeAndIdentity() throws {
        let id = UUID()
        let data = Data("{\"id\":\"\(id)\",\"heard\":\"構成\",\"written\":\"校正\"}".utf8)
        let rule = try JSONDecoder().decode(WordRule.self, from: data)
        XCTAssertEqual(rule.mode, .automatic); XCTAssertEqual(rule.id, id)
        XCTAssertEqual(TextFormatter.format("構成", rules: [rule]), "校正")
        XCTAssertEqual(try JSONDecoder().decode(WordRule.self, from: JSONEncoder().encode(candidate)), candidate)
    }
    func testSuggestionIsNotAutomaticallyAppliedOrRewritten() {
        let original = "原稿の構成と章の構成を確認します"
        XCTAssertEqual(TextFormatter.format(original, rules: [candidate]), original)
        XCTAssertEqual(WritingValidation.rejection(original: original,
            result: "原稿の校正と章の構成を確認します。", rules: [candidate]), "dictionary")
        XCTAssertNil(WritingValidation.rejection(original: original,
            result: "原稿の構成と章の構成を確認します。", rules: [candidate]))
        XCTAssertNil(WritingValidation.rejection(original: "原稿の校正と章の構成を確認します",
            result: "原稿の校正と章の構成を確認します。", rules: [candidate]))
    }
    func testOneOccurrenceAndStaleSnapshotWithEmoji() {
        let original = "🙂原稿の構成です。章の構成です。"
        let matches = WordSuggestions.matches(in: original, rules: [candidate])
        XCTAssertEqual(matches.count, 2)
        XCTAssertEqual(matches[0].applying("校正", to: original), "🙂原稿の校正です。章の構成です。")
        XCTAssertEqual(matches[1].applying("校正", to: original), "🙂原稿の構成です。章の校正です。")
        XCTAssertNil(matches[0].applying("校正", to: "別の文章" + original))
        XCTAssertNil(matches[0].applying("別の語", to: original))
    }
    func testAppSuggestionOverridesSharedAutomaticAndViceVersa() {
        let common = WordRule(heard: "構成", written: "校正")
        let app = WordRule(heard: "構成", written: "更生", bundleID: "test", mode: .suggestion)
        let selected = WordRule.resolve([common, app], bundleID: "test")
        XCTAssertEqual(selected, [app])
        XCTAssertEqual(TextFormatter.format("構成", rules: selected), "構成")
        XCTAssertEqual(WordSuggestions.matches(in: "構成", rules: selected).first?.replacements, ["更生"])
        XCTAssertEqual(TextFormatter.format("構成", rules: WordRule.resolve([common, app], bundleID: "other")), "校正")
        let automatic = WordRule(heard: "構成", written: "構成", bundleID: "test")
        XCTAssertTrue(WordSuggestions.matches(in: "構成", rules: WordRule.resolve([candidate, automatic], bundleID: "test")).isEmpty)
    }
    func testKanaBoundariesAndLongerMatches() {
        let kana = WordRule(heard: "こうせい", written: "校正", matchKana: true, mode: .suggestion)
        XCTAssertEqual(WordSuggestions.matches(in: "ｺｳｾｲ コウセイ", rules: [kana]).count, 2)
        let latin = WordRule(heard: "git", written: "Git", mode: .suggestion)
        XCTAssertEqual(WordSuggestions.matches(in: "digital git", rules: [latin]).count, 1)
        let longer = WordRule(heard: "構成案", written: "校正案", mode: .suggestion)
        XCTAssertEqual(WordSuggestions.matches(in: "構成案と構成", rules: [candidate, longer]).map(\.source), ["構成案", "構成"])
        XCTAssertEqual(WordSuggestions.matches(in: "構成構成", rules: [candidate], limit: 1).count, 1)
        XCTAssertTrue(WordSuggestions.matches(in: String(repeating: "構成", count: 50_001), rules: [candidate]).isEmpty)
    }
}
