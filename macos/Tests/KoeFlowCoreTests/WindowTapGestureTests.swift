import XCTest
@testable import KoeFlowCore

final class WindowTapGestureTests: XCTestCase {
    func testTwoQuickTapsToggleOnce() {
        var gesture = WindowTapGesture()
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0, busy: false))
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.06, busy: false))
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0.18, busy: false))
        XCTAssertTrue(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.24, busy: false))
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.25, busy: false))
    }
    func testLongHoldAndSlowTapsDoNotToggle() {
        for firstEnd in [0.3, 0.06] {
            var gesture = WindowTapGesture()
            _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0, busy: false)
            _ = gesture.update(isTarget: true, targetDown: false, isolated: true, time: firstEnd, busy: false)
            _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0.8, busy: false)
            XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.86, busy: false))
        }
    }
    func testTypingChordsOrAnotherModifierInvalidateTheSequence() {
        for isolated in [true, false] {
            var gesture = WindowTapGesture()
            _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0, busy: false)
            _ = gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.05, busy: false)
            _ = gesture.update(isTarget: false, targetDown: true, isolated: isolated, time: 0.10, busy: false)
            _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0.18, busy: false)
            XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.24, busy: false))
        }
    }
    func testRecordingAndFinishingNeverToggleOrLeaveAPendingTap() {
        var gesture = WindowTapGesture()
        _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0, busy: false)
        _ = gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.05, busy: false)
        _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0.18, busy: true)
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.24, busy: true))
        _ = gesture.update(isTarget: true, targetDown: true, isolated: true, time: 0.30, busy: false)
        XCTAssertFalse(gesture.update(isTarget: true, targetDown: false, isolated: true, time: 0.35, busy: false))
    }
}
