from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget,
    QToolButton, QVBoxLayout, QWidget, QDialog, QProgressBar, QScrollArea, QFrame, QApplication, QGroupBox,
)

COPY = json.loads(Path(__file__).with_name("copy.json").read_text(encoding="utf-8"))
COPY.update(json.loads(Path(__file__).with_name("setup-copy.json").read_text(encoding="utf-8")))


def wave_icon(recording=False):
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#ed4f5b" if recording else "#7953c8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 36, 36)
    p.setBrush(QColor("white"))
    if recording:
        p.drawRoundedRect(13, 13, 14, 14, 3, 3)
    else:
        p.drawRoundedRect(16, 9, 8, 15, 4, 4)
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(12, 12, 16, 16, 180 * 16, 180 * 16)
        p.drawLine(20, 28, 20, 32)
    p.end()
    return QIcon(pixmap)


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.setFixedHeight(30)
        self.setAccessibleName("音声レベル")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#45bfe4"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(29):
            h = 2 + min(1, self.level) * (5 + 18 * abs(math.sin(i * 1.7)))
            x = (i + 0.5) * self.width() / 29
            p.drawLine(int(x), int((30 - h) / 2), int(x), int((30 + h) / 2))


class CaptionPanel(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("caption")
        self.resize(360, 146)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 14)
        self.wave = Waveform()
        self.text = QLabel(COPY["subtitle_hint"])
        self.text.setWordWrap(True)
        self.text.setMaximumHeight(86)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setFont(QFont("Yu Gothic UI", 14))
        layout.addWidget(self.wave)
        layout.addWidget(self.text)
        self.setStyleSheet("#caption {background: rgba(24,32,46,220); border: 1px solid rgba(255,255,255,90); border-radius:18px;} QLabel {color:#f7faff; background:transparent;}")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.setBrush(QColor(24, 32, 46, 220))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)

    def show_near_bottom(self):
        area = self.screen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 44)
        self.show()


