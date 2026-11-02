"""Lazy history viewer. File reads run on one worker, never the UI thread."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from .history import HistoryChanged, HistoryReader, MAX_VISIBLE_RECORDS

HISTORY_COPY = json.loads(Path(__file__).with_name("history-copy.json").read_text(encoding="utf-8"))


class _ReaderWorker(QObject):
    page_ready = Signal(int, object, str)
    detail_ready = Signal(int, object, object, str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.reader = None

    @Slot(int, bool)
    def page(self, generation, reset):
        try:
            if reset or self.reader is None:
                self.reader = HistoryReader(self.path)
            result = self.reader.page()
            self.page_ready.emit(generation, result, "")
        except (OSError, ValueError, RecursionError) as error:
            self.page_ready.emit(generation, None, "changed" if isinstance(error, HistoryChanged) else "failed")

    @Slot(int, object)
    def detail(self, generation, entry):
        try:
            result = self.reader.detail(entry)
            self.detail_ready.emit(generation, entry, result, "")
        except (OSError, ValueError, RecursionError) as error:
            self.detail_ready.emit(generation, entry, None, "changed" if isinstance(error, HistoryChanged) else "failed")


class HistoryTab(QWidget):
    open_folder = Signal()
    _page_request = Signal(int, bool)
    _detail_request = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.path = None
        self.thread = None
        self.worker = None
        self._generation = 0
        self._started = False
        self._closing = False
        self._page_busy = self._detail_busy = self._paused = False
        self._has_more = True
        self._total = self._skipped = 0
        self._wanted = self._displayed = None
        self._texts = None
        layout = QVBoxLayout(self)
        hint = QLabel(HISTORY_COPY["hint"])
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.refresh = QPushButton(HISTORY_COPY["refresh"])
        self.refresh.clicked.connect(self.reload)
        self.more = QPushButton(HISTORY_COPY["more"])
        self.more.clicked.connect(self.load_more)
        self.folder = QPushButton(HISTORY_COPY["folder"])
        self.folder.clicked.connect(self.open_folder.emit)
        for button in (self.refresh, self.more, self.folder):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.records = QListWidget()
        self.records.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.records.setAccessibleName(HISTORY_COPY["history"])
        self.records.setMinimumHeight(100)
        self.records.setUniformItemSizes(True)
        self.records.currentItemChanged.connect(self._selected)
        self.records.verticalScrollBar().valueChanged.connect(self._scrolled)
        layout.addWidget(self.records, 1)
        self.details = QTabWidget()
        self.text = QPlainTextEdit()
        self.raw = QPlainTextEdit()
        for editor, key in ((self.text, "text"), (self.raw, "raw")):
            editor.setReadOnly(True)
            editor.setAccessibleName(HISTORY_COPY[key])
            editor.setPlaceholderText(HISTORY_COPY["select"])
            self.details.addTab(editor, HISTORY_COPY[key])
        self.details.setMinimumHeight(145)
        for panel in (self.records, self.details):
            policy = panel.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
            panel.setSizePolicy(policy)
        layout.addWidget(self.details, 1)
        copies = QHBoxLayout()
        self.copy_text = QPushButton(HISTORY_COPY["copy_text"])
        self.copy_raw = QPushButton(HISTORY_COPY["copy_raw"])
        self.copy_text.clicked.connect(lambda: self._copy(0))
        self.copy_raw.clicked.connect(lambda: self._copy(1))
        copies.addWidget(self.copy_text)
        copies.addWidget(self.copy_raw)
        layout.addLayout(copies)
        self.info = QLabel(HISTORY_COPY["select"])
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self._buttons()
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def set_path(self, path):
        self.path = Path(path)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._started:
            self.reload()

    def _ensure_worker(self):
        if self.thread is not None:
            return
        self.thread = QThread(self)
        self.worker = _ReaderWorker(self.path)
        self.worker.moveToThread(self.thread)
        self._page_request.connect(self.worker.page, Qt.ConnectionType.QueuedConnection)
        self._detail_request.connect(self.worker.detail, Qt.ConnectionType.QueuedConnection)
        self.worker.page_ready.connect(self._page_ready, Qt.ConnectionType.QueuedConnection)
        self.worker.detail_ready.connect(self._detail_ready, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def _buttons(self):
        available = not self._paused and not self._closing
        for viewer in (self.records, self.text, self.raw):
            viewer.setEnabled(available)
        self.refresh.setEnabled(available and not self._page_busy and not self._detail_busy)
        self.more.setEnabled(available and self._has_more and not self._page_busy)
        self.copy_text.setEnabled(available and self._texts is not None)
        self.copy_raw.setEnabled(available and self._texts is not None)

    def set_paused(self, paused):
        if self._paused == paused:
            return
        self._paused = paused
        self._buttons()
        if paused and self.isVisible():
            self.info.setText(HISTORY_COPY["paused"])
        elif not paused and self.isVisible():
            if not self._started:
                self.reload()
            else:
                self._counts()
                self._load_detail()

    def reload(self):
        if self._paused or self._page_busy or self._detail_busy or self._closing or self.path is None:
            return
        self._generation += 1
        self._started = True
        self.records.clear()
        self.text.clear()
        self.raw.clear()
        self._wanted = self._displayed = self._texts = None
        self._total = self._skipped = 0
        self._has_more = True
        self._load_page(True)

    def load_more(self):
        if self._paused or self._closing or self._page_busy or not self._has_more:
            return
        if not self._started:
            self.reload()
        else:
            self._load_page(False)

    def _load_page(self, reset):
        self._ensure_worker()
        self._page_busy = True
        self.info.setText(HISTORY_COPY["loading"])
        self._buttons()
        self._page_request.emit(self._generation, reset)

    def _scrolled(self, value):
        bar = self.records.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum():
            self.load_more()

    @Slot(int, object, str)
    def _page_ready(self, generation, page, error):
        if self._closing or generation != self._generation:
            return
        if error:
            self._has_more = False
            self._page_busy = False
            self.info.setText(HISTORY_COPY[error])
            self._buttons()
            return
        self._has_more = page.has_more
        self._skipped += page.skipped
        self._total += len(page.entries)
        old_scroll = self.records.verticalScrollBar().value()
        removed_height = 0
        self.records.blockSignals(True)
        for entry in page.entries:
            try:
                date = datetime.fromisoformat(entry.at).astimezone().strftime("%Y/%m/%d %H:%M:%S")
            except (ValueError, OverflowError, OSError):
                date = entry.at or HISTORY_COPY["unknown_time"]
            outcome = HISTORY_COPY.get("outcome_" + entry.outcome, entry.outcome or HISTORY_COPY["unknown_outcome"])
            item = QListWidgetItem(f"{date}　{entry.app or HISTORY_COPY['unknown_app']}\n{outcome}\n{entry.preview}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.records.addItem(item)
        while self.records.count() > MAX_VISIBLE_RECORDS:
            removed_height += self.records.sizeHintForRow(0)
            self.records.takeItem(0)
        self.records.blockSignals(False)
        self.records.verticalScrollBar().setValue(max(0, old_scroll - removed_height))
        self._page_busy = False
        self._buttons()
        self._counts()
        if self.records.currentItem() is None and self.records.count():
            self.records.setCurrentRow(0)
        else:
            self._selected(self.records.currentItem(), None)

    def _counts(self):
        if self._paused:
            self.info.setText(HISTORY_COPY["paused"])
            return
        text = (HISTORY_COPY["counts"].format(total=self._total, limit=MAX_VISIBLE_RECORDS)
                if self._total else HISTORY_COPY["no_valid_page" if self._has_more else "empty"])
        if self._skipped:
            text += "\n" + HISTORY_COPY["skipped"].format(count=self._skipped)
        self.info.setText(text)

    def _selected(self, current, previous):
        self._wanted = current.data(Qt.ItemDataRole.UserRole) if current else None
        if self._wanted != self._displayed:
            self._displayed = None
            self._texts = None
            self.text.clear()
            self.raw.clear()
        self._buttons()
        self._load_detail()

    def _load_detail(self):
        if (self._wanted is None or self._wanted == self._displayed or self._page_busy or
                self._detail_busy or self._paused or self._closing):
            return
        self._detail_busy = True
        self._buttons()
        self._detail_request.emit(self._generation, self._wanted)

    @Slot(int, object, object, str)
    def _detail_ready(self, generation, entry, texts, error):
        if self._closing or generation != self._generation:
            return
        self._detail_busy = False
        if entry == self._wanted:
            if error:
                self.info.setText(HISTORY_COPY[error])
            else:
                self._displayed = entry
                self._texts = texts
                self.text.setPlainText(texts[0])
                self.raw.setPlainText(texts[1])
        self._buttons()
        if entry != self._wanted:
            self._load_detail()

    def _copy(self, index):
        if self._texts is not None and not self._paused and not self._closing:
            QApplication.clipboard().setText(self._texts[index])
            self.info.setText(HISTORY_COPY["copied"])

    def shutdown(self):
        self._closing = True
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
