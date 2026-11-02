import XCTest
@testable import KoeFlowCore

final class RecentContextTests: XCTestCase {
    func testRecentTopicIsBoundedAndOnlyAvailableForIdenticalFieldSnapshot() {
        var memory = RecentContext<String>()
        memory.remember(key: "app:field:hash:selection", text: String(repeating: "古い話題。", count: 100) + "今は桜美林大学です。", now: 10)
        let value = memory.reference(for: "app:field:hash:selection", now: 11)
        XCTAssertEqual(value.count, 80); XCTAssertTrue(value.hasSuffix("今は桜美林大学です。"))
        XCTAssertEqual(memory.reference(for: "app:otherField:hash:selection", now: 12), "")
        XCTAssertEqual(memory.reference(for: "app:field:hash:selection", now: 13), "")
    }
    func testNoRenewalFromReadsAndExpiredTopicCannotReturn() {
        var memory = RecentContext<String>()
        memory.remember(key: "field", text: "話題", now: 10)
        XCTAssertEqual(memory.reference(for: "field", now: 189), "話題")
        XCTAssertEqual(memory.reference(for: "field", now: 190), "話題")
        XCTAssertEqual(memory.reference(for: "field", now: 190.01), "")
        XCTAssertEqual(memory.reference(for: "field", now: 11), "")
    }
    func testManualEditSelectionChangeOrNilTargetClearsTopic() {
        for changed in ["field:newHash:selection", "field:hash:newSelection", nil] {
            var memory = RecentContext<String>()
            memory.remember(key: "field:hash:selection", text: "話題", now: 10)
            XCTAssertEqual(memory.reference(for: changed, now: 11), "")
            XCTAssertEqual(memory.reference(for: "field:hash:selection", now: 12), "")
        }
    }
    func testInvalidRememberAndClockReversalDiscardTopic() {
        var memory = RecentContext<String>()
        for text in ["", " \n "] {
            memory.remember(key: "field", text: "古い話題", now: 10)
            memory.remember(key: "field", text: text, now: 11)
            XCTAssertEqual(memory.reference(for: "field", now: 12), "")
        }
        memory.remember(key: "field", text: "話題", now: 10)
        XCTAssertEqual(memory.reference(for: "field", now: 9), "")
        memory.remember(key: nil, text: "話題", now: 10)
        XCTAssertEqual(memory.reference(for: "field", now: 11), "")
    }
    func testPrivacySettingCanClearMemoryImmediately() {
        var memory = RecentContext<String>()
        memory.remember(key: "field", text: "話題", now: 10); memory.clear()
        XCTAssertEqual(memory.reference(for: "field", now: 11), "")
    }
    func testConfirmedNeutralInputCanAdvanceSnapshotWithoutRenewingTopic() {
        var memory = RecentContext<String>()
        memory.remember(key: "old", text: "桜美林大学", now: 10)
        XCTAssertEqual(memory.reference(for: "old", now: 110), "桜美林大学")
        memory.advance(to: "new", now: 110)
        XCTAssertEqual(memory.reference(for: "new", now: 111), "桜美林大学")
        XCTAssertEqual(memory.reference(for: "new", now: 191), "")
    }
}
