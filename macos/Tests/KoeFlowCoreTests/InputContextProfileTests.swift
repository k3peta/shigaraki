import XCTest
@testable import KoeFlowCore

final class InputContextProfileTests: XCTestCase {
    func testProfileLimitAllowsExistingEditButRefusesNewAddition() {
        let profiles = (0..<64).map { InputContextProfile(kind: .application, identifier: "com.example.app\($0)") }
        XCTAssertTrue(InputContextProfile.canAddOrEdit(profiles, kind: .application, identifier: "com.example.app0"))
        XCTAssertFalse(InputContextProfile.canAddOrEdit(profiles, kind: .application, identifier: "com.example.additional"))
        XCTAssertTrue(InputContextProfile.canAddOrEdit(Array(profiles.prefix(63)), kind: .application, identifier: "com.example.additional"))
        XCTAssertFalse(InputContextProfile.canAddOrEdit(profiles, kind: .website, identifier: "example.com"))
    }
    func testRestorationCapsAtFirst64AndSkipsInvalidRowsWithoutMutation() throws {
        let profiles = (0..<65).map { InputContextProfile(kind: .application, identifier: "com.example.app\($0)") }
        let original = try JSONEncoder().encode(profiles)
        let restored = InputContextProfile.restored(from: original)
        XCTAssertEqual(restored.count, 64); XCTAssertEqual(restored.last?.identifier, "com.example.app63")
        XCTAssertEqual(try JSONDecoder().decode([InputContextProfile].self, from: original).count, 65)
        var rows = try XCTUnwrap(JSONSerialization.jsonObject(with: original) as? [Any])
        rows[0] = ["kind": "invalid"]
        let invalid = InputContextProfile.restored(from: try JSONSerialization.data(withJSONObject: rows))
        XCTAssertEqual(invalid.count, 63); XCTAssertFalse(invalid.contains { $0.identifier == "com.example.app64" })
        XCTAssertTrue(InputContextProfile.restored(from: Data(repeating: 32, count: InputContextProfile.maximumStoredBytes + 1)).isEmpty)
        XCTAssertTrue(InputContextProfile.restored(from: Data("{\"not\":\"an array\"}".utf8)).isEmpty)
    }
    func testEntriesBeyond64CannotTriggerLookupOrMatching() {
        let profiles = (0..<64).map { InputContextProfile(kind: .application, identifier: "com.example.app\($0)") }
        let extra = InputContextProfile(kind: .website, identifier: "example.com", topic: "65件目")
        var calls = 0
        let result = InputContextSupplement.resolve(profiles + [extra], bundleID: "com.example.editor", scope: .nearby, excluded: false) {
            calls += 1; return "example.com"
        }
        XCTAssertEqual(calls, 0); XCTAssertEqual(result, InputContextSupplement())
        XCTAssertEqual(InputContextSupplement.resolve(profiles + [extra], bundleID: "com.example.editor", siteHost: "example.com"), InputContextSupplement())
    }
    func testSiteLookupIsLazyAndRespectsExistingScopeAndExclusion() {
        let app = InputContextProfile(kind: .application, identifier: "com.example.editor", topic: "手入力の補足")
        let site = InputContextProfile(kind: .website, identifier: "example.com", terms: ["Codex"])
        var disabled = site; disabled.enabled = false
        var calls = 0
        let lookup = { calls += 1; return Optional("example.com") }
        _ = InputContextSupplement.resolve([], bundleID: app.identifier, scope: .nearby, excluded: false, siteHost: lookup)
        _ = InputContextSupplement.resolve([app, disabled], bundleID: app.identifier, scope: .nearby, excluded: false, siteHost: lookup)
        let appOnly = InputContextSupplement.resolve([app, site], bundleID: app.identifier, scope: .appOnly, excluded: false, siteHost: lookup)
        XCTAssertEqual(appOnly.topic, app.topic); XCTAssertTrue(appOnly.terms.isEmpty)
        let excluded = InputContextSupplement.resolve([app, site], bundleID: app.identifier, scope: .nearby, excluded: true, siteHost: lookup)
        XCTAssertEqual(excluded, InputContextSupplement()); XCTAssertEqual(calls, 0)
        let included = InputContextSupplement.resolve([app, site], bundleID: app.identifier, scope: .nearby, excluded: false, siteHost: lookup)
        XCTAssertEqual(included.terms, ["Codex"]); XCTAssertEqual(calls, 1)
    }
    func testEmptyOptionsPreserveExistingHints() {
        let supplement = InputContextSupplement.resolve([], bundleID: "com.example.editor", siteHost: "example.com")
        XCTAssertEqual(supplement.topic, "")
        XCTAssertEqual(supplement.recognitionHints(fallback: ["Codex", "信楽"]), ["Codex", "信楽"])
    }
    func testSiteHostOnlyIsSavedAndMatchedExactly() throws {
        let profile = try XCTUnwrap(InputContextProfile(kind: .website, identifier: "https://EXAMPLE.com/private?q=secret#fragment", topic: "AIへの指示", terms: ["Antigravity"]).normalized)
        XCTAssertEqual(profile.identifier, "example.com")
        let encoded = String(decoding: try JSONEncoder().encode(profile), as: UTF8.self)
        XCTAssertFalse(encoded.contains("private")); XCTAssertFalse(encoded.contains("secret")); XCTAssertFalse(encoded.contains("fragment"))
        XCTAssertEqual(InputContextSupplement.resolve([profile], bundleID: "com.google.Chrome", siteHost: "EXAMPLE.com").terms, ["Antigravity"])
        for host in [nil, "sub.example.com", "example.com.evil.test", "other.com"] {
            XCTAssertEqual(InputContextSupplement.resolve([profile], bundleID: "com.google.Chrome", siteHost: host).terms, [])
        }
    }
    func testInvalidSiteIdentifiersAreRefused() {
        for site in ["", "file:///private", "ftp://example.com", "javascript:alert(1)", "https://user:pass@example.com", "https://user@example.com", "https://bad host.com", "https://example..com", "https://-bad.com", "https://[::badzz]", "https://foo_bar.com", "https://ex%61mple.com", "https://example.com\n/secret"] {
            XCTAssertNil(InputContextProfile.normalizedIdentifier(site, kind: .website), site)
        }
        XCTAssertEqual(InputContextProfile.normalizedIdentifier("https://[::1]:8000/path", kind: .website), "[::1]")
        XCTAssertEqual(InputContextProfile.normalizedIdentifier("Example.COM.", kind: .website), "example.com")
    }
    func testOnlyMatchingEnabledApplicationAndSiteContribute() {
        let app = InputContextProfile(kind: .application, identifier: "com.example.editor", topic: "アプリの話題", terms: ["Codex", "共通"])
        let site = InputContextProfile(kind: .website, identifier: "example.com", topic: "サイトの話題", terms: ["Antigravity", "共通"])
        let other = InputContextProfile(kind: .application, identifier: "com.example.other", topic: "混ぜない", terms: ["別の用語"])
        let disabled = InputContextProfile(kind: .website, identifier: "other.com", topic: "無効", terms: ["無効な用語"], enabled: false)
        let result = InputContextSupplement.resolve([other, app, disabled, site], bundleID: "com.example.editor", siteHost: "example.com")
        XCTAssertEqual(result.topic, "アプリの話題\nサイトの話題")
        XCTAssertEqual(result.terms, ["Antigravity", "共通", "Codex"])
        XCTAssertEqual(InputContextSupplement.resolve([app, site], bundleID: "com.example.editor", siteHost: "example.com", excluded: true), InputContextSupplement())
        XCTAssertEqual(InputContextSupplement.resolve([app, site], bundleID: "", siteHost: "example.com"), InputContextSupplement())
        XCTAssertTrue(InputContextSupplement.resolve([disabled], bundleID: "com.example.editor", siteHost: "other.com").terms.isEmpty)
    }
    func testBoundsAndPriorityDoNotGrowRecognitionBudget() {
        let app = InputContextProfile(kind: .application, identifier: "com.example.editor", topic: String(repeating: "あ", count: 600), terms: (0..<40).map { "アプリ用語\($0)" })
        let site = InputContextProfile(kind: .website, identifier: "example.com", topic: String(repeating: "い", count: 600), terms: (0..<40).map { "サイト用語\($0)" })
        XCTAssertEqual(app.topic.count, 400); XCTAssertEqual(app.terms.count, 32)
        let supplement = InputContextSupplement.resolve([app, site], bundleID: app.identifier, siteHost: site.identifier)
        XCTAssertEqual(supplement.topic.count, 800)
        let hints = supplement.recognitionHints(fallback: ["辞書用語"])
        XCTAssertEqual(hints.count, 64); XCTAssertEqual(hints[0], "サイト用語0"); XCTAssertEqual(hints[32], "アプリ用語0")
        XCTAssertFalse(hints.contains("辞書用語"))
        XCTAssertEqual(InputContextProfile.boundedTerms([" Codex ", "Codex", "", "a\nb", "a=b", "a,b", String(repeating: "あ", count: 81)]), ["Codex"])
    }
    func testSupplementRanksDictionaryButDoesNotBecomeReplacementRules() {
        let rule = WordRule(heard: "こーでっくす", written: "Codex")
        let context = WritingContext(supplement: "Codexを使う作業です。")
        let filler = (0..<70).map { WordRule(heard: "よみ\($0)", written: "用語\($0)") }
        XCTAssertEqual(ContextVocabulary.recognitionHints(rules: filler + [rule], context: context).first, "Codex")
        let supplement = InputContextSupplement(topic: "Antigravity", terms: ["Antigravity"])
        XCTAssertEqual(supplement.recognitionHints(fallback: ["Codex"]), ["Antigravity", "Codex"])
        // Profiles are not appended to the formatter's rules.
        XCTAssertEqual(TextFormatter.Prepared(rules: [rule]).format("こーでっくすとアンチグラビティ"), "Codexとアンチグラビティ")
    }
    func testProfilesRoundTripAndOldContextRemainsDecodable() throws {
        let profile = InputContextProfile(kind: .application, identifier: "com.example.editor", topic: "話題", terms: ["Codex"])
        XCTAssertEqual(try JSONDecoder().decode(InputContextProfile.self, from: JSONEncoder().encode(profile)), profile)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(WritingContext(bundleID: "app"))) as? [String: Any])
        object.removeValue(forKey: "supplement")
        let old = try JSONDecoder().decode(WritingContext.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(old.supplement)
        let context = WritingContext(bundleID: "app", supplement: "利用者が指定した補足")
        XCTAssertEqual(context.limited(to: .appOnly).supplement, context.supplement)
    }
}
