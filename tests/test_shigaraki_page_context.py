import unittest
from shigaraki_win.page_context import PageContext, combine_reference


class PageContextTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.pages = PageContext(clock=lambda: self.now)

    def test_cross_app_reference_expires_without_read_renewal(self):
        self.pages.observe("桜美林大学 - Google Chrome")
        self.now = 200
        self.assertIn("桜美林大学", self.pages.reference())
        self.now = 281
        self.assertEqual(self.pages.reference(), "")

    def test_maximum_three_titles_and_character_budget(self):
        for i in range(10):
            self.pages.observe(f"ページ{i}" + "長" * 200)
        self.assertEqual(len(self.pages.pages), 3)
        self.assertLessEqual(len(self.pages.reference()), 240)
        self.assertTrue(self.pages.reference().startswith("ページ9"))

    def test_private_title_and_off_clear_previous_references(self):
        for title in ("Incognito", "InPrivate", "シークレット", "プライベート", "Password"):
            self.pages.observe("通常のページ")
            self.pages.observe(title)
            self.assertEqual(self.pages.reference(), "")
        self.pages.observe("通常のページ")
        self.pages.clear()
        self.assertEqual(self.pages.reference(), "")

    def test_invalid_clock_does_not_reuse_future_page(self):
        self.pages.observe("以前のページ")
        self.now = 99
        self.assertEqual(self.pages.reference(), "")

    def test_current_field_takes_priority_and_budget_is_shared(self):
        value = combine_reference("本文" * 200, "ウィンドウ" * 40, "過去ページ" * 100)
        self.assertLessEqual(len(value), 240)
        self.assertTrue(value.endswith("本文" * 80))
        self.assertEqual(combine_reference("入力本文", "シークレット", "桜美林大学"), "")

    def test_registered_terms_reach_recognition_from_recent_pages(self):
        from shigaraki_win.vocabulary import recognition_terms
        self.pages.observe("桜美林大学の授業案内 - Chrome")
        reference = combine_reference("", "Codex", self.pages.reference())
        terms = recognition_terms([("おうびりん", "桜美林大学")], [], context=reference)
        self.assertIn("桜美林大学", terms)
