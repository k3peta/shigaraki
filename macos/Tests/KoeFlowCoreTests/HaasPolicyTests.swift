import XCTest
@testable import KoeFlowCore

final class HaasPolicyTests: XCTestCase {
    func testFixedModelMatchesWindowsPin() {
        let pin = ModelPin.haasJapanese
        XCTAssertEqual(pin.bytes, 351_901_216)
        XCTAssertEqual(pin.sha256, "d24d53f29b75214a933ff482b0de1217e0a800f4118cca39ad6cf03609a89d6f")
        XCTAssertTrue(pin.url.path.contains(pin.revision)); XCTAssertTrue(DownloadPolicy.permits(pin.url))
    }
    func testRedirectsRequireHTTPSAndCannotEmbedCredentials() {
        for s in ["http://cdn.example/model", "file:///tmp/model", "https://u:p@example.com/model", "https://example.com/model#fragment"] {
            XCTAssertFalse(DownloadPolicy.permits(URL(string: s)!))
        }
        XCTAssertTrue(DownloadPolicy.permits(URL(string: "https://cdn.example/model?signature=temporary")!))
    }
    func testExternalModelCannotBeRepaired() {
        XCTAssertFalse(DownloadPolicy.mayRepair(URL(fileURLWithPath: "/tmp/user/model.gguf"), ownedURL: URL(fileURLWithPath: "/tmp/app/model.gguf")))
    }
    func testCancelledAndOldEventsCannotEnterNextSession() throws {
        var state = HaasTranscriptState(); try state.begin(1)
        let old = try HaasEvent.decode(Data(#"{"type":"caption","id":1,"text":"old","final":false}"#.utf8))
        XCTAssertTrue(try state.receive(old)); state.cancel(); try state.begin(2)
        XCTAssertFalse(try state.receive(old)); XCTAssertEqual(state.text, "")
        XCTAssertTrue(try state.receive(HaasEvent.decode(Data(#"{"type":"complete","id":2,"text":"new"}"#.utf8))))
        XCTAssertEqual(state.text, "new"); XCTAssertEqual(state.finalText, "new")
    }
    func testEmptyFinalCannotTurnDraftIntoSuccessfulInput() throws {
        var state = HaasTranscriptState(); try state.begin(3)
        _ = try state.receive(HaasEvent.decode(Data(#"{"type":"caption","id":3,"text":"draft","final":false}"#.utf8)))
        XCTAssertThrowsError(try state.receive(HaasEvent.decode(Data(#"{"type":"complete","id":3,"text":""}"#.utf8))))
        XCTAssertEqual(state.text, "draft")
    }
    func testMalformedReadyAndOversizedMessagesAreRejected() {
        XCTAssertThrowsError(try HaasEvent.decode(Data(#"{"type":"ready","protocol":1,"engine":"other","device":"CPU","threads":6}"#.utf8)))
        XCTAssertThrowsError(try HaasEvent.decode(Data(repeating: 32, count: HaasEvent.maximumLineBytes + 1)))
    }
    func testProgressCannotExceedItsRange() {
        XCTAssertEqual(HaasPreparationProgress(stage: .downloading, bytes: 20, total: 10).fraction, 1)
        XCTAssertEqual(HaasPreparationProgress(stage: .ready).fraction, 1)
    }
    func testCaptureClipsAndSealsBeforeRust305SecondLimitIncludingFlush() {
        var budget = HaasSampleBudget()
        XCTAssertEqual(budget.take(HaasSampleBudget.maximum - 17), HaasSampleBudget.maximum - 17)
        XCTAssertEqual(budget.take(2048), 17)
        XCTAssertTrue(budget.exhausted)
        XCTAssertEqual(budget.take(32), 0) // converter flush cannot exceed the same budget
        XCTAssertLessThan(budget.accepted, 305 * 16000)
    }
    func testDownloadBudgetRejectsOversizeBeforeWritingAndDoesNotOverflow() {
        var budget = DownloadByteBudget(maximum: 10)
        XCTAssertTrue(budget.accept(4)); XCTAssertEqual(budget.received, 4)
        XCTAssertFalse(budget.accept(7)); XCTAssertEqual(budget.received, 4)
        XCTAssertFalse(budget.accept(Int.max)); XCTAssertFalse(budget.accept(-1))
        XCTAssertTrue(budget.accept(6)); XCTAssertTrue(budget.complete)
        XCTAssertFalse(budget.accept(1)); XCTAssertEqual(budget.received, 10)
    }
    func testDownloadUnknownLengthStillHasAnActualByteLimit() {
        var budget = DownloadByteBudget(maximum: 20)
        XCTAssertTrue(budget.permitsDeclaredLength(-1))
        XCTAssertTrue(budget.permitsDeclaredLength(20)); XCTAssertFalse(budget.permitsDeclaredLength(21))
        XCTAssertTrue(budget.accept(19)); XCTAssertFalse(budget.complete)
        XCTAssertFalse(budget.accept(2)); XCTAssertEqual(budget.received, 19)
    }
}