class MainWindow(QMainWindow):
    toggle_recording = Signal()
    cancel_recording = Signal()
    copy_last = Signal()
    copy_raw = Signal()
    paste_last = Signal()
    prepare_model = Signal()
    settings_changed = Signal()
    add_word = Signal(str, str)
    closed = Signal()
    open_log = Signal()
    quit_requested = Signal()
    edit_context_profiles = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(COPY["name"])
        from . import __version__
        self.setWindowTitle(f'{COPY["name"]} {__version__}')
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(650, available.width() - 40), min(720, available.height() - 80))
        self.setMinimumSize(min(500, max(360, available.width() - 40)),
                            min(440, max(320, available.height() - 80)))
        self.setWindowIcon(wave_icon())
        self.setFont(QFont("Yu Gothic UI", 12))
        self.setStyleSheet("QMainWindow,QWidget {background:#f4f5f8;color:#1b2535;} QPlainTextEdit,QLineEdit,QComboBox,QSpinBox {background:white; border:1px solid #cdd3df;border-radius:8px;padding:7px;} QPushButton {background:#e5ebf5;border:1px solid #c4cddc;border-radius:8px;padding:9px;} QPushButton:disabled {color:#929aaa;} QToolButton {border:0;background:transparent;} QTabWidget::pane {border:0;} QTabBar::tab {padding:11px 20px;border:0;background:transparent;} QTabBar::tab:selected {background:#dce7f8;border-radius:8px;} QLabel {background:transparent;}")
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 18)
        header = QHBoxLayout()
        title = QLabel(COPY["name"])
        title.setFont(QFont("Yu Gothic UI", 22, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        self.record = QToolButton()
        self.record.setIcon(wave_icon())
        self.record.setIconSize(QSize(44, 44))
        self.record.setFixedSize(54, 54)
        self.record.clicked.connect(self.toggle_recording.emit)
        self.record.setAccessibleName(COPY["start"])
        self.record.setToolTip(COPY["start"])
        header.addWidget(self.record)
        layout.addLayout(header)
        note = QLabel(COPY["cpu_only"])
        note.setStyleSheet("color:#526278;")
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        page = QWidget()
        pl = QVBoxLayout(page)
        self.wave = Waveform()
        pl.addWidget(self.wave)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Yu Gothic UI", 14))
        self.editor.setPlaceholderText(COPY["subtitle_hint"])
        pl.addWidget(self.editor, 1)
        main_hint = QLabel(COPY["main_hint"])
        main_hint.setWordWrap(True)
        pl.addWidget(main_hint)
        self.cancel = QPushButton(COPY["cancel"])
        self.cancel.clicked.connect(self.cancel_recording.emit)
        self.cancel.hide()
        pl.addWidget(self.cancel)
        self.tabs.addTab(page, COPY["dictation"])

        dictionary = QWidget()
        dl = QVBoxLayout(dictionary)
        label = QLabel(COPY["dictionary_note"])
        label.setWordWrap(True)
        dl.addWidget(label)
        self.words = QPlainTextEdit()
        self.words.setReadOnly(True)
        dl.addWidget(self.words)
        self.heard = QLineEdit()
        self.heard.setPlaceholderText(COPY["heard"])
        self.written = QLineEdit()
        self.written.setPlaceholderText(COPY["written"])
        dl.addWidget(self.heard)
        dl.addWidget(self.written)
        add = QPushButton(COPY["add_word"])
        add.clicked.connect(lambda: self.add_word.emit(self.heard.text(), self.written.text()))
        dl.addWidget(add)
        self.tabs.addTab(dictionary, COPY["dictionary"])

        settings = QWidget()
        sl = QVBoxLayout(settings)
        app_settings = QGroupBox(COPY["app_settings"])
        app_layout = QVBoxLayout(app_settings)
        self.launch_at_login = QCheckBox(COPY["launch_at_login"])
        app_layout.addWidget(self.launch_at_login)
        self.startup_status = QLabel()
        self.startup_status.setWordWrap(True)
        app_layout.addWidget(self.startup_status)
        quit_row = QHBoxLayout()
        quit_row.addStretch()
        self.quit_button = QPushButton(COPY["quit"])
        self.quit_button.clicked.connect(self.quit_requested.emit)
        quit_row.addWidget(self.quit_button)
        app_layout.addLayout(quit_row)
        sl.addWidget(app_settings)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.modifier = QComboBox()
        self.modifier.addItems([COPY["left_control"], COPY["left_alt"]])
        form.addRow(COPY["shortcut"], self.modifier)
        self.microphone = QComboBox()
        self.microphone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.microphone.setMinimumContentsLength(12)
        self.microphone.addItem(COPY["system_mic"], "auto")
        form.addRow(COPY["microphone"], self.microphone)
        sl.addLayout(form)
        toggle_hint = QLabel(COPY["toggle_hint"])
        toggle_hint.setWordWrap(True)
        sl.addWidget(toggle_hint)
        self.prepare = QPushButton(COPY["prepare_model"])
        self.prepare.clicked.connect(self.prepare_model.emit)
        sl.addWidget(self.prepare)
        for key in ["local_note", "log_note", "diagnostics_note"]:
            note = QLabel(COPY[key])
            note.setWordWrap(True)
            sl.addWidget(note)
        self.log_enabled = QCheckBox(COPY["save_log"])
        sl.addWidget(self.log_enabled)
        self.punctuation_enabled = QCheckBox(COPY["punctuation_enabled"])
        sl.addWidget(self.punctuation_enabled)
        punctuation_note = QLabel(COPY["punctuation_note"])
        punctuation_note.setWordWrap(True)
        sl.addWidget(punctuation_note)
        self.proof_enabled = QCheckBox(COPY["proof_enabled"])
        sl.addWidget(self.proof_enabled)
        proof_note = QLabel(COPY["proof_note"])
        proof_note.setWordWrap(True)
        sl.addWidget(proof_note)
        self.context_enabled = QCheckBox(COPY["context_enabled"])
        sl.addWidget(self.context_enabled)
        context_note = QLabel(COPY["context_note"])
        context_note.setWordWrap(True)
        sl.addWidget(context_note)
        self.context_profiles_button = QPushButton(COPY["input_profiles"])
        self.context_profiles_button.clicked.connect(self.edit_context_profiles.emit)
        sl.addWidget(self.context_profiles_button)
        open_log = QPushButton(COPY["open_log"])
        open_log.clicked.connect(self.open_log.emit)
        sl.addWidget(open_log)
        sl.addStretch()
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setAccessibleName(COPY["settings"])
        self.settings_scroll.setWidget(settings)
        self.tabs.addTab(self.settings_scroll, COPY["settings"])

        from .history_ui import HistoryTab, HISTORY_COPY
        self.history = HistoryTab()
        self.history.open_folder.connect(self.open_log.emit)
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.history_scroll.setWidget(self.history)
        self.tabs.addTab(self.history_scroll, HISTORY_COPY["history"])

        self.status = QLabel(COPY["ready"])
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(52)
        layout.addWidget(self.status)
        recovery = QHBoxLayout()
        self.copy_button = QPushButton(COPY["copy_last"])
        self.copy_button.clicked.connect(self.copy_last.emit)
        self.paste_button = QPushButton(COPY["paste_last"])
        self.paste_button.clicked.connect(self.paste_last.emit)
        recovery.addWidget(self.copy_button)
        recovery.addWidget(self.paste_button)
        layout.addLayout(recovery)
        self.raw_button = QPushButton(COPY["copy_raw"])
        self.raw_button.clicked.connect(self.copy_raw.emit)
        layout.addWidget(self.raw_button)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.busy(False)

    def _tab_changed(self, index):
        show_last = self.tabs.currentWidget() is not self.history_scroll
        for button in (self.copy_button, self.paste_button, self.raw_button):
            button.setVisible(show_last)

    def busy(self, busy: bool, recording=False, finishing=False):
        self.history.set_paused(busy)
        self.record.setEnabled(not busy or recording)
        self.record.setIcon(wave_icon(recording))
        title = COPY["stop" if recording else "start"]
        self.record.setAccessibleName(title)
        self.record.setToolTip(title)
        self.editor.setReadOnly(busy)
        self.cancel.setVisible(recording or finishing)
        self.copy_button.setEnabled((not busy or finishing) and bool(self.editor.toPlainText()))
        self.paste_button.setEnabled(not busy and bool(self.editor.toPlainText()))
        self.raw_button.setEnabled((not busy or finishing) and bool(self.editor.toPlainText()))
        for widget in [self.modifier, self.microphone, self.prepare, self.log_enabled, self.punctuation_enabled, self.proof_enabled, self.context_enabled, self.context_profiles_button]:
            widget.setEnabled(not busy)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.closed.emit()

    def presented(self):
        return self.isVisible() and not self.isMinimized()


class SetupWindow(QDialog):
    begin = Signal()
    later = Signal()
    complete = Signal()

    def __init__(self):
        super().__init__()
        self.ready = False
        self.setWindowTitle(COPY["setup_title"])
        self.resize(560, min(670, QApplication.primaryScreen().availableGeometry().height() - 80))
        self.setMinimumSize(500, 440)
        self.setFont(QFont("Yu Gothic UI", 12))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)
        title = QLabel(COPY["setup_title"])
        title.setFont(QFont("Yu Gothic UI", 20, QFont.Weight.Bold))
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for key in ["setup_intro", "setup_model", "setup_steps", "setup_defaults", "local_note"]:
            label = QLabel(COPY[key])
            label.setWordWrap(True)
            body_layout.addWidget(label)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.logs = QCheckBox(COPY["save_log"])
        self.logs.setChecked(True)
        layout.addWidget(self.logs)
        buttons = QHBoxLayout()
        self.later_button = QPushButton(COPY["setup_later"])
        self.later_button.clicked.connect(self.close)
        self.start_button = QPushButton(COPY["setup_begin"])
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(lambda: self.complete.emit() if self.ready else self.begin.emit())
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)

    def closeEvent(self, event):
        self.later.emit()
        super().closeEvent(event)


