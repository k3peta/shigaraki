import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.proofreading import SentenceProofreader, safe_revision, sentences


class ProofreadingTests(unittest.TestCase):
    def test_long_recording_does_not_keep_using_expired_external_reference(self):
        worker = SentenceProofreader(MagicMock(), lambda: None, context="桜美林大学 - Chrome")
        try:
            self.assertIn("桜美林大学", worker._reference_at(0))
            worker._context_deadline = time.monotonic() - 1
            self.assertEqual(worker._reference_at(0), "")
        finally:
            worker.freeze()

    def test_dictionary_readings_reach_inference_but_reference_cannot_change_valid_name(self):
        from shigaraki_win.proofread_runtime import ProofreadRuntime
        runtime = ProofreadRuntime()
        runtime._process = MagicMock(); runtime._process.poll.return_value = None
        response = MagicMock(); response.__enter__.return_value = [b'data: [DONE]\n']
        vocabulary = [("おうびりんだいがく", "桜美林大学"), ("めいじだいがく", "明治大学")]
        with patch.object(runtime, "_request", return_value=response) as request:
            runtime.correct("桜尾林大学で授業です。", context="桜美林大学の講義。",
                            vocabulary=vocabulary, protected=("桜美林大学", "明治大学"))
        raw_content = request.call_args.args[1]["messages"][1]["content"]
        content, end = json.JSONDecoder().raw_decode(raw_content)
        self.assertIn("修正後の本文だけ", raw_content[end:])
        self.assertEqual(content["登録語"],
                         [{"表記": "桜美林大学", "読み": ["おうびりんだいがく"]}])
        self.assertNotIn("明治大学", request.call_args.args[1]["messages"][1]["content"])
        source = "明治大学には15日に行きません。"
        self.assertEqual(safe_revision(source, "桜美林大学には15日に行きません。",
                                      ("桜美林大学", "明治大学")), source)

    def test_field_reference_reaches_worker_and_current_utterance_takes_priority(self):
        done = threading.Event()
        engine = MagicMock(); engine.correct.side_effect = lambda source, **_: source
        worker = SentenceProofreader(engine, done.set, context="早稲田大学での授業です。", debounce=0)
        try:
            worker.update("汗田大学で講義です。", final=True)
            self.assertTrue(done.wait(1))
            self.assertIn("早稲田大学", engine.correct.call_args.kwargs["context"])
            reference = worker._key("新"*240+"対象文。", 240, "対象文。")[2]
            self.assertEqual(reference, "新"*240)
        finally:
            worker.freeze()

    def test_completed_utterance_without_a_period_is_proofread(self):
        notified = threading.Event()
        engine = MagicMock(); engine.correct.return_value = "明日は晴れです。"
        worker = SentenceProofreader(engine, notified.set, debounce=0)
        try:
            worker.update("明日は晴れです", final=True)
            self.assertTrue(notified.wait(1))
            self.assertEqual(worker.snapshot(), ("明日は晴れです", "明日は晴れです。"))
            worker.update("明日は晴れです次の文")
            self.assertEqual(worker.snapshot()[1], "明日は晴れです。次の文")
            worker.update("まだ確定していない別の文")
            self.assertEqual(worker.snapshot()[1], "まだ確定していない別の文")
        finally:
            worker.freeze()

    def test_punctuation_does_not_allow_changed_numbers_or_negation(self):
        self.assertEqual(safe_revision("明日は参加しません", "明日は参加しません。", allow_punctuation=True), "明日は参加しません。")
        self.assertEqual(safe_revision("明日は参加しません", "明日は参加します。", allow_punctuation=True), "明日は参加しません")
        self.assertEqual(safe_revision("100円です", "1000円です。", allow_punctuation=True), "100円です")
        self.assertEqual(safe_revision("東京、京都です", "東京京都です。", allow_punctuation=True), "東京、京都です")

    def test_triggers_are_sentence_stops_and_newlines_only(self):
        self.assertEqual(list(sentences("一文。二行\n未完成")), [(0, "一文。"), (3, "二行\n")])
        self.assertEqual(list(sentences("unfinished? still speaking")), [])

    def test_obvious_homophone_fix_is_allowed_but_original_can_be_recovered(self):
        self.assertEqual(safe_revision("気象長の発表です。", "気象庁の発表です。"), "気象庁の発表です。")

    def test_quantities_negation_dictionary_and_deleted_words_are_preserved(self):
        cases = [("午後三時に伺います。", "午後四時に伺います。", ()),
                 ("100円で購入します。", "1000円で購入します。", ()),
                 ("明日は参加しません。", "明日は参加します。", ()),
                 ("これはテストです。", "テストです。", ()),
                 ("信楽を使います。", "滋賀を使います。", ("信楽",)),
                 ("気象長です。", "校正後：気象庁です。", ())]
        for source, value, terms in cases:
            with self.subTest(source=source):
                self.assertEqual(safe_revision(source, value, terms), source)

    def test_newline_is_kept_without_inventing_a_new_sentence(self):
        self.assertEqual(safe_revision("気象長です\n", "気象庁です"), "気象庁です\n")
        self.assertEqual(safe_revision("気象長です。", "気象庁です。追加です。"), "気象長です。")

    def test_caption_returns_immediately_while_correction_is_busy(self):
        entered, release, revised = threading.Event(), threading.Event(), threading.Event()
        engine = MagicMock()
        engine.correct.side_effect = lambda *_, **__: (entered.set(), release.wait(2), "気象庁です。")[2]
        worker = SentenceProofreader(engine, revised.set, debounce=0)
        try:
            self.assertEqual(worker.update("気象長です。"), "気象長です。")
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            self.assertEqual(worker.update("気象長です。まだ話している"), "気象長です。まだ話している")
            self.assertLess(time.monotonic()-before, .1)
            release.set()
            self.assertTrue(revised.wait(1))
            self.assertEqual(worker.snapshot(), ("気象長です。まだ話している", "気象庁です。まだ話している"))
            engine.correct.assert_called_once()
        finally:
            release.set(); worker.freeze()

    def test_stable_completed_sentence_is_processed_while_tail_keeps_updating(self):
        checked = threading.Event()
        engine = MagicMock()
        engine.correct.side_effect = lambda source, **_: (checked.set(), source)[1]
        worker = SentenceProofreader(engine, lambda: None, debounce=.05)
        try:
            for i in range(30):
                worker.update("一文目です。" + "続き"*i)
                if checked.wait(.01):
                    break
            self.assertTrue(checked.is_set())
            engine.correct.assert_called_once()
        finally:
            worker.freeze()

    def test_revised_asr_prefix_discards_old_correction(self):
        entered, release, notified = threading.Event(), threading.Event(), threading.Event()
        engine = MagicMock()
        engine.correct.side_effect = lambda *_, **__: (entered.set(), release.wait(2), "気象庁です。")[2]
        worker = SentenceProofreader(engine, notified.set, debounce=0)
        try:
            worker.update("気象長です。")
            self.assertTrue(entered.wait(1))
            worker.update("まだ確定していません")
            release.set(); time.sleep(.05)
            self.assertFalse(notified.is_set())
            self.assertEqual(worker.snapshot()[1], "まだ確定していません")
        finally:
            release.set(); worker.freeze()

    def test_freezing_prevents_late_rewrite_or_callback(self):
        entered, release, notified = threading.Event(), threading.Event(), threading.Event()
        engine = MagicMock()
        engine.correct.side_effect = lambda *_, **__: (entered.set(), release.wait(2), "気象庁です。")[2]
        worker = SentenceProofreader(engine, notified.set, debounce=0)
        worker.update("気象長です。")
        self.assertTrue(entered.wait(1))
        self.assertEqual(worker.freeze(), ("気象長です。", "気象長です。"))
        release.set(); worker._thread.join(1)
        self.assertFalse(notified.is_set())

    def test_final_caption_skips_debounce_and_can_complete_before_freezing(self):
        revised = threading.Event()
        engine = MagicMock(); engine.correct.return_value = "気象庁です。"
        # A fresh CI VM can have less uptime than the configured debounce.
        with patch("shigaraki_win.proofreading.time.monotonic", return_value=0.5):
            worker = SentenceProofreader(engine, revised.set, debounce=60)
            try:
                worker.update("気象長です。")
                self.assertTrue(worker.has_pending())
                worker.update("気象長です。", final=True)
                self.assertTrue(revised.wait(1))
                self.assertFalse(worker.has_pending())
                self.assertEqual(worker.freeze(), ("気象長です。", "気象庁です。"))
            finally:
                worker.freeze()
                worker._thread.join(1)

    def test_failure_is_attempted_once_and_retains_text(self):
        engine = MagicMock(); engine.correct.side_effect = TimeoutError()
        revised = threading.Event(); worker = SentenceProofreader(engine, revised.set, debounce=0)
        try:
            worker.update("気象長です。")
            self.assertTrue(revised.wait(1))
            for _ in range(5): worker.update("気象長です。")
            self.assertEqual(worker.snapshot()[1], "気象長です。")
            engine.correct.assert_called_once()
            self.assertEqual(worker.metrics["errors"], 1)
        finally:
            worker.freeze()

    def test_request_contains_only_bounded_text_and_no_tools(self):
        from shigaraki_win.proofread_runtime import ProofreadRuntime
        runtime = ProofreadRuntime()
        runtime._process = MagicMock(); runtime._process.poll.return_value = None
        response = MagicMock()
        response.__enter__.return_value = [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n', b'data: [DONE]\n']
        with patch.object(runtime, "_request", return_value=response) as request:
            self.assertEqual(runtime.correct("文章。", context="前の文章。"), "ok")
        body = request.call_args.args[1]
        self.assertNotIn("tools", body)
        self.assertEqual(body["max_tokens"], 384)
        self.assertFalse(body["cache_prompt"])

    def test_runtime_receives_the_full_bounded_reference(self):
        from shigaraki_win.proofread_runtime import ProofreadRuntime
        runtime = ProofreadRuntime()
        runtime._process = MagicMock(); runtime._process.poll.return_value = None
        response = MagicMock(); response.__enter__.return_value = [b'data: [DONE]\n']
        reference = "先頭" + "文"*236 + "末尾"
        with patch.object(runtime, "_request", return_value=response) as request:
            runtime.correct("文章。", context=reference)
        content, _ = json.JSONDecoder().raw_decode(request.call_args.args[1]["messages"][1]["content"])
        self.assertEqual(content["参考文脈"], reference)

    def test_corrected_previous_sentence_guides_the_next_without_reusing_stale_asr(self):
        done = threading.Event()
        engine = MagicMock()
        def correct(source, **kwargs):
            if source.startswith("桜尾林大学"):
                return source.replace("桜尾林大学", "桜美林大学")
            self.assertIn("桜美林大学", kwargs["context"])
            self.assertNotIn("桜尾林大学", kwargs["context"])
            return source
        engine.correct.side_effect = correct
        worker = SentenceProofreader(engine, done.set, debounce=0)
        try:
            worker.update("桜尾林大学で授業です。次の講義の準備です。", final=True)
            deadline = time.monotonic() + 2
            while engine.correct.call_count < 2 and time.monotonic() < deadline:
                done.wait(.02); done.clear()
            self.assertEqual(engine.correct.call_count, 2)
            self.assertEqual(worker.metrics["errors"], 0)
            worker.update("新しい話題です。", final=True)
            self.assertNotIn("桜美林大学", worker._reference_at(0))
        finally:
            worker.freeze()

    def test_quotes_addresses_and_protocol_output_cannot_be_rewritten(self):
        for source, candidate in [
            ('「汗田大学」という表記を残します。', '「早稲田大学」という表記を残します。'),
            ('https://example.com/path を見ます。', 'https://example.com/other を見ます。'),
            ('user@example.com に送ります。', 'user@example.net に送ります。'),
            ('これは本文のテストです。', '{これは本文のテストです。}'),
        ]:
            with self.subTest(source=source):
                self.assertEqual(safe_revision(source, candidate), source)

    def test_context_cannot_introduce_an_unrelated_registered_place(self):
        source = "今日は京都市に行ってから打ち合わせをします。"
        candidate = "今日は信楽に行ってから打ち合わせをします。"
        self.assertEqual(safe_revision(source, candidate, ("信楽",)), source)
        for source, candidate, word in (
            ("桜尾林大学で授業をします。", "桜美林大学で授業をします。", "桜美林大学"),
            ("汗田大学で授業をします。", "早稲田大学で授業をします。", "早稲田大学"),
            ("相子大学で講義をします。", "相模女子大学で講義をします。", "相模女子大学"),
        ):
            self.assertEqual(safe_revision(source, candidate, (word,)), candidate)
        self.assertEqual(safe_revision("早稲太大学に資料を送ります。", "早稲田大学へ資料を送ります。", ("早稲田大学",)),
                         "早稲田大学に資料を送ります。")


if __name__ == "__main__":
    unittest.main()
