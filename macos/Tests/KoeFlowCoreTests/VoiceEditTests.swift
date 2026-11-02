import Foundation
import XCTest
@testable import KoeFlowCore

final class VoiceEditTests: XCTestCase {
    func testDraftSelectionKeepsOutsideTextAndUnselectedDraftUsesWholeBody() throws {
        let text = "前🙂東京です。東京へ行きます。後"
        let draft = try VoiceEditDraft(text: text, selection: NSRange(location: 3, length: 5))
        XCTAssertEqual(draft.target, "東京です。")
        XCTAssertEqual(try draft.applying("大阪です。", to: text), "前🙂大阪です。東京へ行きます。後")
        XCTAssertFalse(draft.wholeDocument)
        let whole = try VoiceEditDraft(text: text, selection: NSRange(location: 3, length: 0))
        XCTAssertTrue(whole.wholeDocument); XCTAssertEqual(whole.target, text)
        XCTAssertThrowsError(try draft.applying("大阪です。", to: text + "変更"))
        XCTAssertThrowsError(try VoiceEditDraft(text: text, selection: NSRange(location: 2, length: 1)))
    }
    func testExactReplacementChangesAllOccurrencesButPreservesOtherWordsAndEmoji() throws {
        let command = try VoiceEditCommand(instruction: "東京を大阪に置き換えて。")
        XCTAssertEqual(try command.literalResult(original: "🙂東京へ行きます。東京の予定は15時です。東京都も含む。"), "🙂大阪へ行きます。大阪の予定は15時です。大阪都も含む。")
        XCTAssertEqual(try VoiceEditCommand(instruction: "「AをB」を「C」に変えてください。").literalResult(original: "前AをB後"), "前C後")
        XCTAssertEqual(try VoiceEditCommand(instruction: "信楽に置き換えて").literalResult(original: "古い名前"), "信楽")
        XCTAssertEqual(try VoiceEditCommand(instruction: "15を16に変更して").literalResult(original: "15時"), "16時")
    }
    func testMissingAndAmbiguousInstructionsCannotRewriteTheDocument() {
        for instruction in ["文書を送信して", "消して", "新しい予定を作って", "東京を大阪", "をに変えて", "「東京」を「大阪に変えて"] {
            XCTAssertThrowsError(try VoiceEditCommand(instruction: instruction))
        }
        XCTAssertThrowsError(try VoiceEditCommand(instruction: "京都を大阪に直して").literalResult(original: "東京です。"))
        XCTAssertThrowsError(try VoiceEditCommand(instruction: "東京を東京に変更して").literalResult(original: "東京です。"))
        XCTAssertThrowsError(try VoiceEditCommand(instruction: String(repeating: "あ", count: 301)))
    }
    func testLongDocumentLiteralReplacementAndStyleBounds() throws {
        let original = String(repeating: "東京です。", count: 10_000)
        let output = try VoiceEditCommand(instruction: "東京を大阪に置き換えて").literalResult(original: original)!
        XCTAssertEqual(output, String(repeating: "大阪です。", count: 10_000))
        XCTAssertThrowsError(try VoiceEditCommand(instruction: "敬語にして").literalResult(original: original))
        XCTAssertThrowsError(try VoiceEditCommand(instruction: "東京を大阪に置き換えて").literalResult(original: String(repeating: "あ", count: 100001)))
    }
    func testStyleNeedsLocalModelAndEmptySelectionCannotRun() throws {
        XCTAssertNil(try VoiceEditCommand(instruction: "敬語にしてください").literalResult(original: "資料を送ります。"))
        XCTAssertEqual(try VoiceEditCommand(instruction: "句読点を整えて"), .style(.punctuation))
        XCTAssertThrowsError(try VoiceEditCommand(instruction: "信楽に変えて").literalResult(original: " "))
    }
    func testVoiceEditLogIsDistinctAndDoesNotStoreDocumentBody() throws {
        let record = TranscriptRecord(startedAt: Date(), recognized: "東京を大阪に置き換えて", editScope: .document, editedCharacters: 50000)
        // Production voice-edit rows intentionally omit document content.
        let edit = TranscriptRecord(kind: .voiceEdit, startedAt: record.startedAt, recognized: record.recognized,
            editScope: .document, editedCharacters: 50000)
        let data = try JSONEncoder().encode(edit)
        let decoded = try JSONDecoder().decode(TranscriptRecord.self, from: data)
        XCTAssertEqual(decoded.schemaVersion, 2); XCTAssertEqual(decoded.kind, .voiceEdit)
        XCTAssertEqual(decoded.editScope, .document); XCTAssertEqual(decoded.editedCharacters, 50000)
        XCTAssertEqual(decoded.final, "")
        XCTAssertEqual(decoded.recognized, "東京を大阪に置き換えて")
        let legacy = TranscriptRecord(startedAt: Date(), recognized: "以前の音声入力")
        XCTAssertEqual(try JSONDecoder().decode(TranscriptRecord.self, from: JSONEncoder().encode(legacy)), legacy)
    }
}
