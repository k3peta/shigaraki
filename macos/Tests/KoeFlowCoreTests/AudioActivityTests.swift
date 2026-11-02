import XCTest
@testable import KoeFlowCore

final class AudioActivityTests: XCTestCase {
    func testSilenceAndBelowMeterFloorCannotAuthorizeText() {
        var activity = AudioActivity()
        for _ in 0..<100 {
            activity.receive(rms: 0, frames: 1600, sampleRate: 16000)
            activity.receive(rms: 0.0005, frames: 1600, sampleRate: 16000)
        }
        XCTAssertFalse(activity.hasAudibleInput)
    }
    func testBriefImpulseDoesNotCountButQuietDictationDoes() {
        var activity = AudioActivity()
        activity.receive(rms: 0.2, frames: 320, sampleRate: 16000)
        XCTAssertFalse(activity.hasAudibleInput)
        // Approximately -45 dBFS, the existing quiet speech fixture's level.
        activity.receive(rms: 0.0056, frames: 1600, sampleRate: 16000)
        XCTAssertTrue(activity.hasAudibleInput)
    }
    func testInvalidMeasurementsCannotAuthorizeText() {
        var activity = AudioActivity()
        activity.receive(rms: .nan, frames: 1600, sampleRate: 16000)
        activity.receive(rms: .infinity, frames: 1600, sampleRate: 16000)
        activity.receive(rms: 1, frames: 0, sampleRate: 16000)
        activity.receive(rms: 1, frames: 1600, sampleRate: .infinity)
        XCTAssertFalse(activity.hasAudibleInput)
    }
}
