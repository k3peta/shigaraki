import unittest

from shigaraki_win.context_profiles import (
    load_profiles, normalize_app, normalize_host, prioritize_hints,
    recognition_vocabulary, resolve_profiles, validate_profile,
)
from shigaraki_win.haas_worker import keywords_from_rules
from shigaraki_win.session import format_text


def profile(kind="application", identifier="chrome.exe", **values):
    return {"kind": kind, "identifier": identifier, "topic": "大学の講義について", "terms": ["桜美林大学"], **values}


class ContextProfileTests(unittest.TestCase):
    def test_empty_settings_add_nothing(self):
        self.assertEqual(resolve_profiles([], "chrome.exe", "chatgpt.com"), ("", ()))
        self.assertEqual(load_profiles(None), [])

    def test_domains_normalize_to_host_without_paths_or_queries(self):
        self.assertEqual(normalize_host("https://CHATGPT.com.:443/c/secret?q=private#fragment"), "chatgpt.com")
        self.assertEqual(normalize_host("chatgpt.com"), "chatgpt.com")
        self.assertEqual(normalize_host("https://日本語.jp/"), "xn--wgv71a119e.jp")
        for value in ("file:///tmp/private", "https://name:password@chatgpt.com", "https://chatgpt.com:99999", "*.chatgpt.com", "https://chatgpt.com\\evil", "chat gpt.com", "https://chatgpt.\ncom"):
            with self.subTest(value=value):
                self.assertEqual(normalize_host(value), "")

    def test_site_is_exact_not_suffix_title_or_previous_page(self):
        values = [profile("website", "chatgpt.com")]
        for host in ("", "chatgpt.com.evil.invalid", "other.chatgpt.com", "unrelated.invalid"):
            self.assertEqual(resolve_profiles(values, "chrome.exe", host), ("", ()))
        self.assertEqual(resolve_profiles(values, "chrome.exe", "chatgpt.com")[1], ("桜美林大学",))

    def test_application_name_uses_executable_basename_and_case(self):
        self.assertEqual(normalize_app(r"C:\Program Files\Chrome.EXE"), "chrome.exe")
        self.assertEqual(resolve_profiles([profile()], "Chrome.EXE")[1], ("桜美林大学",))
        self.assertEqual(resolve_profiles([profile()], "antigravity.exe"), ("", ()))

    def test_site_terms_precede_app_and_notes_are_bounded(self):
        values = [profile(topic="A"*400, terms=["共通用語", "大学"]),
                  profile("website", "chatgpt.com", topic="B"*400, terms=["信楽", "共通用語"])]
        note, terms = resolve_profiles(values, "chrome.exe", "chatgpt.com")
        self.assertLessEqual(len(note), 800)
        self.assertTrue(note.startswith("A"*400))
        self.assertEqual(terms, ("信楽", "共通用語", "大学"))

    def test_disabled_and_protected_contexts_cannot_apply(self):
        self.assertEqual(resolve_profiles([profile(enabled=False)], "chrome.exe"), ("", ()))
        self.assertEqual(resolve_profiles([profile()], "chrome.exe", blocked=True), ("", ()))

    def test_invalid_or_large_profile_is_not_partially_applied(self):
        for values in ({"topic": "x"*401}, {"terms": ["a"]*33}, {"terms": ["x"*81]},
                       {"terms": ["word=alias"]}, {"terms": ["secret\nline"]}, {"enabled": "false"}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_profile(profile(**values))
                self.assertEqual(load_profiles([profile(**values)]), [])

    def test_profile_and_hint_counts_stay_bounded(self):
        values = [profile(identifier=f"app{i}.exe") for i in range(100)]
        self.assertEqual(len(load_profiles(values)), 64)
        hints = prioritize_hints(tuple(f"old{i}" for i in range(64)), tuple(f"site{i}" for i in range(64)))
        self.assertEqual(len(hints), 64)
        self.assertEqual(hints[-1], "site0")

    def test_profile_terms_reach_actual_haas_keywords_without_new_replacements(self):
        original_rules = [("おーぷんえーあい", "OpenAI")]
        rules = recognition_vocabulary(original_rules, ("信楽", "OpenAI"))
        phrases = prioritize_hints(("OpenAI",), ("信楽", "OpenAI"))
        keywords = keywords_from_rules(rules, phrases)
        self.assertEqual(keywords.splitlines()[0], "信楽")
        self.assertIn("OpenAI=オープンエーアイ", keywords)
        self.assertEqual(original_rules, [("おーぷんえーあい", "OpenAI")])
        self.assertEqual(format_text("信楽におーぷんえーあいと入力", original_rules), "信楽にOpenAIと入力")
        self.assertEqual(format_text("シガラキと入力", original_rules), "シガラキと入力")


if __name__ == "__main__":
    unittest.main()
