import XCTest
@testable import KoeFlowCore

final class ContextVocabularyTests: XCTestCase {
    private let obirin = WordRule(heard: "おうびりんだいがく", written: "桜美林大学", matchKana: true)
    func testTopicReferenceKeepsOnlyCompleteAutomaticRegisteredTerms() {
        let rules = [obirin, WordRule(heard: "おうびりん", written: "桜美林"), WordRule(heard: "こうせい", written: "校正", mode: .suggestion)]
        XCTAssertEqual(ContextVocabulary.topicReference(in: "桜美林大学で校正します。未登録大学です。", rules: rules), "桜美林大学")
        XCTAssertEqual(ContextVocabulary.topicReference(in: "未登録大学です。", rules: rules), "")
    }
    func testCurrentTopicRescuesDictionaryEntryOutsideHintBudget() {
        let rules = (0..<80).map { WordRule(heard: "よみ\($0)", written: "用語\($0)") } + [obirin]
        XCTAssertFalse(ContextVocabulary.recognitionHints(rules: rules, context: .init()).contains("桜美林大学"))
        let context = WritingContext(before: "桜美林大学の授業について相談します。")
        let hints = ContextVocabulary.recognitionHints(rules: rules, context: context)
        XCTAssertEqual(hints.first, "桜美林大学"); XCTAssertEqual(hints.count, 64)
        XCTAssertFalse(ContextVocabulary.recognitionHints(rules: rules, context: context.limited(to: .appOnly)).contains("桜美林大学"))
    }
    func testGlossaryLinksMisrecognizedNameToReadingOnlyWithEvidence() {
        let source = "桜尾林大学で授業をします。"
        XCTAssertTrue(ContextVocabulary.glossary(rules: [obirin], original: source, context: .init()).isEmpty)
        let glossary = ContextVocabulary.glossary(rules: [obirin], original: source,
            context: .init(before: "桜美林大学の授業について。"))
        XCTAssertEqual(glossary.map(\.written), ["桜美林大学"])
        XCTAssertTrue(glossary[0].readings.contains("おうびりんだいがく"))
        XCTAssertTrue(ContextVocabulary.glossary(rules: [obirin], original: "オウビリンダイガクです。", context: .init()).count == 1)
    }
    func testSuggestionsAndReplacedSelectionNeverSteerRecognitionOrRewrite() {
        var suggestion = obirin; suggestion.mode = .suggestion
        let context = WritingContext(selected: "桜美林大学")
        XCTAssertTrue(ContextVocabulary.glossary(rules: [obirin], original: "本文です。", context: context).isEmpty)
        XCTAssertTrue(ContextVocabulary.glossary(rules: [suggestion], original: "おうびりんだいがく", context: context).isEmpty)
        XCTAssertTrue(ContextVocabulary.recognitionHints(rules: [suggestion], context: context).isEmpty)
    }
    func testAppSpecificSpellingWinsAndPreviousAppHasNoInfluence() {
        let rules = [obirin, WordRule(heard: obirin.heard, written: "OBIRIN", bundleID: "app.one", matchKana: true)]
        let local = WordRule.resolve(rules, bundleID: "app.one")
        XCTAssertEqual(ContextVocabulary.recognitionHints(rules: local, context: .init(before: "桜美林大学")), ["OBIRIN"])
        XCTAssertEqual(ContextVocabulary.recognitionHints(rules: WordRule.resolve(rules, bundleID: "app.two"), context: .init()), ["桜美林大学"])
    }
    func testASCIIWordsDoNotMatchInsideUnrelatedWords() {
        let ai = WordRule(heard: "えーあい", written: "AI")
        XCTAssertTrue(ContextVocabulary.glossary(rules: [ai], original: "emailを送ります。", context: .init()).isEmpty)
        XCTAssertEqual(ContextVocabulary.glossary(rules: [ai], original: "aiについて。", context: .init()).count, 1)
    }
    func testGlossaryIsBoundedAndDoesNotAddArbitraryContextWords() {
        let rules = (0..<100).map { WordRule(heard: "読み\($0)", written: "用語\($0)") }
        let glossary = ContextVocabulary.glossary(rules: rules, original: rules.map(\.written).joined(separator: " "), context: .init(before: "未登録の地名"))
        XCTAssertLessThanOrEqual(glossary.count, 16)
        XCTAssertLessThanOrEqual(glossary.reduce(0) { $0 + $1.written.count + $1.readings.reduce(0) { $0 + $1.count } }, 600)
        XCTAssertFalse(glossary.map(\.written).contains("未登録の地名"))
        let invalid = [WordRule(heard: "よみ", written: "悪\n語"), WordRule(heard: "よみ", written: String(repeating: "長", count: 81))]
        XCTAssertTrue(ContextVocabulary.recognitionHints(rules: invalid, context: .init()).isEmpty)
    }
    func testContextCannotOverrideCorrectNameNumbersOrNegation() {
        let rules = [obirin, WordRule(heard: "めいじだいがく", written: "明治大学")]
        let source = "明治大学には15日に行きません。"
        XCTAssertEqual(WritingValidation.rejection(original: source, result: "桜美林大学には15日に行きません。", rules: rules), "dictionary")
        XCTAssertEqual(WritingValidation.rejection(original: source, result: "明治大学には16日に行きません。", rules: rules), "facts")
        XCTAssertEqual(WritingValidation.rejection(original: source, result: "明治大学には15日に行きます。", rules: rules), "negation")
        XCTAssertEqual(WritingValidation.rejection(original: "明日は行かない。", result: "明日は行く。"), "negation")
        XCTAssertEqual(WritingValidation.rejection(original: "明日は行きます。", result: "明日は行きません。"), "negation")
        XCTAssertEqual(WritingValidation.rejection(original: "行かない、送らない。", result: "行かない、送る。"), "negation")
        XCTAssertEqual(WritingValidation.rejection(original: "「汗田大学」という誤字を残します。", result: "「早稲田大学」という誤字を残します。"), "quotation")
    }
    func testCopiedReferenceAndKanaOutputAreRejectedButLocalCorrectionIsAccepted() {
        let source = "桜尾林大学で授業をします。"
        let glossary = ContextVocabulary.glossary(rules: [obirin], original: source, context: .init(before: "桜美林大学の授業について。"))
        XCTAssertEqual(ContextVocabulary.rejection(original: source, result: "桜美林大学の授業について。", glossary: glossary), "contextRewrite")
        XCTAssertNil(ContextVocabulary.rejection(original: source, result: "桜美林大学で授業をします。", glossary: glossary))
        XCTAssertNil(ContextVocabulary.rejection(original: source, result: "桜尾林大学で\n授業をします。", glossary: glossary))
        XCTAssertEqual(ContextVocabulary.rejection(original: source, result: "オウビリンダイガクで授業をします。", glossary: glossary), "dictionaryReading")
    }
}
