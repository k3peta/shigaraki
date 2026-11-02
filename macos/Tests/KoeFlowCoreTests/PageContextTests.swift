import XCTest
@testable import KoeFlowCore

final class PageContextTests: XCTestCase {
    func testPageReferenceSurvivesAppSwitchWithoutExtendingItsLifetime() {
        var pages = PageContext()
        pages.observe(title: "桜美林大学 - Chrome", now: 10)
        XCTAssertTrue(pages.reference(now: 100).contains("桜美林大学"))
        XCTAssertTrue(pages.reference(now: 190).contains("桜美林大学"))
        XCTAssertEqual(pages.reference(now: 191), "")
    }
    func testBoundedDeduplicationAndReset() {
        var pages = PageContext()
        for i in 0..<10 { pages.observe(title: "ページ\(i)" + String(repeating: "長", count: 200), now: Double(i)) }
        let reference = pages.reference(now: 10)
        XCTAssertLessThanOrEqual(reference.count, 240)
        XCTAssertTrue(reference.hasPrefix("ページ9"))
        XCTAssertFalse(reference.contains("ページ6"))
        pages.clear(); XCTAssertEqual(pages.reference(now: 11), "")
        pages.observe(title: "前のページ", now: 20)
        XCTAssertEqual(pages.reference(now: 19), "")
    }
    func testKnownPrivateAndPasswordTitlesClearAllPages() {
        for title in ["Incognito", "InPrivate", "シークレット", "プライベートブラウズ", "Password"] {
            var pages = PageContext()
            pages.observe(title: "通常ページ", now: 1)
            pages.observe(title: title, now: 2)
            XCTAssertEqual(pages.reference(now: 3), "")
        }
    }
    func testTitleCanPrioritizeRegisteredNameButCannotOverrideFacts() {
        let rules = [WordRule(heard: "おうびりんだいがく", written: "桜美林大学")]
        let context = WritingContext(recentPages: "桜美林大学の講義案内")
        XCTAssertEqual(ContextVocabulary.glossary(rules: rules, original: "桜尾林大学です。", context: context).map(\.written), ["桜美林大学"])
        XCTAssertTrue(ContextVocabulary.glossary(rules: rules, original: "本文です。", context: context.limited(to: .appOnly)).isEmpty)
        XCTAssertEqual(context.limited(to: .window).recentPages, context.recentPages)
        XCTAssertEqual(WritingValidation.rejection(original: "15日には行きません。", result: "16日に行きます。"), "facts")
    }
}
