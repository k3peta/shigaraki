"""Small optional editor; regular dictation needs no additional steps."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .context_profiles import MAX_PROFILES, load_profiles, validate_profile
from .ui import COPY


class ContextProfilesDialog(QDialog):
    def __init__(self, profiles, save, *, app="", host="", lookup=None, parent=None):
        super().__init__(parent)
        self.setFont(parent.font() if parent is not None else QFont("Yu Gothic UI", 12))
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())
        self.setWindowTitle(COPY["input_profiles"])
        self.resize(560, 650)
        self.profiles = load_profiles(profiles)
        self._save, self._default_app, self._default_host = save, app, host
        self._lookup = lookup
        layout = QVBoxLayout(self)
        note = QLabel(COPY["input_profiles_note"])
        note.setWordWrap(True)
        layout.addWidget(note)
        self.entries = QListWidget()
        self.entries.setAccessibleName(COPY["input_profiles"])
        self.entries.setMaximumHeight(125)
        self.entries.currentRowChanged.connect(self.select_entry)
        layout.addWidget(self.entries)
        row = QHBoxLayout()
        self.new_button = QPushButton(COPY["profile_new"])
        self.new_button.clicked.connect(self.new_entry)
        self.delete_button = QPushButton(COPY["profile_delete"])
        self.delete_button.clicked.connect(self.delete_entry)
        row.addWidget(self.new_button); row.addWidget(self.delete_button); row.addStretch()
        layout.addLayout(row)
        content = QWidget()
        form = QFormLayout(content)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.kind = QComboBox()
        self.kind.addItem(COPY["profile_application"], "application")
        self.kind.addItem(COPY["profile_website"], "website")
        self.kind.currentIndexChanged.connect(self.kind_changed)
        form.addRow(COPY["profile_target"], self.kind)
        self.identifier = QLineEdit()
        self.identifier.setMaxLength(4096)
        form.addRow(COPY["profile_identifier"], self.identifier)
        self.use_current_button = QPushButton(COPY["profile_use_current"])
        self.use_current_button.setEnabled(lookup is not None)
        self.use_current_button.clicked.connect(self.use_current)
        form.addRow(self.use_current_button)
        self.enabled = QCheckBox(COPY["profile_enabled"])
        self.enabled.setChecked(True)
        form.addRow(self.enabled)
        self.topic = QPlainTextEdit()
        self.topic.setMaximumHeight(100)
        self.topic.setAccessibleName(COPY["profile_topic"])
        form.addRow(COPY["profile_topic"], self.topic)
        self.terms = QPlainTextEdit()
        self.terms.setMaximumHeight(125)
        self.terms.setAccessibleName(COPY["profile_terms"])
        form.addRow(COPY["profile_terms"], self.terms)
        self.bounds = QLabel(COPY["profile_limits"])
        self.bounds.setWordWrap(True)
        form.addRow(self.bounds)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True); scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.save_button = QPushButton(COPY["profile_save"])
        self.save_button.clicked.connect(self.save_entry)
        close = QPushButton(COPY["profile_close"])
        close.clicked.connect(self.close)
        buttons.addWidget(self.save_button); buttons.addStretch(); buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh_list()
        self.new_entry()

    def kind_changed(self):
        website = self.kind.currentData() == "website"
        self.identifier.setPlaceholderText("chatgpt.com" if website else "chrome.exe")
        if self.entries.currentRow() < 0:
            self.identifier.setText(self._default_host if website else self._default_app)

    def refresh_list(self, selected=-1):
        blocked = self.entries.blockSignals(True)
        self.entries.clear()
        for profile in self.profiles:
            title = COPY["profile_website" if profile["kind"] == "website" else "profile_application"]
            suffix = "" if profile["enabled"] else " · " + COPY["profile_disabled"]
            self.entries.addItem(title + " · " + profile["identifier"] + suffix)
        self.entries.setCurrentRow(selected)
        self.entries.blockSignals(blocked)
        self.delete_button.setEnabled(selected >= 0)

    def use_current(self):
        if self._lookup is None:
            return
        app, host = self._lookup(self.kind.currentData())
        value = host if self.kind.currentData() == "website" else app
        if value:
            self.identifier.setText(value)
            self.message.clear()
        else:
            self.message.setText(COPY["profile_target_unavailable"])

    def new_entry(self):
        self.entries.setCurrentRow(-1)
        self.kind.setCurrentIndex(0)
        self.identifier.setText(self._default_app)
        self.enabled.setChecked(True)
        self.topic.clear(); self.terms.clear(); self.message.clear()
        self.delete_button.setEnabled(False)
        self.kind_changed()

    def select_entry(self, index):
        if not 0 <= index < len(self.profiles):
            return
        profile = self.profiles[index]
        self.kind.setCurrentIndex(int(profile["kind"] == "website"))
        self.identifier.setText(profile["identifier"])
        self.enabled.setChecked(profile["enabled"])
        self.topic.setPlainText(profile["topic"])
        self.terms.setPlainText("\n".join(profile["terms"]))
        self.delete_button.setEnabled(True)
        self.message.clear()

    def save_entry(self):
        try:
            profile = validate_profile({"kind": self.kind.currentData(), "identifier": self.identifier.text(),
                "topic": self.topic.toPlainText(), "terms": [line for line in self.terms.toPlainText().splitlines() if line.strip()],
                "enabled": self.enabled.isChecked()})
        except ValueError:
            self.message.setText(COPY["profile_invalid"])
            return
        index = self.entries.currentRow()
        if any(i != index and (p["kind"], p["identifier"]) == (profile["kind"], profile["identifier"])
               for i, p in enumerate(self.profiles)):
            self.message.setText(COPY["profile_duplicate"])
            return
        updated = [p for i, p in enumerate(self.profiles) if i != index]
        if len(updated) >= MAX_PROFILES:
            self.message.setText(COPY["profile_limit_reached"])
            return
        updated.append(profile)
        if not self._save(updated):
            self.message.setText(COPY["profile_save_failed"])
            return
        self.profiles = updated
        self.refresh_list(len(updated)-1)
        self.message.setText(COPY["profile_saved"])

    def delete_entry(self):
        index = self.entries.currentRow()
        if not 0 <= index < len(self.profiles):
            return
        updated = [p for i, p in enumerate(self.profiles) if i != index]
        if not self._save(updated):
            self.message.setText(COPY["profile_save_failed"])
            return
        self.profiles = updated
        self.refresh_list(); self.new_entry()
