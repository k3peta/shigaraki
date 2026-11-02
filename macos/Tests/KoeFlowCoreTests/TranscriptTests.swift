import XCTest
@testable import KoeFlowCore
final class TranscriptTests: XCTestCase {
    func testVolatileRevisionAndFinalizationDoNotDuplicate() {
        var t = Transcript()
        t.receive(.init(start: 0, end: 1, text: "今日は", isFinal: false))
        t.receive(.init(start: 0, end: 2, text: "今日は晴れです。", isFinal: false))
        XCTAssertEqual(t.text, "今日は晴れです。")
        t.receive(.init(start: 0, end: 2, text: "今日は晴れです。", isFinal: true))
        t.receive(.init(start: 0, end: 1, text: "今日", isFinal: false))
        t.receive(.init(start: 2, end: 3, text: "散歩します。", isFinal: false))
        XCTAssertEqual(t.text, "今日は晴れです。散歩します。")
        XCTAssertEqual(t.finalText, "今日は晴れです。")
    }
    func testFinalRangeReplacesMultiplePartialPieces() {
        var t = Transcript()
        t.receive(.init(start: 0, end: 1, text: "Hello", isFinal: false))
        t.receive(.init(start: 1, end: 2, text: "world", isFinal: false))
        t.receive(.init(start: 0, end: 2, text: "Hello world.", isFinal: true))
        XCTAssertEqual(t.text, "Hello world.")
    }
    func testDictionaryIsNonCascadingAndDoesNotChangeSubstrings() {
        let rules = [WordRule(heard: "こえきち", written: "声吉"), WordRule(heard: "声吉", written: "別の名前"), WordRule(heard: "git", written: "Git"), WordRule(heard: "こえ", written: "声")]
        XCTAssertEqual(TextFormatter.format("こえきち git digital", rules: rules), "声吉 Git digital")
        XCTAssertEqual(TextFormatter.format("改行して", rules: []), "改行して")
        XCTAssertEqual(TextFormatter.format("改行して", rules: [], commands: true), "\n")
    }
    func testPasteExpectationPreservesUnicodeAndSuffixAndRejectsInvalidRanges() {
        var insertion = TextInsertion(original: "前🙂選択後", selection: NSRange(location: 3, length: 2))!
        insertion.commit("こんにちは🌊")
        XCTAssertEqual(insertion.expected, "前🙂こんにちは🌊後")
        XCTAssertNil(TextInsertion(original: "abc", selection: NSRange(location: 2, length: 8)))
    }
}