class InstallWindow(QDialog):
    cancel_install = Signal()
    retry_install = Signal()

    def __init__(self):
        super().__init__()
        self.install_finished = False
        self.setWindowTitle(COPY["install_title"])
        self.setMinimumWidth(500)
        self.resize(540, 240)
        self.setFont(QFont("Yu Gothic UI", 12))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        self.status = QLabel(COPY["install_copy"])
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.progress = QProgressBar()
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (self.status, self.progress, self.detail):
            layout.addWidget(widget)
        buttons = QHBoxLayout()
        self.retry_button = QPushButton(COPY["install_retry"])
        self.retry_button.clicked.connect(self.retry_install.emit)
        self.retry_button.hide()
        self.button = QPushButton(COPY["install_cancel"])
        self.button.clicked.connect(self.reject)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.button)
        layout.addLayout(buttons)

    def update_stage(self, stage, done, total, elapsed, *, finished=False, cancelling=False,
                     shortcut_ok=True, failed_stage=None):
        self.install_finished = finished
        self.status.setText(COPY["install_" + stage])
        if stage == "failed" and failed_stage in ("copy", "asr_model", "proof_model", "check_asr", "check_proof", "check_punctuation", "shortcut", "launch"):
            self.status.setText(COPY["install_failed_stage"].format(stage=COPY["install_" + failed_stage]))
        self.detail.setText(COPY["install_elapsed"].format(seconds=int(elapsed)))
        self.progress.setRange(0, 100)
        if stage in ("copy", "asr_model", "proof_model"):
            self.progress.setValue(int(100*done/total) if total else 0)
            self.detail.setText(COPY["install_progress"].format(
                done=done/1_000_000, total=total/1_000_000, seconds=int(elapsed)))
        elif not finished:
            self.progress.setRange(0, 0)
        elif stage == "complete":
            self.progress.setValue(100)
            self.detail.setText("" if shortcut_ok else COPY["install_shortcut_failed"])
        else:
            self.progress.setValue(0)
        self.retry_button.setVisible(finished and stage in ("failed", "cancelled"))
        self.button.setEnabled(not cancelling or finished)
        self.button.setText(COPY["install_close" if finished else "install_cancel"])
        if cancelling and not finished:
            self.status.setText(COPY["install_cancelling"])

    def reject(self):
        if self.install_finished:
            super().reject()
        else:
            self.cancel_install.emit()

    def closeEvent(self, event):
        if self.install_finished:
            event.accept()
        else:
            self.cancel_install.emit()
            event.ignore()
