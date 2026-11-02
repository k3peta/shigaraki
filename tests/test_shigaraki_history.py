import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from shigaraki_win.history import (
    BLOCK_BYTES, MAX_RECORD_BYTES, MAX_VISIBLE_RECORDS, PAGE_BYTES, PAGE_RECORDS,
    HistoryChanged, HistoryReader,
)


def line(number, **extra):
    record = {"at": "2026-11-02T12:00:00+09:00", "app": "chrome.exe",
              "outcome": "confirmed", "text": f"記録{number}。", "raw_text": f"原文{number}", **extra}
    return json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"


class HistoryReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "recognition.jsonl"

    def test_newest_first_and_exact_text_with_read_only_paging(self):
        original = b"".join(line(i) for i in range(137))
        self.path.write_bytes(original)
        reader = HistoryReader(self.path)
        texts = []
        while True:
            page = reader.page()
            self.assertLessEqual(len(page.entries), PAGE_RECORDS)
            self.assertLessEqual(page.bytes_read, PAGE_BYTES)
            texts.extend(reader.detail(e) for e in page.entries)
            if not page.has_more:
                break
        self.assertEqual(texts, [(f"記録{i}。", f"原文{i}") for i in reversed(range(137))])
        self.assertEqual(self.path.read_bytes(), original)

    def test_large_sparse_file_only_reads_a_bounded_tail(self):
        with self.path.open("wb") as f:
            f.write(line(0))
            f.seek(2 * 1024**3)
            f.write(b"\n" + b"".join(line(i) for i in range(1000, 1200)))
        reader = HistoryReader(self.path)
        page = reader.page()
        self.assertEqual(reader.detail(page.entries[0])[0], "記録1199。")
        self.assertEqual(len(page.entries), PAGE_RECORDS)
        self.assertLessEqual(page.bytes_read, PAGE_BYTES)
        self.assertLessEqual(len(reader.buffer), MAX_RECORD_BYTES + BLOCK_BYTES)
        self.assertTrue(page.has_more)
        self.assertGreater(reader.cursor, 2 * 1024**3 - PAGE_BYTES)

    def test_invalid_utf8_json_schema_partial_and_oversized_lines_are_skipped(self):
        self.path.write_bytes(line(0) + b"x" * (PAGE_BYTES * 2) + b"\n" +
                              b'[]\n{"text":13}\n{"text":"ok","raw_text":false}\n\xff\n' +
                              line(1) + b'{"text": "unfinished')
        reader = HistoryReader(self.path)
        found, skipped, pages = [], 0, 0
        while True:
            page = reader.page()
            found.extend(reader.detail(e)[0] for e in page.entries)
            skipped += page.skipped
            pages += 1
            self.assertLessEqual(page.bytes_read, PAGE_BYTES)
            self.assertLessEqual(len(reader.buffer), MAX_RECORD_BYTES + BLOCK_BYTES)
            if not page.has_more:
                break
            self.assertLess(pages, 12)
        self.assertEqual(found, ["記録1。", "記録0。"])
        self.assertEqual(skipped, 6)
        self.assertGreater(pages, 1)

    def test_utf8_record_crosses_blocks_and_eof_without_newline_is_accepted(self):
        value = "しがらき🙂" * 6000
        self.path.write_bytes(line(0) + line(1, text=value).rstrip(b"\n"))
        reader = HistoryReader(self.path)
        page = reader.page()
        self.assertEqual(reader.detail(page.entries[0])[0], value)
        self.assertEqual(reader.detail(page.entries[1]), ("記録0。", "原文0"))

    def test_appends_wait_for_refresh_and_legacy_raw_text_falls_back(self):
        self.path.write_bytes(b'{"text":"old"}\n')
        reader = HistoryReader(self.path)
        page = reader.page()
        with self.path.open("ab") as f:
            f.write(line(1))
        self.assertEqual(reader.detail(page.entries[0]), ("old", "old"))
        self.assertFalse(reader.page().has_more)
        newer = HistoryReader(self.path)
        self.assertEqual(newer.detail(newer.page().entries[0])[0], "記録1。")

    def test_replaced_truncated_and_same_size_rewritten_snapshot_rejects_stale_details(self):
        for mutation in ("replace", "truncate", "rewrite"):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(line(0))
                reader = HistoryReader(self.path)
                entry = reader.page().entries[0]
                if mutation == "replace":
                    newer = self.path.with_suffix(".new")
                    newer.write_bytes(line(1))
                    os.replace(newer, self.path)
                elif mutation == "truncate":
                    self.path.write_bytes(b"")
                else:
                    self.path.write_bytes(line(1))
                    os.utime(self.path, ns=(1, 1))
                with self.assertRaises(HistoryChanged):
                    reader.detail(entry)

    def test_rewritten_record_with_append_cannot_be_copied_as_the_old_entry(self):
        self.path.write_bytes(line(0))
        reader = HistoryReader(self.path)
        entry = reader.page().entries[0]
        self.path.write_bytes(line(1) + line(2))
        with self.assertRaises(HistoryChanged):
            reader.detail(entry)

    def test_absent_and_empty_logs_do_not_create_files(self):
        self.assertEqual(HistoryReader(self.path).page().entries, ())
        self.assertFalse(self.path.exists())
        self.path.write_bytes(b"")
        self.assertFalse(HistoryReader(self.path).page().has_more)

    def test_escaped_unpaired_surrogates_are_skipped_before_ui_rendering(self):
        self.path.write_bytes(line(0) + b'{"text":"\\ud800"}\n' +
                              b'{"text":"valid","app":"\\udfff"}\n' + line(1))
        reader = HistoryReader(self.path)
        page = reader.page()
        self.assertEqual([reader.detail(e)[0] for e in page.entries], ["記録1。", "記録0。"])
        self.assertEqual(page.skipped, 2)


class HistoryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["history tests"])

    def setUp(self):
        from shigaraki_win.ui import MainWindow
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "recognition.jsonl"
        self.main = MainWindow()
        self.history = self.main.history
        self.history.set_path(self.path)
        self.addCleanup(self.main.deleteLater)
        self.addCleanup(self.history.shutdown)

    def wait_for(self, predicate):
        end = time.monotonic() + 5
        while not predicate() and time.monotonic() < end:
            self.qt.processEvents()
            time.sleep(.001)
        self.assertTrue(predicate())

    def open_history(self):
        self.main.tabs.setCurrentWidget(self.main.history_scroll)
        self.main.show()
        self.wait_for(lambda: self.history._started and not self.history._page_busy)

    def test_startup_is_lazy_and_io_runs_off_the_ui_thread(self):
        import threading
        calls = []
        actual = HistoryReader.page
        def track(reader):
            calls.append(threading.get_ident())
            return actual(reader)
        self.path.write_bytes(line(1))
        with patch.object(HistoryReader, "page", track):
            self.main.show()
            self.qt.processEvents()
            self.assertEqual(calls, [])
            self.assertIsNone(self.history.thread)
            self.open_history()
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.get_ident())
        self.wait_for(lambda: self.history._texts is not None)
        self.assertEqual(self.history.text.toPlainText(), "記録1。")
        self.assertFalse(self.main.copy_button.isVisible())

    def test_old_pages_evict_widgets_but_can_continue_past_the_display_limit(self):
        original = b"".join(line(i) for i in range(MAX_VISIBLE_RECORDS + PAGE_RECORDS * 2))
        self.path.write_bytes(original)
        self.open_history()
        while self.history._has_more:
            self.wait_for(lambda: not self.history._page_busy)
            self.history.load_more()
            self.wait_for(lambda: not self.history._page_busy)
            self.assertLessEqual(self.history.records.count(), MAX_VISIBLE_RECORDS)
        self.assertEqual(self.history._total, MAX_VISIBLE_RECORDS + PAGE_RECORDS * 2)
        self.history.records.setCurrentRow(self.history.records.count()-1)
        self.wait_for(lambda: self.history._texts == ("記録0。", "原文0"))
        self.history.copy_raw.click()
        self.assertEqual(self.qt.clipboard().text(), "原文0")
        self.assertEqual(self.path.read_bytes(), original)

    def test_recording_defers_loading_and_prevents_clipboard_changes(self):
        self.path.write_bytes(line(1))
        self.main.busy(True, recording=True)
        self.main.tabs.setCurrentWidget(self.main.history_scroll)
        self.main.show()
        self.qt.processEvents()
        self.assertFalse(self.history._started)
        self.assertIsNone(self.history.thread)
        self.main.busy(False)
        self.wait_for(lambda: self.history._texts is not None)
        self.qt.clipboard().setText("untouched")
        self.main.busy(True, recording=True)
        self.history._copy(0)
        self.assertEqual(self.qt.clipboard().text(), "untouched")
        self.assertFalse(self.history.more.isEnabled())
        self.assertFalse(self.history.refresh.isEnabled())

    def test_recording_also_blocks_the_editors_native_copy_shortcut(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.path.write_bytes(line(1))
        self.open_history()
        self.wait_for(lambda: self.history._texts is not None)
        self.history.text.setFocus()
        self.history.text.selectAll()
        self.qt.clipboard().setText("untouched")
        self.main.busy(True, recording=True)
        self.assertFalse(self.history.text.isEnabled())
        self.assertFalse(self.history.raw.isEnabled())
        QTest.keyClick(self.history.text, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.qt.processEvents()
        self.assertEqual(self.qt.clipboard().text(), "untouched")
        self.main.busy(False)
        self.assertTrue(self.history.text.isEnabled())

    def test_refresh_updates_appended_records_and_plain_text_is_not_html(self):
        self.path.write_bytes(line(0))
        self.open_history()
        self.wait_for(lambda: self.history._texts is not None)
        with self.path.open("ab") as f:
            f.write(line(1, text="<b>加工しない</b>"))
        self.history.reload()
        self.wait_for(lambda: self.history._texts == ("<b>加工しない</b>", "原文1"))
        self.assertEqual(self.history.text.toPlainText(), "<b>加工しない</b>")

    def test_scrolling_to_the_bottom_reads_the_next_page(self):
        self.path.write_bytes(b"".join(line(i) for i in range(100)))
        self.open_history()
        self.qt.processEvents()
        initial = self.history._total
        bar = self.history.records.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        self.wait_for(lambda: self.history._total > initial)

    def test_evicting_newer_rows_keeps_the_visible_old_record_in_place(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QAbstractItemView
        self.path.write_bytes(b"".join(line(i) for i in range(300)))
        self.open_history()
        while self.history._total < MAX_VISIBLE_RECORDS:
            self.history.load_more()
            self.wait_for(lambda: not self.history._page_busy)
        self.assertEqual(self.history.records.verticalScrollMode(),
                         QAbstractItemView.ScrollMode.ScrollPerPixel)
        bar = self.history.records.verticalScrollBar()
        old_total = self.history._total
        bar.setValue(bar.maximum())
        anchor = self.history.records.itemAt(2, 2).data(Qt.ItemDataRole.UserRole)
        self.wait_for(lambda: not self.history._page_busy and self.history._total > old_total)
        current = self.history.records.itemAt(2, 2).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(current, anchor)

    def test_copy_buttons_fit_default_size_and_remain_reachable_on_small_windows(self):
        self.path.write_bytes(line(0))
        self.open_history()
        self.main.resize(650, 720)
        self.qt.processEvents()
        scroll = self.main.history_scroll
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(scroll.viewport().rect().contains(
            self.history.copy_text.mapTo(scroll.viewport(), self.history.copy_text.rect().center())))
        self.main.resize(500, 540)
        self.qt.processEvents()
        scroll.ensureWidgetVisible(self.history.copy_text)
        self.qt.processEvents()
        self.assertTrue(scroll.viewport().rect().contains(
            self.history.copy_text.mapTo(scroll.viewport(), self.history.copy_text.rect().center())))
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

    def test_fast_selection_back_to_previous_record_does_not_show_stale_text(self):
        import threading
        self.path.write_bytes(line(0) + line(1))
        self.open_history()
        self.wait_for(lambda: self.history._texts == ("記録1。", "原文1"))
        entered, release = threading.Event(), threading.Event()
        actual = HistoryReader.detail
        def delayed(reader, entry):
            entered.set()
            release.wait(3)
            return actual(reader, entry)
        with patch.object(HistoryReader, "detail", delayed):
            try:
                self.history.records.setCurrentRow(1)
                self.wait_for(entered.is_set)
                self.history.records.setCurrentRow(0)
            finally:
                release.set()
            self.wait_for(lambda: self.history._texts == ("記録1。", "原文1"))
        self.assertEqual(self.history.text.toPlainText(), "記録1。")
