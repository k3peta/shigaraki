import XCTest
@testable import KoeFlowCore

final class ChordGestureTests: XCTestCase {
    func testEitherOrderAndReleaseOrderFireExactlyOnce() {
        for first: UInt8 in [1, 2] {
            for remaining: UInt8 in [1, 2] {
                var gesture = ChordGesture()
                XCTAssertFalse(gesture.update(modifiers: first, chord: 3))
                XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
                XCTAssertTrue(gesture.armed)
                XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
                XCTAssertFalse(gesture.update(modifiers: remaining, chord: 3))
                XCTAssertTrue(gesture.update(modifiers: 0, chord: 3))
                XCTAssertFalse(gesture.update(modifiers: 0, chord: 3))
            }
        }
    }
    func testTypingOrClickingPreventsShortcutUntilAllKeysReleased() {
        var gesture = ChordGesture()
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
        gesture.interaction()
        XCTAssertFalse(gesture.update(modifiers: 1, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 0, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
        XCTAssertTrue(gesture.update(modifiers: 0, chord: 3))
    }
    func testExtraModifierAndConfigurationChangeDoNotFire() {
        var gesture = ChordGesture()
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 7, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 0, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 3))
        XCTAssertFalse(gesture.update(modifiers: 0, chord: 5))
        XCTAssertFalse(gesture.update(modifiers: 3, chord: 0))
        XCTAssertFalse(gesture.update(modifiers: 0, chord: 0))
    }
}
