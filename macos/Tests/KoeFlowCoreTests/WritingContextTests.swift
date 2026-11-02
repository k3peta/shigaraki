import XCTest
@testable import KoeFlowCore

final class WritingContextTests: XCTestCase {
    private func plan(_ context: WritingContext, profiles: [AppWritingProfile] = [], purpose: WritingPurpose = .automatic, scope: ContextScope = .window) -> WritingPlan {
        ContextRouter.resolve(context, profiles: profiles, defaultPurpose: purpose, automatic: true, scope: scope)
    }
    func testBrowserTabsSwitchWithoutSharingPreviousText() {
        let mail = plan(.init(bundleID: "com.apple.Safari", windowTitle: "受信トレイ - Gmail"))
        let chat = plan(.init(bundleID: "com.apple.Safari", windowTitle: "general - Slack"))
        XCTAssertEqual(mail.purpose, .email); XCTAssertEqual(chat.purpose, .chat)
        XCTAssertEqual(chat.context.before, "")
        XCTAssertEqual(plan(.init(bundleID: "com.apple.Safari", windowTitle: "GmailとSlackの比較記事")).purpose, .automatic)
        XCTAssertEqual(plan(.init(bundleID: "com.apple.Safari", windowTitle: "Gmail"), scope: .appOnly).purpose, .automatic)
    }
    func testAppOverrideAndVerbatimAreRespected() {
        let context = WritingContext(bundleID: "com.apple.mail", windowTitle: "Gmail", before: "private text")
        XCTAssertEqual(plan(context).purpose, .email)
        let override = AppWritingProfile(bundleID: context.bundleID, name: "Mail", purpose: .chat, mode: .manual, scope: .appOnly)
        let chosen = plan(context, profiles: [override], purpose: .document)
        XCTAssertEqual(chosen.purpose, .chat); XCTAssertEqual(chosen.mode, .manual)
        XCTAssertEqual(chosen.context.windowTitle, ""); XCTAssertEqual(chosen.context.before, "")
        XCTAssertEqual(plan(.init(bundleID: "com.microsoft.VSCode")).mode, .verbatim)
        XCTAssertEqual(plan(context, purpose: .plain).mode, .verbatim)
    }
    func testNearbyContextIsBoundedAndScopeIsEnforced() {
        let prefix = String(repeating: "前", count: 5000), suffix = String(repeating: "後", count: 5000)
        let context = WritingContext.surrounding(prefix + "選択" + suffix, range: NSRange(location: 5000, length: 2))!
        XCTAssertEqual(context.0.count, 300); XCTAssertEqual(context.1, "選択"); XCTAssertEqual(context.2.count, 200)
        XCTAssertNil(WritingContext.surrounding("abc", range: NSRange(location: 2, length: 100)))
        let full = WritingContext(bundleID: "test", windowTitle: "title", fieldHint: "hint", before: context.0, selected: context.1, after: context.2)
        XCTAssertEqual(full.limited(to: .window).before, "")
        XCTAssertEqual(full.limited(to: .appOnly).fieldHint, "")
        XCTAssertEqual(full.limited(to: .nearby).selected, "選択")
    }
    func testRewriteRejectsChangedNumbersDroppedNegationAndSpelling() {
        let original = "明日15時に信楽の資料を送ります。変更はしないでください。"
        XCTAssertNil(WritingValidation.rejection(original: original, result: "明日15時に信楽の資料を送ります。\n変更はしないでください。"))
        XCTAssertEqual(WritingValidation.rejection(original: original, result: original.replacingOccurrences(of: "15", with: "16")), "facts")
        XCTAssertEqual(WritingValidation.rejection(original: original, result: original.replacingOccurrences(of: "しない", with: "する")), "negation")
        XCTAssertEqual(WritingValidation.rejection(original: original, result: original.replacingOccurrences(of: "信楽", with: "しがらき"), rules: [.init(heard: "しがらき", written: "信楽")]), "dictionary")
        let link = "https://example.com/path を確認してください。"
        XCTAssertEqual(WritingValidation.rejection(original: link, result: link.replacingOccurrences(of: "/path", with: "/other")), "facts")
        XCTAssertEqual(WritingValidation.rejection(original: original, result: original + "」} ```json {"), "structure")
        XCTAssertEqual(WritingValidation.rejection(original: original, result: original.replacingOccurrences(of: "15時", with: "15日")), "facts")
    }
    func testProfilePersistenceRoundTrip() throws {
        let source = [AppWritingProfile(bundleID: "com.apple.Safari", name: "Safari", purpose: .automatic, mode: .automatic, scope: .nearby)]
        XCTAssertEqual(try JSONDecoder().decode([AppWritingProfile].self, from: JSONEncoder().encode(source)), source)
    }
    func testNumericSelfCorrectionDoesNotBecomeAlternatives() {
        let original = "明日15時 いや16時に集合です 金額は3000円ではなく3200円です"
        XCTAssertEqual(WritingValidation.rejection(original: original,
            result: "明日15時か16時に集合です。金額は3000円ではなく3200円です。"), "selfCorrection")
        XCTAssertNil(WritingValidation.rejection(original: original,
            result: "明日15時、いや16時に集合です。金額は3000円ではなく3200円です。"))
        XCTAssertNil(WritingValidation.rejection(original: "いや今日は予定がありません", result: "今日は予定がありません。"))
    }

    func testUserDictionarySpellingCannotBeChangedOrReducedByRewrite() {
        let rules = [WordRule(heard: "構成", written: "校正"), WordRule(heard: "捜瑳市", written: "匝瑳市")]
        let original = TextFormatter.format("構成をお願いします 捜瑳市の原稿も構成します", rules: rules)
        XCTAssertEqual(original, "校正をお願いします 匝瑳市の原稿も校正します")
        XCTAssertNil(WritingValidation.rejection(original: original,
            result: "校正をお願いします。匝瑳市の原稿も校正します。", rules: rules))
        XCTAssertEqual(WritingValidation.rejection(original: original,
            result: "構成をお願いします。匝瑳市の原稿も校正します。", rules: rules), "dictionary")
        XCTAssertEqual(WritingValidation.rejection(original: original,
            result: "校正をお願いします。捜瑳市の原稿も校正します。", rules: rules), "dictionary")
    }

}
