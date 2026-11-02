import Foundation
import XCTest
@testable import KoeFlowCore

final class JapaneseWordRuleTests: XCTestCase {
    func testPreparedFormatterFreezesRulesAndPreservesUnicodeAndCommands() {
        var rules = [WordRule(heard: "しがらき", written: "信楽", matchKana: true)]
        let formatter = TextFormatter.Prepared(rules: rules, commands: true)
        rules[0].written = "別名"
        XCTAssertEqual(formatter.format("🙂ｼｶﾞﾗｷ改行して明日15時"), "🙂信楽\n明日15時")
        XCTAssertEqual(formatter.format("シガラキ"), "信楽")
        XCTAssertEqual(TextFormatter.format("シガラキ", rules: rules), "別名")
    }
    func testLegacyRulesRetainExactBehaviorAndIDs() throws {
        let id = UUID()
        let json = "{\"id\":\"\(id)\",\"heard\":\"そうさし\",\"written\":\"匝瑳市\"}"
        let rule = try JSONDecoder().decode(WordRule.self, from: Data(json.utf8))
        XCTAssertEqual(rule.id, id); XCTAssertNil(rule.bundleID); XCTAssertFalse(rule.matchKana)
        XCTAssertEqual(TextFormatter.format("ソウサシ", rules: [rule]), "ソウサシ")
    }
    func testAllKanaFormsAndVoicedHalfWidthWorkWithoutCascading() {
        let rules = [WordRule(heard: "しがらき", written: "信楽", matchKana: true), WordRule(heard: "信楽", written: "別名")]
        XCTAssertEqual(TextFormatter.format("しがらき シガラキ ｼｶﾞﾗｷ", rules: rules), "信楽 信楽 信楽")
        XCTAssertEqual(TextFormatter.format("そうさし ソウサシ ｿｳｻｼ", rules: [WordRule(heard: "ｿｳｻｼ", written: "匝瑳市", matchKana: true)]), "匝瑳市 匝瑳市 匝瑳市")
    }
    func testApplicationVocabularyIsIsolatedAndOverridesCommon() {
        let shared = WordRule(heard: "こーど", written: "コード", matchKana: true)
        let mail = WordRule(heard: "こーど", written: "口頭", bundleID: "mail", matchKana: true)
        let rules = [shared, mail]
        XCTAssertEqual(TextFormatter.format("コート こーど", rules: WordRule.resolve(rules, bundleID: "mail")), "コート 口頭")
        XCTAssertEqual(TextFormatter.format("こーど", rules: WordRule.resolve(rules, bundleID: "editor")), "コード")
        XCTAssertEqual(WordRule.resolve(rules, bundleID: ""), [shared])
        XCTAssertEqual(WordRule.resolve([mail], bundleID: "editor"), [])
    }
    func testExactConflictsDeterministicAndNoFuzzyGuessing() {
        let rules = [WordRule(heard: "そうさし", written: "匝瑳市", matchKana: true), WordRule(heard: "ソウサシ", written: "別表記", matchKana: true)]
        XCTAssertEqual(TextFormatter.format("ソウサシ 操作 そうさ", rules: rules), "匝瑳市 操作 そうさ")
        let mixed = WordRule(heard: "APIそうさ", written: "API操作", matchKana: true)
        XCTAssertEqual(mixed.heardForms, ["APIそうさ"])
        XCTAssertEqual(TextFormatter.format("APIソウサ", rules: [mixed]), "APIソウサ")
        XCTAssertEqual(TextFormatter.format("digital git", rules: [WordRule(heard: "git", written: "Git", matchKana: true)]), "digital Git")
    }
    func testPersistenceAndDuplicateKeyRespectScopeAndKanaOptIn() throws {
        let rule = WordRule(heard: "しがらき", written: "信楽", bundleID: "editor", matchKana: true)
        XCTAssertEqual(try JSONDecoder().decode(WordRule.self, from: JSONEncoder().encode(rule)), rule)
        XCTAssertTrue(rule.hasSameKey(as: WordRule(heard: "シガラキ", written: "別名", bundleID: "editor", matchKana: true)))
        XCTAssertFalse(rule.hasSameKey(as: WordRule(heard: "しがらき", written: "別名", bundleID: "mail", matchKana: true)))
        XCTAssertFalse(rule.hasSameKey(as: WordRule(heard: "シガラキ", written: "別名", bundleID: "editor", matchKana: false)))
    }
}
