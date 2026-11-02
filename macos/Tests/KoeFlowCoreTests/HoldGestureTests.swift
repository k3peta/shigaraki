import XCTest
@testable import KoeFlowCore

final class HoldGestureTests: XCTestCase {
    func testOnlyTargetReleaseStopsAnActiveHold() {
        var gesture = HoldGesture()
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true), .arm)
        XCTAssertEqual(gesture.holdElapsed(), .start)
        XCTAssertEqual(gesture.interaction(), .none) // clicks or other key presses
        XCTAssertEqual(gesture.modifierChanged(isTarget: false, targetDown: false, onlyTarget: false), .none)
        XCTAssertEqual(gesture.state, .recording)
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false), .confirmRelease)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .stop)
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false), .none)
    }
    func testShortcutBeforeThresholdCancelsWithoutStartingOrStopping() {
        var gesture = HoldGesture()
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true), .arm)
        XCTAssertEqual(gesture.interaction(), .cancelPending)
        XCTAssertEqual(gesture.holdElapsed(), .none)
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false), .none)
    }
    func testRightModifierOrChordCannotArm() {
        var gesture = HoldGesture()
        XCTAssertEqual(gesture.modifierChanged(isTarget: false, targetDown: false, onlyTarget: true), .none)
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: false), .none)
        XCTAssertEqual(gesture.state, .idle)
    }
    func testFalseReleaseWhilePhysicalKeyIsHeldDoesNotCommit() {
        var gesture = HoldGesture()
        _ = gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true)
        _ = gesture.holdElapsed()
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false), .confirmRelease)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: true), .none)
        XCTAssertEqual(gesture.state, .recording)
        XCTAssertEqual(gesture.interaction(), .none)
        XCTAssertEqual(gesture.modifierChanged(isTarget: false, targetDown: false, onlyTarget: false), .none)
        XCTAssertEqual(gesture.state, .recording)
        // The later real release still stops exactly once.
        XCTAssertEqual(gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false), .confirmRelease)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .stop)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .none)
    }
    func testReleaseBounceCannotStopUntilKeyStaysUp() {
        var gesture = HoldGesture()
        _ = gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true)
        _ = gesture.holdElapsed()
        _ = gesture.modifierChanged(isTarget: true, targetDown: false, onlyTarget: false)
        _ = gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: true), .none)
        XCTAssertEqual(gesture.state, .recording)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .stop)
    }
    func testLateReleaseConfirmationCannotStopResetOrPendingGesture() {
        var gesture = HoldGesture()
        _ = gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true)
        _ = gesture.holdElapsed()
        _ = gesture.reset() // promotion to continuous mode
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .none)
        _ = gesture.modifierChanged(isTarget: true, targetDown: true, onlyTarget: true)
        XCTAssertEqual(gesture.releaseConfirmed(targetDown: false), .none)
        XCTAssertEqual(gesture.state, .pending)
    }
}
