import Foundation

/// Only verifies a paste already sent once. Never retries or changes the field.
@MainActor enum PasteReceipt {
    enum Observation { case value(String), unavailable, targetChanged }
    enum Outcome: String { case confirmed, timedOut, targetChanged, cancelled }

    static func wait(expected: String, observe: () -> Observation) async -> Outcome {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .milliseconds(750))
        while clock.now < deadline {
            do { try await Task.sleep(for: .milliseconds(25)) }
            catch { return .cancelled }
            guard !Task.isCancelled else { return .cancelled }
            switch observe() {
            case .targetChanged: return .targetChanged
            case .value(let value) where value == expected: return .confirmed
            default: break
            }
        }
        return .timedOut
    }

    /// Synthetic observations exercise the same wait path without reading any app.
    static func selfTest() async -> [String: Any] {
        let expected = "前🙂長い文章の確認。\n\n後"
        var cases: [[String: Any]] = []
        for delay in [0, 300, 600] {
            let start = ContinuousClock.now
            let result = await wait(expected: expected) {
                .value(ContinuousClock.now - start >= .milliseconds(delay) ? expected : "前🙂\n\n後")
            }
            cases.append(["case": "valueUpdatedAfter\(delay)ms", "passed": result == .confirmed,
                          "outcome": result.rawValue])
        }
        let different = await wait(expected: expected) { .value(expected + "別の入力") }
        cases.append(["case": "differentTextNeverConfirmed", "passed": different == .timedOut])
        let unavailable = await wait(expected: expected) { .unavailable }
        cases.append(["case": "unreadableValueNeverConfirmed", "passed": unavailable == .timedOut])
        let changed = await wait(expected: expected) { .targetChanged }
        cases.append(["case": "focusChangeStopsVerification", "passed": changed == .targetChanged])
        var reads = 0
        let cancelled = Task { @MainActor in
            await wait(expected: expected) { reads += 1; return .value(expected) }
        }
        cancelled.cancel()
        let outcome = await cancelled.value
        cases.append(["case": "cancelledVerificationDoesNotRead", "passed": outcome == .cancelled && reads == 0])
        return ["passed": cases.allSatisfy { $0["passed"] as? Bool == true }, "cases": cases,
                "recordsFieldText": false, "postsPaste": false]
    }
}
