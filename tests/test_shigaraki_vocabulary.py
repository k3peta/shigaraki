import ctypes as C
import unittest
from unittest.mock import MagicMock

from shigaraki_win.vocabulary import effective_words, recognition_terms, correction_glossary, topic_terms, display_words
from shigaraki_win.session import format_text
from shigaraki_win.proofreading import safe_revision


# These names are user registrations in the fixture, never built-in vocabulary.
SCHOOL_WORDS = [
    ["おーびりん", "桜美林"],
    ["おーびりん大学", "桜美林大学"],
    ["おうびりん大学", "桜美林大学"],
    ["おうびりんだいがく", "桜美林大学"],
    ["おーびりん学園", "桜美林学園"],
    ["めいじだいがく", "明治大学"],
    ["わせだ大学", "早稲田大学"],
    ["さがみじょしだい", "相模女子大"],
    ["さがみじょしだいがく", "相模女子大学"],
]


class VocabularyTests(unittest.TestCase):
    def test_defaults_contain_only_common_ai_names_in_display_hints_and_replacement(self):
        from pathlib import Path
        from shigaraki_win.engine import CpuEngine
        from shigaraki_win.haas_worker import keywords_from_rules
        rules = effective_words([])
        expected = {"OpenAI", "ChatGPT", "Codex", "Antigravity", "Claude", "Gemini"}
        self.assertEqual({written for _, written in display_words([])}, expected)
        self.assertEqual(len(display_words([])), 6)
        self.assertEqual(set(recognition_terms(rules)), expected)
        self.assertEqual(CpuEngine(Path("unused-model.gguf")).rules, rules)
        keywords = keywords_from_rules(rules)
        self.assertEqual({line.split("=", 1)[0] for line in keywords.splitlines()}, expected)
        for reading, written in display_words([]):
            self.assertEqual(format_text(reading + "を使います。", rules), written + "を使います。")
        source = "オービリン大学とめいじだいがく、ワセダ大学とサガミジョシダイガク。"
        self.assertEqual(format_text(source, rules), source)
        self.assertEqual(topic_terms("桜美林大学と明治大学の話題。", rules), ())
        self.assertEqual(correction_glossary(rules, source, "桜美林大学の授業。"), [])
        self.assertNotIn("信楽", keywords)

    def test_default_ai_readings_and_mixed_spellings_are_corrected(self):
        rules = effective_words([])
        for raw, written in (("オープンAI", "OpenAI"), ("オープンエーアイ", "OpenAI"),
                             ("チャットGPT", "ChatGPT"), ("チャットジーピーティー", "ChatGPT"),
                             ("アンチグラビティ", "Antigravity"), ("アンチグラビティー", "Antigravity"),
                             ("ｱﾝﾁｸﾞﾗﾋﾞﾃｨ", "Antigravity")):
            with self.subTest(raw=raw):
                self.assertEqual(format_text(raw + "を開きます。", rules), written + "を開きます。")
        source = "AIとGPT、オープンな資料とチャットについて。"
        self.assertEqual(format_text(source, rules), source)

    def test_user_override_changes_default_ai_readings_without_changing_saved_words(self):
        custom = [["Antigravity", "アンチグラビティ"], ["こーでっくす", "コーデックス"]]
        rules = effective_words(custom)
        self.assertEqual(format_text("あんちぐらびてぃとコーデックス", rules), "アンチグラビティとコーデックス")
        self.assertNotIn("Antigravity", recognition_terms(rules))
        self.assertIn(("アンチグラビティ", "アンチグラビティ"), display_words(custom))
        self.assertEqual(custom, [["Antigravity", "アンチグラビティ"], ["こーでっくす", "コーデックス"]])

    def test_full_registered_name_is_not_diluted_by_its_shorter_fragments(self):
        rules = effective_words(SCHOOL_WORDS)
        glossary = correction_glossary(rules, "相子大学で講義です。", "相模女子大学の授業。")
        self.assertEqual([entry["written"] for entry in glossary], ["相模女子大学"])
        self.assertEqual(topic_terms("桜美林大学と相模女子大学です。", rules), ("相模女子大学", "桜美林大学"))
        self.assertEqual(topic_terms("相模女子大の話題。", rules), ("相模女子大",))
        self.assertEqual(topic_terms("未登録の大学。", rules), ())

    def test_context_rescues_relevant_word_outside_the_recognition_budget(self):
        custom = [["おうびりんだいがく", "桜美林大学"]] + [[f"読み{i}", f"用語{i}"] for i in range(80)]
        rules = effective_words(custom)
        self.assertNotIn("桜美林大学", recognition_terms(rules, custom))
        terms = recognition_terms(rules, custom, context="桜美林大学の授業について。")
        self.assertIn("桜美林大学", terms)
        self.assertEqual(len(terms), 64)
        # A fresh request has no retained topic, and explicit user spelling wins.
        self.assertNotIn("桜美林大学", recognition_terms(rules, custom))
        overridden = effective_words([["桜美林大学", "OBIRIN"]])
        self.assertNotIn("桜美林大学", recognition_terms(overridden, context="オービリン大学の話題。"))

    def test_glossary_uses_readings_and_current_topic_without_guessing_new_terms(self):
        rules = effective_words(SCHOOL_WORDS)
        source = "桜尾林大学で授業をします。"
        self.assertEqual(correction_glossary(rules, source), [])
        glossary = correction_glossary(rules, source, "桜美林大学の授業について。")
        entry = next(item for item in glossary if item["written"] == "桜美林大学")
        self.assertIn("オービリン大学", entry["readings"])
        self.assertNotIn("未登録大学", str(correction_glossary(rules, source, "未登録大学の話題。")))
        self.assertTrue(correction_glossary(rules, "オービリン大学です。"))

    def test_glossary_is_bounded_deduplicated_and_ascii_matches_whole_words(self):
        rules = [[f"読み{i}", f"用語{i}"] for i in range(100)]
        glossary = correction_glossary(rules * 2, " ".join(w for _, w in rules))
        self.assertLessEqual(len(glossary), 16)
        self.assertLessEqual(sum(len(g["written"]) + sum(map(len, g["readings"])) for g in glossary), 600)
        self.assertEqual(len({g["written"] for g in glossary}), len(glossary))
        self.assertEqual(correction_glossary([["えーあい", "AI"]], "emailです。"), [])
        self.assertTrue(correction_glossary([["えーあい", "AI"]], "aiを使います。"))
        self.assertEqual(recognition_terms([["読み", "悪\t語"]]), ())

    def test_registered_kana_readings_accept_hiragana_katakana_and_halfwidth(self):
        for reading in ("しがらき", "シガラキ", "ｼｶﾞﾗｷ"):
            with self.subTest(reading=reading):
                custom = [[reading, "信楽"]]
                rules = effective_words(custom)
                for raw in ("しがらき", "シガラキ", "ｼｶﾞﾗｷ"):
                    self.assertEqual(format_text(raw + "を使います。", rules), "信楽を使います。")
                self.assertEqual(custom, [[reading, "信楽"]])
                self.assertEqual(recognition_terms(rules).count("信楽"), 1)

    def test_explicit_spelling_rules_always_beat_generated_kana_aliases(self):
        entries = [["しがらき", "信楽"], ["シガラキ", "SHIGARAKI"], ["ｼｶﾞﾗｷ", "半角専用"]]
        for custom in (entries, list(reversed(entries))):
            with self.subTest(custom=custom):
                self.assertEqual(format_text("しがらき／シガラキ／ｼｶﾞﾗｷ", effective_words(custom)),
                                 "信楽／SHIGARAKI／半角専用")

    def test_kana_aliases_do_not_remove_spaces_or_guess_unregistered_kanji(self):
        rules = effective_words([["しがらき", "信楽"]])
        for raw in ("し がらき", "しが らき", "シガ ラキ", "ｼｶﾞ ﾗｷ", "滋賀楽", "滋賀らき", "柴楽"):
            with self.subTest(raw=raw):
                self.assertEqual(format_text(raw, rules), raw)
        unrelated = "しがらみと滋賀県、文章の構成を確認します。"
        self.assertEqual(format_text(unrelated, rules), unrelated)
        unregistered = "しがらき／シガラキ／ｼｶﾞﾗｷ"
        self.assertEqual(format_text(unregistered, effective_words([])), unregistered)

    def test_generated_kana_aliases_keep_longest_match_and_single_pass_replacement(self):
        rules = effective_words([["しがらき", "信楽"], ["しがらきあぷり", "公式アプリ"], ["信楽", "別名"]])
        self.assertEqual(format_text("シガラキアプリとｼｶﾞﾗｷを使います。", rules), "公式アプリと信楽を使います。")

    def test_saved_school_names_still_work_as_user_registrations(self):
        rules = effective_words(SCHOOL_WORDS)
        for raw, expected in (
            ("オービリン", "桜美林"),
            ("オービリン大学です。", "桜美林大学です。"),
            ("オービリン学園です。", "桜美林学園です。"),
            ("おうびりんだいがく", "桜美林大学"),
            ("めいじだいがく", "明治大学"),
            ("ワセダ大学", "早稲田大学"),
            ("さがみじょしだい", "相模女子大"),
            ("サガミジョシダイガク", "相模女子大学"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(format_text(raw, rules), expected)

    def test_correct_names_and_ordinary_homophones_are_not_replaced(self):
        rules = effective_words([])
        for text in ("桜美林学園、桜美林大学、明治大学、早稲田大学、相模女子大、相模女子大学。",
                     "明治時代の文章の構成と、女子の部活動について考えます。",
                     "相模原と早稲田の地名を調べます。"):
            self.assertEqual(format_text(text, rules), text)

    def test_user_dictionary_takes_precedence_without_mutating_it(self):
        custom = [["オービリン", "OBIRIN"], ["めいじだいがく", "MEIJI"], ["明治大学", "MEIJI"]]
        rules = effective_words(custom)
        self.assertEqual(custom, [["オービリン", "OBIRIN"], ["めいじだいがく", "MEIJI"], ["明治大学", "MEIJI"]])
        self.assertEqual(format_text("オービリンとめいじだいがく", rules), "OBIRINとMEIJI")
        self.assertEqual(format_text("明治大学", rules), "MEIJI")

    def test_recognition_bias_is_bounded_and_prioritizes_user_entries(self):
        custom = [[f"読み{i}", f"用語{i}"] for i in range(70)]
        terms = recognition_terms(effective_words(custom))
        self.assertEqual(len(terms), 64)
        self.assertEqual(terms[-1], "用語69")
        self.assertNotIn("用語0", terms)
        self.assertEqual(recognition_terms([(None, ""), (None, "a\nb"), (None, "x"*81)]), ())

    def test_asr_adds_only_two_fullwidth_kana_readings_for_registered_user_words(self):
        for reading in ("しがらき", "シガラキ", "ｼｶﾞﾗｷ"):
            with self.subTest(reading=reading):
                custom = [[reading, "信楽"]]
                rules = effective_words(custom)
                canonical = recognition_terms(rules)
                terms = recognition_terms(rules, user_words=custom)
                self.assertIn("信楽", terms)
                self.assertEqual(set(terms) - set(canonical), {"しがらき", "シガラキ"})
                self.assertNotIn("ｼｶﾞﾗｷ", terms)

    def test_asr_written_terms_keep_their_slots_before_reading_hints(self):
        for other_count, expected_extras in ((56, 1), (57, 0), (70, 0)):
            with self.subTest(other_count=other_count):
                custom = [["しがらき", "信楽"]] + [[f"読み{i}", f"用語{i}"] for i in range(other_count)]
                rules = effective_words(custom)
                canonical = recognition_terms(rules)
                terms = recognition_terms(rules, user_words=custom)
                self.assertLessEqual(len(terms), 64)
                self.assertEqual(tuple(term for term in terms if term in canonical), canonical)
                self.assertEqual(len(set(terms) - set(canonical)), expected_extras)

    def test_asr_never_adds_control_characters_numbers_or_non_kana_readings(self):
        for reading in ("shigaraki", "しがらき1", "シガラキ123", "しがらき\n", "しがらき\r",
                        "しがらき\0", "しが らき", "信楽", "しがらき大学", "ｼｶﾞﾗｷ1", "し" * 81):
            with self.subTest(reading=reading):
                custom = [[reading, "信楽"]]
                rules = effective_words(custom)
                self.assertEqual(recognition_terms(rules, user_words=custom), recognition_terms(rules))

    def test_asr_reading_hints_are_deduplicated_and_omitted_for_legacy_calls(self):
        custom = [["しがらき", "信楽"], ["シガラキ", "信楽"], ["ｼｶﾞﾗｷ", "信楽"]]
        rules = effective_words(custom)
        canonical = recognition_terms(rules)
        self.assertEqual(canonical, recognition_terms(rules, user_words=()))
        self.assertNotIn("しがらき", canonical)
        self.assertNotIn("シガラキ", canonical)
        terms = recognition_terms(rules, user_words=custom)
        for term in ("信楽", "しがらき", "シガラキ"):
            self.assertEqual(terms.count(term), 1)
        self.assertEqual(len(terms), len(canonical) + 2)

    def test_a_missed_registered_name_can_be_introduced_but_existing_names_stay(self):
        terms = recognition_terms(effective_words(SCHOOL_WORDS))
        source, corrected = "桜尾林大学で授業です。", "桜美林大学で授業です。"
        self.assertEqual(safe_revision(source, corrected, terms), corrected)
        for old, new in (("桜美林学園です。", "桜美林大学です。"),
                         ("相模女子大です。", "相模女子大学です。"),
                         ("明治大学です。", "早稲田大学です。"),
                         ("桜美林大学です。", "桜美林です。")):
            self.assertEqual(safe_revision(old, new, terms), old)

    def test_biasing_uses_utf8_and_preserves_c_abi_lifetimes(self):
        from shigaraki_win.nemotron_native import Stream, Options, SpeechContext
        native = MagicMock()
        native.recognition_options_default.return_value = Options()
        def opened(recognizer, options, out):
            opt = C.cast(options, C.POINTER(Options)).contents
            self.assertEqual(opt.speech_context_count, 1)
            context = C.cast(opt.speech_contexts, C.POINTER(SpeechContext)).contents
            self.assertEqual(context.size, C.sizeof(SpeechContext))
            self.assertEqual(context.phrase_count, 2)
            self.assertEqual(context.phrases[0].decode(), "桜美林大学")
            self.assertEqual(context.phrases[1].decode(), "早稲田大学")
            self.assertEqual(context.boost, 1.0)
            C.cast(out, C.POINTER(C.c_void_p))[0] = 7
            return 0
        native.streaming_recognize.side_effect = opened
        stream = Stream(native, 1, phrases=("桜美林大学", "早稲田大学"))
        self.assertEqual(stream._phrase_bytes[0].decode(), "桜美林大学")
        stream.close()
        native.stream_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
