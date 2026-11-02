from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, QMimeData, Qt
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from windows_voice_typer.recorder import Recorder
from .engine import CpuEngine, data_dir, default_model, prepare_model
from .gestures import ControlGesture, SHORTCUT_KEYS
from .native import WindowsInput, normalize_newlines
from .context import ShortContext, same_field
from .page_context import PageContext, combine_reference, excluded
from .session import format_text
from .rust_session import CaptionSession
from .vocabulary import display_words, effective_words, recognition_terms, topic_terms
from .proofread_runtime import ProofreadRuntime, prepare_model as prepare_proofreading
from .runtime_status import ProofreaderBlocked
from .proofreading import SentenceProofreader
from .punctuation import PunctuationRuntime
from .ui import COPY, CaptionPanel, MainWindow, SetupWindow, wave_icon


class Signals(QObject):
    caption = Signal(int, str)
    prepared = Signal(bool)
    download = Signal(int, int)
    revised = Signal(int)
    utterance = Signal(int, str)
    preparing_stage = Signal(str)


class App:
    def __init__(self, application: QApplication, force_setup=False, from_installer=False):
        self.initialized_at = time.monotonic()
        self.preparation_started_at = None
        self.preparation_completed_at = None
        self.preparation_metrics = {}
        self.preparing_status = COPY["preparing"]
        self.qt = application
        self.qt.setQuitOnLastWindowClosed(False)
        self.from_installer = from_installer
        self.native = WindowsInput()
        self.main = MainWindow()
        self.main.history.set_path(data_dir() / "recognition.jsonl")
        self.panel = CaptionPanel()
        self.setup = SetupWindow()
        self.signals = Signals()
        self.signals.caption.connect(self.session_caption, Qt.ConnectionType.QueuedConnection)
        self.signals.prepared.connect(self.prepared, Qt.ConnectionType.QueuedConnection)
        self.signals.download.connect(self.download_progress, Qt.ConnectionType.QueuedConnection)
        self.signals.revised.connect(self.proofread_caption, Qt.ConnectionType.QueuedConnection)
        self.signals.utterance.connect(self.session_utterance, Qt.ConnectionType.QueuedConnection)
        self.signals.preparing_stage.connect(self.preparation_stage, Qt.ConnectionType.QueuedConnection)
        self.settings_path = data_dir() / "settings.json"
        self.settings = self.read_json(self.settings_path, {"modifier": 0, "log": True, "words": []})
        from .settings_migration import migrate_control_default
        if migrate_control_default(self.settings):
            try:
                self.write_json(self.settings_path, self.settings)
            except OSError:
                pass
        self.engine = CpuEngine(default_model())
        self.proof_engine = ProofreadRuntime()
        self.punctuation_engine = PunctuationRuntime()
        self.punctuation_error = ""
        self.proof_blocked = False
        self.proofreader = None
        self.preparing_label = COPY["preparing"]
        self.correction_metrics = {}
        self.context_memory = ShortContext()
        self.page_context = PageContext()
        self.last_page_sample = 0.0
        self.context_source, self.context_chars = "none", 0
        self.proof_wait_until = None
        self.phase = "idle"
        self.generation = 0
        self.session = None
        self.recorder = None
        self.target = None
        self.last_target = None
        self.model_thread = None
        self.model_cancel = threading.Event()
        self.continuous = False
        self.cancelled = False
        self.quitting = False
        self.hold_stops = False
        self.last_capture = 0.0
        self.last_checkpoint = 0.0
        self.last_diagnostics = 0.0
        self.current_text = self.read_json(data_dir() / "last-input.json", {}).get("text", "")
        self.last_input = self.current_text
        self.raw_text = self.read_json(data_dir() / "last-input.json", {}).get("raw_text", self.current_text)
        self.main.editor.setPlainText(self.current_text)
        self.main.modifier.setCurrentIndex(int(self.settings.get("modifier", 0)))
        self.main.log_enabled.setChecked(bool(self.settings.get("log", True)))
        self.main.proof_enabled.setChecked(bool(self.settings.get("proofreading", False)))
        self.main.punctuation_enabled.setChecked(bool(self.settings.get("punctuation", True)))
        self.main.context_enabled.setChecked(bool(self.settings.get("context", True)))
        self.setup.logs.setChecked(self.main.log_enabled.isChecked())
        self.setup.logs.toggled.connect(self.main.log_enabled.setChecked)
        self.setup.begin.connect(lambda: self.prepare(retry_proof=True))
        self.setup.complete.connect(self.complete_setup)
        self.setup.later.connect(self.model_cancel.set)
        self.update_words()
        self.control = ControlGesture()
        self.alt = ControlGesture()
        self._shortcut_diagnosed = set()
        self.main.toggle_recording.connect(self.toggle_recording)
        self.main.cancel_recording.connect(self.cancel_recording)
        self.main.copy_last.connect(self.copy_last)
        self.main.copy_raw.connect(self.copy_raw)
        self.main.paste_last.connect(lambda: self.paste(self.main.editor.toPlainText(), self.last_target))
        self.main.prepare_model.connect(lambda: self.prepare(retry_proof=True))
        self.main.add_word.connect(self.add_word)
        self.main.closed.connect(self.closed)
        self.main.open_log.connect(self.open_log)
        self.main.editor.textChanged.connect(lambda: self.main.busy(self.phase != "idle", self.phase == "recording"))
        self.main.modifier.currentIndexChanged.connect(self.save_settings)
        self.main.log_enabled.toggled.connect(self.save_settings)
        self.main.proof_enabled.toggled.connect(self.save_settings)
        self.main.punctuation_enabled.toggled.connect(self.save_settings)
        self.main.context_enabled.toggled.connect(self.save_settings)
        self.main.microphone.currentIndexChanged.connect(self.save_settings)
        try:
            import sounddevice as sd
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    self.main.microphone.addItem(device["name"], index)
            for index in range(self.main.microphone.count()):
                if self.main.microphone.itemData(index) == self.settings.get("microphone", "auto"):
                    self.main.microphone.setCurrentIndex(index)
        except Exception:
            pass
        self.tray = QSystemTrayIcon(wave_icon(), self.qt)
        menu = QMenu()
        menu.addAction(COPY["show"], self.show_main)
        menu.addAction(COPY["copy_last"], self.copy_last)
        menu.addSeparator()
        menu.addAction(COPY["quit"], self.quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(COPY["name"])
        self.tray.activated.connect(lambda reason: self.toggle_main() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(20)
        self.main.busy(False)
        if from_installer:
            self.setup.show()
            QTimer.singleShot(0, self.prepare)
        elif force_setup or not self.settings.get("setup_complete", False) or not default_model().exists():
            self.setup.show()
        else:
            QTimer.singleShot(0, self.prepare)

    @staticmethod
    def read_json(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def save_settings(self, *_):
        was_enabled = bool(self.settings.get("proofreading", True))
        self.settings.update(modifier=self.main.modifier.currentIndex(),
                             log=self.main.log_enabled.isChecked(), microphone=self.main.microphone.currentData(),
                             proofreading=self.main.proof_enabled.isChecked(),
                             punctuation=self.main.punctuation_enabled.isChecked(),
                             context=self.main.context_enabled.isChecked())
        if not self.main.context_enabled.isChecked():
            self.context_memory.clear()
            self.page_context.clear()
            self.context_source, self.context_chars = "none", 0
        if not self.main.context_enabled.isChecked() or not self.main.proof_enabled.isChecked():
            if self.proofreader:
                self.proofreader.freeze()
                self.proofreader = None
        if not self.main.proof_enabled.isChecked() and self.phase == "idle":
            self.proof_blocked = False
            self.main.proof_enabled.setText(COPY["proof_enabled"])
            self.proof_engine.close()
        elif not was_enabled and self.main.proof_enabled.isChecked() and self.phase == "idle":
            QTimer.singleShot(0, lambda: self.prepare(retry_proof=True))
        try:
            self.write_json(self.settings_path, self.settings)
        except OSError:
            self.main.status.setText(COPY["log_failed"])

    def add_word(self, heard, written):
        heard, written = heard.strip(), written.strip()
        if not heard or not written or self.phase != "idle":
            return
        self.settings.setdefault("words", []).append([heard, written])
        self.save_settings()
        self.main.heard.clear()
        self.main.written.clear()
        self.update_words()

    def update_words(self):
        self.main.words.setPlainText("\n".join(f"{a} → {b}" for a, b in display_words(self.settings.get("words", []))))

    def word_rules(self):
        return effective_words(self.settings.get("words", []))

    def show_main(self):
        if self.setup.isVisible() and not self.setup.ready or not self.settings.get("setup_complete", False):
            self.setup.show()
            self.setup.raise_()
            self.setup.activateWindow()
            return
        if self.setup.ready:
            self.setup.hide()
        if self.phase == "idle":
            fresh = self.native.capture()
            if fresh:
                self.last_target = fresh
            elif self.native.foreground()[1] != os.getpid():
                self.last_target = None
                self.context_memory.clear()
        self.panel.hide()
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def toggle_main(self):
        if self.phase != "idle":
            return
        if self.main.presented():
            self.main.hide()
        else:
            self.show_main()

    def closed(self):
        if self.phase == "recording":
            self.cancel_recording()

    def tick(self):
        now = time.monotonic()
        if not self.quitting and now - self.last_page_sample >= 2:
            self.last_page_sample = now
            self.page_context.reference()  # Expire RAM entries even without another utterance.
            if self.main.context_enabled.isChecked():
                title = self.native.foreground_page()
                if isinstance(title, str):
                    self.page_context.observe(title)
            else:
                self.page_context.clear()
        if self.quitting:
            if (not self.session or self.session.done.is_set()) and (not self.model_thread or not self.model_thread.is_alive()):
                if self.session and self.session.text:
                    self.raw_text = self.session.text
                    self.retain(self.session.text, "cancelled")
                self.engine.close()
                self.proof_engine.close()
                self.punctuation_engine.close()
                self.timer.stop()
                self.tray.hide()
                self.qt.quit()
            return
        if self.phase == "modelSetup" and self.preparation_started_at is not None:
            message = COPY["preparing_elapsed"].format(status=self.preparing_status,
                seconds=int(now - self.preparation_started_at))
            self.main.status.setText(message)
            self.setup.status.setText(message)
        if self.main.presented():
            self.panel.hide()
        elif self.phase == "modelSetup" and self.preparation_started_at is not None and not self.setup.isVisible():
            self.panel.text.setText(message)
            if not self.panel.isVisible():
                self.panel.show_near_bottom()
        elif self.phase in ("recording", "finishing") and not self.cancelled and not self.panel.isVisible():
            self.panel.show_near_bottom()
        modifier = self.main.modifier.currentIndex()
        for gesture, key, is_control in [(self.control, 0xA2, True), (self.alt, 0xA4, False)]:
            selected = is_control == (modifier == 0)
            down = self.native.key_down(key)
            # Modifier states plus keyboard/mouse interactions cancel an unarmed hold.
            isolated = not down or not any(self.native.key_down(k) for k in SHORTCUT_KEYS
                                          if k not in {key, 0x10, 0x11, 0x12})
            setup_pending = self.setup.isVisible() and not self.setup.ready
            allow = not setup_pending and selected and (self.phase == "idle" or self.phase == "recording" and self.continuous)
            actions = gesture.update(now, down, isolated, busy=self.phase != "idle" or setup_pending, allow_hold=allow)
            self.shortcut_diagnostic(now, key, gesture, down, isolated, selected, actions)
            for action in actions:
                if action == "double_tap" and selected:
                    self.toggle_main()
                elif action == "hold_start" and selected:
                    if self.phase == "recording" and self.continuous:
                        self.hold_stops = True
                    elif self.phase == "idle":
                        self.start(self.main.presented())
                elif action == "hold_end" and selected:
                    if self.hold_stops or self.phase == "recording" and not self.continuous:
                        self.hold_stops = False
                        self.stop()
        if self.recorder is not None and self.phase == "recording":
            level = self.recorder.level_percent / 100
            for wave in [self.main.wave, self.panel.wave]:
                wave.level = level
                wave.update()
            if self.session:
                elapsed = time.monotonic() - self.session.started_at
                metrics = self.session.metrics
                if metrics["first_caption_seconds"] is None:
                    message = COPY["caption_wait"].format(seconds=int(elapsed))
                    self.main.status.setText(message)
                    if not self.current_text:
                        self.panel.text.setText(message)
                else:
                    self.main.status.setText(COPY["cpu_only"])
                if now - self.last_diagnostics > 1:
                    self.save_stream_diagnostics(self.session)
                    self.last_diagnostics = now
            if self.recorder.recorded_seconds > 300:
                self.stop()
            elif self.session and self.session.done.is_set() and self.session.error:
                self.stop()
        if self.session and self.phase == "finishing" and self.session.done.is_set():
            self.finish()

    def shortcut_diagnostic(self, now, key, gesture, down, isolated, selected, actions):
        if not down:
            if gesture.pressed_at is None:
                self._shortcut_diagnosed.discard(key)
            return
        if (not selected or gesture.pressed_at is None or now - gesture.pressed_at < 0.24
                or key in self._shortcut_diagnosed):
            return
        self._shortcut_diagnosed.add(key)
        from . import __version__
        try:
            # Resample once for this diagnostic. Store only fixed categories;
            # never the VK values themselves or a per-key event history.
            conflicts = [k for k in SHORTCUT_KEYS
                         if k not in {key, 0x10, 0x11, 0x12} and self.native.key_down(k)]
            # One snapshot per held activation key. Never retain other key codes,
            # document text or keystroke sequences. "accepted" means hold_start.
            self.write_json(data_dir() / "hotkey-diagnostics.json", {
                "version": __version__, "phase": self.phase,
                "selected_modifier": self.main.modifier.currentIndex(),
                "setup_visible": self.setup.isVisible(), "setup_ready": self.setup.ready,
                "input_ready": bool(self.engine.ready), "isolated": bool(isolated),
                "blocked": bool(gesture.blocked), "accepted": "hold_start" in actions,
                "navigation_down": any(0x88 <= k <= 0x8F for k in conflicts),
                "abnt_down": any(k in (0xC1, 0xC2) for k in conflicts),
                "other_valid_down": any(not (0x88 <= k <= 0x8F or k in (0xC1, 0xC2))
                                        for k in conflicts),
            })
        except OSError:
            pass

    def toggle_recording(self):
        if self.phase == "idle":
            self.start(True)
        elif self.phase == "recording":
            self.stop()

    def start(self, continuous):
        if self.phase != "idle":
            return
        if not default_model().exists() or not self.engine.ready:
            self.setup.ready = False
            self.setup.start_button.setText(COPY["setup_retry"])
            self.setup.status.setText(COPY["setup_both_failed"])
            self.setup.show()
            return
        if self.setup.ready:
            self.setup.hide()
        fresh = self.native.capture()
        self.target = fresh or (self.last_target if self.native.foreground()[1] == os.getpid() else None)
        reference_target = fresh
        if reference_target is None and self.target and self.native.unchanged(self.target):
            reference_target = self.target
        self.generation += 1
        self.continuous = continuous
        self.cancelled = False
        self.phase = "recording"
        self.current_text = ""
        self.correction_metrics = {}
        self.proof_wait_until = None
        self.main.editor.clear()
        self.main.status.setText("")
        self.main.tabs.setCurrentIndex(0)
        self.main.busy(True, True)
        self.recorder = Recorder(device=self.main.microphone.currentData())
        try:
            self.recorder.start()
        except Exception as error:
            self.phase = "idle"
            self.recorder = None
            self.main.editor.setPlainText(self.last_input)
            self.main.status.setText(COPY["microphone_failed"])
            self.main.busy(False)
            try:
                self.write_json(data_dir() / "microphone-error.json", {"error": type(error).__name__, "detail": str(error)})
            except OSError:
                pass
            if not self.main.presented():
                self.panel.text.setText(COPY["microphone_failed"])
                self.panel.show_near_bottom()
                QTimer.singleShot(10000, lambda: self.panel.hide() if self.phase == "idle" else None)
            return
        generation = self.generation
        self.raw_text = ""
        self.correction_metrics = {}
        rules = self.word_rules()
        protected = tuple(dict.fromkeys(written for _, written in rules))
        reference = ""
        self.context_source, self.context_chars = "none", 0
        if self.main.context_enabled.isChecked():
            reference = self.context_memory.context_for(reference_target)
            self.context_source, self.context_chars = self.context_memory.source, len(reference)
            if self.native.last_capture_checks.get("password", False) is not True:
                foreground = self.native.foreground()
                location = (reference_target.hwnd, reference_target.pid) if reference_target is not None else foreground
                title = self.native.window_title(*location)
                if isinstance(title, str):
                    if excluded(title):
                        self.page_context.clear()
                        self.context_memory.clear()
                    reference = combine_reference(reference, title, self.page_context.reference())
                    self.context_source = "field_and_pages" if reference else "none"
                    self.context_chars = len(reference)
            else:
                self.context_memory.clear()
                self.page_context.clear()
                reference = ""
                self.context_source, self.context_chars = "none", 0
        else:
            self.context_memory.clear()
            self.page_context.clear()
        terms = recognition_terms(rules, self.settings.get("words", []), context=reference)
        if self.main.proof_enabled.isChecked() and self.proof_engine.ready:
            self.proofreader = SentenceProofreader(self.proof_engine,
                lambda: self.signals.revised.emit(generation),
                protected=protected, context=reference, vocabulary=rules)
        self.engine.set_vocabulary(rules)
        self.session = CaptionSession(self.engine, self.recorder,
            lambda text: self.signals.caption.emit(generation, text),
            on_utterance=lambda text: self.signals.utterance.emit(generation, text), phrases=terms)
        self.session.start()
        if not self.main.presented():
            self.panel.text.setText(COPY["subtitle_hint"])
            self.panel.show_near_bottom()
        self.tray.setIcon(wave_icon(True))

    def session_caption(self, generation, text):
        if generation == self.generation and self.phase in ("recording", "finishing"):
            self.raw_text = text
            value = format_text(text, self.word_rules())
            self.caption(self.proofreader.update(value) if self.proofreader else value)

    def session_utterance(self, generation, text):
        if generation == self.generation and self.phase in ("recording", "finishing") and self.proofreader:
            self.raw_text = text
            self.caption(self.proofreader.update(format_text(text, self.word_rules()), final=True))

    def proofread_caption(self, generation):
        if generation == self.generation and self.phase in ("recording", "finishing") and self.proofreader:
            self.caption(self.proofreader.snapshot()[1])

    def caption(self, text):
        self.current_text = text
        self.main.editor.setPlainText(text)
        cursor = self.main.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.main.editor.setTextCursor(cursor)
        self.panel.text.setText(text[-100:] or COPY["subtitle_hint"])
        if text and time.monotonic() - self.last_checkpoint > 3:
            self.retain(text, "recognizing")
            self.last_checkpoint = time.monotonic()

    def stop(self):
        if self.phase != "recording":
            return
        self.phase = "finishing"
        self.main.busy(True, finishing=True)
        self.main.status.setText(COPY["finishing"])
        try:
            self.recorder.stop_audio()
        except Exception:
            self.cancelled = True
        self.session.stop()

    def cancel_recording(self):
        if self.phase not in ("recording", "finishing"):
            return
        self.cancelled = True
        self.context_memory.clear()
        self.session.cancel()
        self.proof_wait_until = None
        if self.phase == "recording":
            self.stop()

    def finish(self):
        session = self.session
        quick_punctuation = self.main.punctuation_enabled.isChecked() and self.punctuation_engine.ready
        if self.proofreader and not quick_punctuation and not self.cancelled and not session.error:
            self.proofreader.update(format_text(session.text, self.word_rules()), final=True)
            if self.proofreader.has_pending():
                if self.proof_wait_until is None:
                    self.proof_wait_until = time.monotonic() + 1.0
                if time.monotonic() < self.proof_wait_until:
                    self.main.status.setText(COPY["proof_finishing"])
                    return
        self.save_stream_diagnostics(session)
        self.session = None
        self.recorder = None
        self.tray.setIcon(wave_icon())
        self.phase = "idle"
        self.main.busy(False)
        text = session.text or self.current_text
        self.raw_text = text
        original = format_text(text, self.word_rules())
        text = original
        if self.proofreader:
            self.proofreader.update(original)
            _, text = self.proofreader.freeze()
            self.correction_metrics = dict(self.proofreader.metrics)
            self.proofreader = None
        if self.cancelled or session.error:
            if text:
                self.caption(text)
                self.retain(text, "cancelled" if self.cancelled else "recognitionFailed")
            else:
                self.main.editor.setPlainText(self.last_input)
            self.main.status.setText(COPY["cancelled" if self.cancelled else "failed"])
            self.panel.hide()
            return
        if not text:
            self.main.editor.setPlainText(self.last_input)
            self.main.status.setText(COPY["silence"])
            self.panel.hide()
            return
        if quick_punctuation:
            began = time.monotonic()
            try:
                protected = tuple(dict.fromkeys(written for _,written in self.word_rules()))
                text = self.punctuation_engine.restore_recent(text, protected=protected)
                self.correction_metrics["punctuation_seconds"] = time.monotonic()-began
                self.correction_metrics["punctuation_characters"] = min(400,len(original))
            except Exception as error:
                # Never lose or delay the ASR text when optional punctuation fails.
                self.punctuation_error = type(error).__name__
        self.last_input = text
        self.caption(text)
        self.retain(text, "recognizing")
        self.paste(text, self.target)

    def save_stream_diagnostics(self, session):
        if not hasattr(session, "metrics"):
            return
        from . import __version__
        import platform
        try:
            self.write_json(data_dir() / "stream-diagnostics.json", {
                "version": __version__, "platform": platform.system(),
                "cpu": platform.processor(), "threads": self.engine.threads,
                "phase": self.phase, "elapsed_seconds": time.monotonic() - session.started_at,
                **dict(session.metrics), "error": session.error,
            })
        except OSError:
            pass

    def retain(self, text, outcome, target=...):
        if not text:
            return
        destination = self.target if target is ... else target
        record = {"at": datetime.now().astimezone().isoformat(), "text": text, "outcome": outcome,
                  "raw_text": self.raw_text, "proofreading": self.correction_metrics,
                  "app": destination.app if destination else "", "pid": destination.pid if destination else None}
        try:
            self.write_json(data_dir() / "last-input.json", record)
            if outcome != "recognizing" and self.main.log_enabled.isChecked():
                with (data_dir() / "recognition.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self.main.status.setText(COPY["log_failed"])

    def copy_last(self):
        text = self.main.editor.toPlainText() or self.last_input
        if self.phase in ("idle", "finishing") and text:
            self.qt.clipboard().setText(text)
            self.main.status.setText(COPY["copied"])

    def copy_raw(self):
        if self.phase in ("idle", "finishing") and self.raw_text:
            self.qt.clipboard().setText(self.raw_text)
            self.main.status.setText(COPY["raw_copied"])

    def paste(self, text, target):
        if not text or self.phase != "idle":
            return
        self.phase = "pasting"
        self.main.busy(True)
        started = time.monotonic()

        def attempt():
            if not self.native.modifiers_up() and time.monotonic() - started < 0.5:
                QTimer.singleShot(25, attempt)
                return
            if not target or not self.native.prepare_paste(target):
                complete("notAttempted")
                return
            clipboard = self.qt.clipboard()
            saved = QMimeData()
            previous = clipboard.mimeData()
            if previous is not None:
                for mime in previous.formats():
                    saved.setData(mime, previous.data(mime))
            clipboard.setText(text)
            sequence = self.native.user.GetClipboardSequenceNumber()

            def restore():
                if self.native.user.GetClipboardSequenceNumber() == sequence:
                    clipboard.setMimeData(saved)
            QTimer.singleShot(1500, restore)
            # Copying delayed clipboard formats may take time; recheck immediately.
            if not self.native.prepare_paste(target):
                complete("notAttempted")
                return
            posted = self.native.send_paste(target)
            if not posted:
                complete("sendFailed")
                return
            sent_at = time.monotonic()

            def verify():
                receipt = self.native.paste_receipt(target, text)
                if receipt is True:
                    complete("confirmed", "matched")
                elif receipt is None:
                    complete("sentUnverified", "unavailable_or_formatted")
                elif time.monotonic() - sent_at >= 1.5:
                    complete("sentUnverified", "unchanged")
                else:
                    QTimer.singleShot(25, verify)
            QTimer.singleShot(25, verify)

        def complete(outcome, readback="not_checked"):
            self.phase = "idle"
            self.main.busy(False)
            self.retain(text, outcome, target=target)
            reason = getattr(self.native, "last_target_reason", "unavailable")
            prepare_reason = getattr(self.native, "last_prepare_reason", "not_checked")
            capture_checks = getattr(self.native, "last_capture_checks", {})
            capture_fallback = getattr(self.native, "last_capture_fallback", "")
            capture_error = getattr(self.native, "last_capture_error", "")
            try:
                from . import __version__
                from .paste_diagnostics import with_history
                diagnostic_path = data_dir() / "paste-diagnostics.json"
                target_app = getattr(target, "app", "")
                if not isinstance(target_app, str) or not target_app:
                    try:
                        target_app = self.native.app_name(self.native.foreground()[1])
                    except Exception:
                        target_app = ""
                self.write_json(diagnostic_path, with_history(diagnostic_path, {
                    "version": __version__, "outcome": outcome,
                    "target_capture": reason if isinstance(reason, str) else "unavailable",
                    "capture_fallback_from": capture_fallback if isinstance(capture_fallback, str) else "",
                    "capture_error_type": capture_error if isinstance(capture_error, str) else "",
                    "target_present": target is not None,
                    "target_capture_checks": ({key: value for key, value in capture_checks.items()
                        if key in {"same_process", "password", "enabled", "keyboard_focus"} and type(value) is bool}
                        if isinstance(capture_checks, dict) else {}),
                    "transfer_mode": getattr(target, "paste_mode", "verified") if target else "none",
                    "prepare_check": (prepare_reason if isinstance(prepare_reason, str) else "unavailable")
                        if target else "not_checked",
                    "readback": readback,
                    "asr_ready": bool(self.engine.ready),
                    "proof_enabled": self.main.proof_enabled.isChecked(),
                    "proof_ready": bool(self.proof_engine.ready),
                    "punctuation_ready": bool(self.punctuation_engine.ready),
                    "punctuation_enabled": self.main.punctuation_enabled.isChecked(),
                    "short_context_enabled": self.main.context_enabled.isChecked(),
                    "reference_source": self.context_source, "reference_chars": self.context_chars,
                    "proofreading": {key: self.correction_metrics.get(key, 0) for key in (
                        "sentences_checked", "sentences_changed", "suggestions", "suggestions_rejected",
                        "errors", "max_seconds", "pending_at_stop", "max_context_chars",
                        "punctuation_seconds", "punctuation_characters")},
                    "seconds_after_preparation": (
                        max(0.0, time.monotonic() - self.preparation_completed_at)
                        if self.preparation_completed_at is not None else None),
                }, edition="haas", target_app=target_app))
            except OSError:
                pass
            message = {"confirmed": "confirmed", "sentUnverified": "inserted",
                       "notAttempted": "no_target", "sendFailed": "send_failed",
                       "unconfirmed": "unconfirmed"}[outcome]
            self.main.status.setText(COPY[message])
            if outcome in ("confirmed", "sentUnverified"):
                if outcome == "confirmed":
                    fresh = self.native.capture()
                    self.last_target = fresh if same_field(fresh, target) else None
                    try:
                        matched = (self.main.context_enabled.isChecked()
                            and same_field(fresh, target) and normalize_newlines(fresh.value)
                            == normalize_newlines(target.prefix + text + target.suffix))
                    except AttributeError:
                        matched = False
                    if matched:
                        topics = topic_terms(text, self.word_rules())
                        if topics:
                            self.context_memory.remember(fresh, text, topics=topics)
                        else:
                            self.context_memory.remember(fresh, text)
                    else:
                        self.context_memory.clear()
                else:
                    self.context_memory.clear()
                self.panel.hide()
            elif not self.main.presented():
                self.context_memory.clear()
                self.panel.text.setText(COPY[message])
                self.panel.show_near_bottom()
                QTimer.singleShot(6000, lambda: self.panel.hide() if self.phase == "idle" else None)
            else:
                self.context_memory.clear()
        attempt()

    def prepare(self, *, retry_proof=False):
        if self.phase != "idle":
            return
        self.phase = "modelSetup"
        self.preparation_started_at = time.monotonic()
        self.preparation_completed_at = None
        self.preparation_metrics = {}
        self.proof_blocked = False
        self.punctuation_error = ""
        self.preparing_label = COPY["preparing"]
        self.preparing_status = self.preparing_label
        proof_enabled = self.main.proof_enabled.isChecked()
        self.model_cancel.clear()
        self.main.busy(True)
        self.main.status.setText(COPY["preparing"])
        self.setup.start_button.setEnabled(False)
        self.setup.logs.setEnabled(False)
        self.setup.status.setText(COPY["preparing"])
        self.setup.progress.setRange(0, 0)
        def work():
            success = False
            def timed(name, action):
                before = time.monotonic()
                try:
                    return action()
                finally:
                    self.preparation_metrics[name] = time.monotonic() - before
            try:
                timed("asr_file_seconds", lambda: prepare_model(default_model(), self.signals.download.emit, cancel=self.model_cancel, repair=True))
                if self.model_cancel.is_set():
                    raise InterruptedError("cancelled")
                self.signals.download.emit(0, 0)
                timed("asr_load_warmup_seconds", lambda: self.engine.load(cancel=self.model_cancel))
                if self.model_cancel.is_set():
                    raise InterruptedError("cancelled")
                success = True
                self.signals.preparing_stage.emit(COPY["punctuation_preparing"])
                try:
                    timed("punctuation_load_seconds", lambda: self.punctuation_engine.load(cancel=self.model_cancel))
                except Exception as error:
                    self.punctuation_error = type(error).__name__
                if proof_enabled:
                    self.signals.preparing_stage.emit(COPY["proof_preparing"])
                    try:
                        timed("proof_file_seconds", lambda: prepare_proofreading(self.signals.download.emit, cancel=self.model_cancel))
                        self.signals.download.emit(0, 0)
                        timed("proof_load_seconds", lambda: self.proof_engine.load(cancel=self.model_cancel, retry_blocked=retry_proof))
                    except Exception as error:
                        self.proof_blocked = isinstance(error, ProofreaderBlocked)
                        self.write_json(data_dir() / "proofreading-error.json", {"error": type(error).__name__,
                            "policy_blocked": self.proof_blocked,
                            "status": f"0x{error.code:08X}" if self.proof_blocked else ""})
            except Exception as error:
                try:
                    self.write_json(data_dir() / "model-error.json", {"error": type(error).__name__, "detail": str(error)})
                except OSError:
                    pass
            finally:
                self.signals.prepared.emit(success)
        self.model_thread = threading.Thread(target=work, daemon=True, name="ShigarakiModel")
        self.model_thread.start()

    def prepared(self, success):
        self.phase = "idle"
        self.main.busy(False)
        self.setup.ready = success
        self.setup.start_button.setEnabled(True)
        self.setup.logs.setEnabled(True)
        self.setup.progress.setRange(0, 100)
        self.setup.progress.setValue(100 if success else 0)
        partial = success and self.main.proof_enabled.isChecked() and not self.proof_engine.ready
        blocked = partial and self.proof_blocked
        self.main.proof_enabled.setText(COPY["proof_blocked_option" if blocked else "proof_enabled"])
        self.main.punctuation_enabled.setText(COPY["punctuation_enabled" if self.punctuation_engine.ready else "punctuation_unavailable"])
        elapsed = None
        if self.preparation_started_at is not None:
            import platform
            from . import __version__
            now = time.monotonic()
            self.preparation_completed_at = now if success else None
            elapsed = now - self.preparation_started_at
            try:
                self.write_json(data_dir() / "startup-diagnostics.json", {
                    "version": __version__, "platform": platform.system(),
                    "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
                    "preparation_seconds": elapsed,
                    "initialization_elapsed_seconds": now - self.initialized_at,
                    "input_ready": bool(success and not (self.from_installer and partial and not blocked)),
                    "proof_enabled": self.main.proof_enabled.isChecked(),
                    "proof_ready": bool(self.proof_engine.ready),
                    "punctuation_ready": bool(self.punctuation_engine.ready),
                    "punctuation_error_type": self.punctuation_error,
                    "proof_policy_blocked": blocked,
                    "cancelled": self.model_cancel.is_set(), **self.preparation_metrics,
                })
            except OSError:
                pass
            self.preparation_started_at = None
        self.panel.hide()
        if self.from_installer and partial and not blocked:
            self.setup.ready = False
            self.setup.progress.setValue(0)
            self.setup.start_button.setText(COPY["setup_retry"])
        self.main.status.setText(COPY["proof_blocked" if blocked else "proof_unavailable" if partial else "model_ready" if success else "model_failed"])
        self.setup.status.setText(COPY["proof_blocked" if blocked else "proof_unavailable" if partial else "setup_ready" if success else "setup_stopped" if self.model_cancel.is_set() else "model_failed"])
        self.setup.start_button.setText(COPY["setup_done" if self.setup.ready else "setup_retry"])
        if self.from_installer and partial and not blocked:
            self.setup.status.setText(COPY["setup_both_failed"])
        if success and not (self.from_installer and partial and not blocked):
            self.settings["setup_complete"] = True
            self.save_settings()
            if self.from_installer:
                self.from_installer = False
                self.complete_setup()
        if success and not partial and elapsed is not None:
            self.main.status.setText(COPY["model_ready_elapsed"].format(seconds=elapsed))

    def download_progress(self, n, total):
        if not total:
            self.preparing_status = self.preparing_label + COPY["loading_suffix"]
            self.main.status.setText(self.preparing_status)
            self.setup.status.setText(self.preparing_status)
            self.setup.progress.setRange(0, 0)
            return
        status = self.preparing_label + f" {n // 1_000_000}/{total // 1_000_000} MB"
        self.preparing_status = status
        self.main.status.setText(status)
        self.setup.status.setText(status)
        self.setup.progress.setRange(0, 100)
        self.setup.progress.setValue(int(100 * n / total))

    def preparation_stage(self, label):
        self.preparing_label = label
        self.download_progress(0, 0)

    def complete_setup(self):
        if not self.setup.ready:
            return
        self.setup.hide()
        self.show_main()
        self.main.status.setText(COPY["proof_blocked"] if self.proof_blocked else COPY["proof_unavailable"] if self.main.proof_enabled.isChecked() and not self.proof_engine.ready else COPY["ready"])

    def open_log(self):
        data_dir().mkdir(parents=True, exist_ok=True)
        os.startfile(data_dir())

    def quit(self):
        self.quitting = True
        self.context_memory.clear()
        self.page_context.clear()
        self.model_cancel.set()
        if self.phase == "recording":
            self.cancel_recording()
        if self.session:
            self.session.cancel()
        if self.proofreader:
            self.proofreader.freeze()
        self.panel.hide()
        self.main.hide()
        self.setup.hide()
