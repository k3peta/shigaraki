import unittest
from types import SimpleNamespace

from shigaraki_win.context import ShortContext


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


_DEFAULT = object()


def target(value="現在の入力欄です。", *, prefix=_DEFAULT, suffix="", **changes):
    data = dict(hwnd=101, pid=202, runtime_id=(42, 7), value=value,
                prefix=value if prefix is _DEFAULT else prefix, suffix=suffix)
    data.update(changes)
    return SimpleNamespace(**data)


class ShortContextTests(unittest.TestCase):
    def test_topic_survives_neutral_input_but_keeps_original_expiry(self):
        clock = Clock(); context = ShortContext(clock=clock)
        first = target("桜美林大学について。" + "説明。" * 100)
        context.remember(first, first.value, topics=("桜美林大学",))
        self.assertIn("桜美林大学", context.context_for(first))
        clock.now += 100
        second = target(first.value + "次の講義の準備をします。")
        context.remember(second, "次の講義の準備をします。")
        self.assertIn("桜美林大学", context.context_for(second))
        clock.now += 81
        self.assertNotIn("桜美林大学", context.context_for(second))

    def test_new_topic_replaces_old_and_other_field_cannot_recover_it(self):
        field = target("現在の本文。")
        self.context.remember(field, "桜美林大学の話題。", topics=("桜美林大学",))
        self.context.remember(field, "明治大学の話題。", topics=("明治大学",))
        value = self.context.context_for(field)
        self.assertIn("明治大学", value); self.assertNotIn("桜美林大学", value)
        self.context.context_for(target(runtime_id=(42, 8)))
        self.assertNotIn("明治大学", self.context.context_for(field))

    def setUp(self):
        self.clock = Clock()
        self.context = ShortContext(clock=self.clock, ttl=180)
        self.recent = "先ほどの話題は桜美林大学の授業準備です。"

    def remember(self, field=None):
        field = field or target()
        self.context.remember(field, self.recent)
        return field

    def assert_no_recent(self, field):
        result = self.context.context_for(field)
        self.assertNotIn(self.recent, result)
        self.assertLessEqual(len(result), 240)
        return result

    def test_current_field_does_not_need_previous_dictation(self):
        field = target("明治大学で授業をします。")
        self.assertEqual(self.context.context_for(field), field.value)
        self.assertEqual(self.context.source, "field")

    def test_current_prefix_and_suffix_surround_the_selection(self):
        field = target("前の文。選択した文章。後の文。",
                       prefix="前の文。", suffix="後の文。")
        result = self.context.context_for(field)
        self.assertIn(field.prefix, result)
        self.assertIn(field.suffix, result)
        self.assertNotIn("選択した文章", result)
        self.assertEqual(self.context.source, "field")

    def test_long_field_keeps_near_caret_text_and_uses_remaining_suffix(self):
        field = target("旧" * 1000 + "直前の話題。" + "直後の話題。" + "遠" * 1000,
                       prefix="旧" * 1000 + "直前の話題。",
                       suffix="直後の話題。" + "遠" * 1000)
        result = self.context.context_for(field)
        self.assertLessEqual(len(result), 240)
        self.assertIn("直前の話題。", result)
        self.assertNotIn("旧" * 241, result)
        # A short prefix leaves room for context immediately after the caret.
        short_prefix = target("前。" + "直後の話題。" + "遠" * 1000,
                              prefix="前。", suffix="直後の話題。" + "遠" * 1000)
        self.assertIn("直後の話題。", self.context.context_for(short_prefix))

    def test_verified_recent_text_is_available_only_for_the_same_snapshot(self):
        field = self.remember()
        result = self.context.context_for(field)
        self.assertIn(self.recent, result)
        self.assertIn(field.prefix, result)
        self.assertEqual(self.context.source, "field_and_recent")

    def test_recent_and_current_context_are_bounded_in_unicode_characters(self):
        field = target("前" * 1000 + "🦉大学の授業について。")
        recent = "桜美林大学。" * 100
        self.context.remember(field, recent)
        result = self.context.context_for(field)
        self.assertLessEqual(len(result), 240)
        self.assertIn(recent[-80:], result)
        self.assertNotIn(recent[-81:], result)
        self.assertIn("🦉大学の授業について。", result)
        self.assertEqual(self.context.source, "field_and_recent")

    def test_long_dictation_remembers_latest_topic_instead_of_its_opening(self):
        field = target("現在の入力欄。")
        recent = "昔の話題は明治大学でした。" + "古い説明。" * 100 + "今は桜美林大学の授業について。"
        self.context.remember(field, recent)
        result = self.context.context_for(field)
        self.assertIn("今は桜美林大学", result)
        self.assertNotIn("明治大学", result)
        self.clock.now += 181
        self.assertNotIn("桜美林大学", self.context.context_for(field))

    def test_large_selected_document_does_not_become_context(self):
        field = target("秘" * 200_000, prefix="", suffix="")
        self.assertEqual(self.context.context_for(field), "")
        self.assertEqual(self.context.source, "none")

    def test_runtime_window_or_process_change_drops_recent_context(self):
        for changes in ({"runtime_id": (42, 8)}, {"hwnd": 102}, {"pid": 203}):
            with self.subTest(changes=changes):
                original = self.remember()
                changed = target(**changes)
                self.assertEqual(self.assert_no_recent(changed), changed.value)
                self.assertEqual(self.context.source, "field")
                self.assert_no_recent(original)

    def test_manual_document_edit_invalidates_recent_even_at_same_caret(self):
        original = target("前。選択範囲。後。", prefix="前。", suffix="後。")
        self.remember(original)
        changed = target("前。別の本文。後。", prefix="前。", suffix="後。")
        self.assert_no_recent(changed)
        self.assert_no_recent(original)

    def test_deleting_text_and_reusing_the_field_does_not_restore_old_topic(self):
        original = self.remember()
        empty = target("")
        self.assertEqual(self.assert_no_recent(empty), "")
        self.assertEqual(self.context.source, "none")
        self.assert_no_recent(original)

    def test_caret_and_selection_changes_invalidate_recent(self):
        for prefix, suffix in (("現在", "の入力欄です。"), ("現在", "です。")):
            with self.subTest(prefix=prefix, suffix=suffix):
                original = self.remember()
                self.assert_no_recent(target(prefix=prefix, suffix=suffix))
                self.assert_no_recent(original)

    def test_none_target_clears_recent_instead_of_falling_back_to_last_app(self):
        original = self.remember()
        self.assertEqual(self.context.context_for(None), "")
        self.assertEqual(self.context.source, "none")
        self.assert_no_recent(original)

    def test_invalid_target_is_ignored_and_clears_recent(self):
        invalid = (
            object(), SimpleNamespace(hwnd=101),
            target(hwnd=0), target(pid=0), target(runtime_id=()),
            target(runtime_id=("wrong",)), target(value=None),
            target(prefix=None), target(suffix=None),
            target("本文", prefix="本文より長い前置き"),
            target("本文", prefix="違", suffix="文"),
            target("本文", prefix="本", suffix="違"),
        )
        for field in invalid:
            with self.subTest(field=field):
                original = self.remember()
                self.assertEqual(self.context.context_for(field), "")
                self.assertEqual(self.context.source, "none")
                self.assert_no_recent(original)

    def test_remember_with_no_target_discards_previous_history(self):
        original = self.remember()
        self.context.remember(None, "別の話題")
        self.assert_no_recent(original)

    def test_remember_keeps_snapshot_instead_of_mutable_target_reference(self):
        original = self.remember()
        original.value = "変更された本文。"
        original.prefix = original.value
        self.assert_no_recent(original)

    def test_ttl_is_measured_from_remember_not_refreshed_by_context_reads(self):
        field = self.remember()
        self.clock.now += 179
        self.assertIn(self.recent, self.context.context_for(field))
        self.clock.now += 1
        self.assertIn(self.recent, self.context.context_for(field))
        self.clock.now += 0.001
        self.assert_no_recent(field)
        self.assertEqual(self.context.source, "field")

    def test_expired_recent_is_not_revived_by_clock_reversal(self):
        field = self.remember()
        self.clock.now += 181
        self.assert_no_recent(field)
        self.clock.now = 100.0
        self.assert_no_recent(field)

    def test_clear_removes_recent_and_source(self):
        field = self.remember()
        self.context.context_for(field)
        self.context.clear()
        self.assertEqual(self.context.source, "none")
        self.assert_no_recent(field)
        self.assertEqual(self.context.source, "field")

    def test_newline_normalization_can_match_equivalent_field_snapshots(self):
        original = target("前の文。\r\n後の文。", prefix="前の文。\r\n", suffix="後の文。")
        self.remember(original)
        equivalent = target("前の文。\n後の文。", prefix="前の文。\n", suffix="後の文。")
        self.assertIn(self.recent, self.context.context_for(equivalent))
        self.assertEqual(self.context.source, "field_and_recent")

    def test_empty_or_whitespace_field_cannot_retain_a_previous_topic(self):
        for value in ("", " \t\n"):
            with self.subTest(value=value):
                original = self.remember()
                field = target(value)
                self.context.remember(field, self.recent)
                self.assertEqual(self.context.context_for(field), "")
                self.assertEqual(self.context.source, "none")
                self.assert_no_recent(original)

    def test_huge_field_uses_local_context_but_discards_recent_history(self):
        original = self.remember()
        field = target("古" * 100_001 + "カーソル直前の話題です。")
        self.context.remember(field, self.recent)
        result = self.context.context_for(field)
        self.assertLessEqual(len(result), 240)
        self.assertIn("カーソル直前の話題です。", result)
        self.assertNotIn(self.recent, result)
        self.assertEqual(self.context.source, "field")
        self.assert_no_recent(original)

    def test_remembering_another_verified_input_replaces_the_old_topic(self):
        field = self.remember()
        replacement = "今の話題は相模女子大学の授業です。"
        self.context.remember(field, replacement)
        result = self.context.context_for(field)
        self.assertIn(replacement, result)
        self.assertNotIn(self.recent, result)


if __name__ == "__main__":
    unittest.main()
