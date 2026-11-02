import XCTest
@testable import KoeFlowCore

final class WindowInputGestureTests: XCTestCase {
    private typealias Action = WindowInputGesture.Action

    private func sample(_ gesture: inout WindowInputGesture, _ time: Double, down: Bool = false,
                        isolated: Bool = true, canToggle: Bool = true, recording: Bool = false) -> Action {
        gesture.update(targetDown: down, isolated: isolated, time: time,
                       canToggle: canToggle, recording: recording)
    }

    @discardableResult
    private func tap(_ gesture: inout WindowInputGesture, _ time: Double,
                     canToggle: Bool = true, recording: Bool = false) -> Action {
        XCTAssertEqual(sample(&gesture, time, down: true, canToggle: canToggle, recording: recording), .none)
        XCTAssertEqual(sample(&gesture, time + 0.05, canToggle: canToggle, recording: recording), .none)
        return sample(&gesture, time + 0.13, canToggle: canToggle, recording: recording)
    }

    func testIdleSingleWaitsForDoubleWindow() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        XCTAssertTrue(gesture.needsPolling)
        XCTAssertEqual(sample(&gesture, 0.39), .none)
        XCTAssertEqual(sample(&gesture, 0.41), .singleTap)
        XCTAssertFalse(gesture.needsPolling)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testIdleDoubleNeverStartsRecording() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        XCTAssertEqual(tap(&gesture, 0.18), .doubleTap)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testRecordingSingleStopsAfterStableRelease() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.05, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.10, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.13, recording: true), .singleTap)
        XCTAssertEqual(sample(&gesture, 0.5, canToggle: false), .none)
    }

    func testRecordingStopThenFinishingDoubleCommits() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0, recording: true), .singleTap)
        XCTAssertEqual(tap(&gesture, 0.18, canToggle: false), .doubleTap)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testRecordingStopThenFastIdleDoubleCommits() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0, recording: true), .singleTap)
        XCTAssertEqual(sample(&gesture, 0.15, canToggle: false), .none)
        XCTAssertEqual(tap(&gesture, 0.18), .doubleTap)
    }

    func testFreshFinishingDoubleCommitsWithoutStart() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0, canToggle: false), .none)
        XCTAssertEqual(tap(&gesture, 0.18, canToggle: false), .doubleTap)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testFinishingSingleCannotStartAfterIdle() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0, canToggle: false), .none)
        XCTAssertEqual(sample(&gesture, 0.2), .none)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
        XCTAssertEqual(tap(&gesture, 1), .none)
        XCTAssertEqual(sample(&gesture, 1.41), .singleTap)
    }

    func testFinishingToIdleKeepsDoubleOnlyIntent() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0, canToggle: false), .none)
        XCTAssertEqual(tap(&gesture, 0.18), .doubleTap)
    }

    func testSinglePendingBeforeBusyCannotStartAfterIdle() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        XCTAssertEqual(sample(&gesture, 0.2, canToggle: false), .none)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
    }

    func testBusyHeldKeyCannotBecomeRecordingToggle() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true, canToggle: false), .none)
        XCTAssertEqual(sample(&gesture, 0.3, down: true), .none)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
        XCTAssertEqual(sample(&gesture, 0.58), .none)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testRecordingStateChangeBeforeReleaseCancelsSingle() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.05, canToggle: false), .none)
        XCTAssertEqual(sample(&gesture, 0.13), .none)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
    }

    func testChordCannotToggleOrCreateDouble() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true), .none)
        XCTAssertEqual(sample(&gesture, 0.02, down: true, isolated: false), .none)
        XCTAssertEqual(sample(&gesture, 0.05), .none)
        XCTAssertEqual(sample(&gesture, 0.13), .none)
        XCTAssertEqual(tap(&gesture, 0.18), .none)
        XCTAssertEqual(sample(&gesture, 0.59), .singleTap)
    }

    func testTypingCancelsPendingIdleSingle() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        gesture.interaction()
        XCTAssertEqual(sample(&gesture, 0.5), .none)
    }

    func testTypingWhileHeldBlocksRelease() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true), .none)
        gesture.interaction()
        XCTAssertEqual(sample(&gesture, 0.05), .none)
        XCTAssertEqual(sample(&gesture, 0.13), .none)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
    }

    func testFalseReleaseDoesNotStopOrBecomeSecondTap() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.05, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.10, down: true, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.2, down: true, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.3, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.38, recording: true), .singleTap)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testUnknownReleaseCannotCreateSingleOrDouble() {
        var gesture = WindowInputGesture()
        for time in [0.0, 0.1, 0.2, 0.5] {
            XCTAssertEqual(sample(&gesture, time), .none)
        }
        XCTAssertFalse(gesture.needsPolling)
    }

    func testLongPressTogglesOnceOnReleaseButCannotDouble() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true), .none)
        XCTAssertEqual(sample(&gesture, 1, down: true), .none)
        XCTAssertEqual(sample(&gesture, 2, down: true), .none)
        XCTAssertEqual(sample(&gesture, 2.1), .none)
        XCTAssertEqual(sample(&gesture, 2.18), .singleTap)
        XCTAssertEqual(tap(&gesture, 2.3, recording: true), .singleTap)
        XCTAssertEqual(sample(&gesture, 3), .none)
    }

    func testResetDropsPendingSingleAndDouble() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        gesture.reset()
        XCTAssertEqual(sample(&gesture, 1), .none)
        XCTAssertEqual(tap(&gesture, 1.2), .none)
    }

    func testHeldResetRequiresStableUpAndDoesNotStart() {
        var gesture = WindowInputGesture()
        gesture.reset(blockUntilUp: true)
        XCTAssertEqual(sample(&gesture, 0, down: true), .none)
        XCTAssertEqual(sample(&gesture, 0.1), .none)
        XCTAssertEqual(sample(&gesture, 0.15, down: true), .none)
        XCTAssertEqual(sample(&gesture, 0.3), .none)
        XCTAssertEqual(sample(&gesture, 0.34), .none)
        XCTAssertTrue(gesture.blocked)
        XCTAssertEqual(sample(&gesture, 0.38), .none)
        XCTAssertFalse(gesture.blocked)
        XCTAssertEqual(sample(&gesture, 0.5), .none)
        XCTAssertEqual(tap(&gesture, 0.6), .none)
        XCTAssertEqual(sample(&gesture, 1.01), .singleTap)
    }

    func testResetWhileRecordingCannotCarryPressedKeyToNewSession() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(sample(&gesture, 0, down: true, recording: true), .none)
        gesture.reset(blockUntilUp: true)
        XCTAssertEqual(sample(&gesture, 0.05, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.13, recording: true), .none)
        XCTAssertEqual(sample(&gesture, 0.5, recording: true), .none)
    }

    func testNonFiniteTimeCannotEmitAction() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 0), .none)
        XCTAssertEqual(sample(&gesture, .nan), .none)
        XCTAssertEqual(sample(&gesture, 1), .none)
    }

    func testBackwardsTimeCancelsPendingAndBlocksHeldKey() {
        var gesture = WindowInputGesture()
        XCTAssertEqual(tap(&gesture, 1), .none)
        XCTAssertEqual(sample(&gesture, 0, down: true), .none)
        XCTAssertTrue(gesture.blocked)
        XCTAssertEqual(sample(&gesture, 2), .none)
        XCTAssertEqual(sample(&gesture, 2.08), .none)
        XCTAssertEqual(sample(&gesture, 3), .none)
    }
}
