"""Per-user Windows login registration; no changes until explicitly requested."""
from __future__ import annotations

from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "信楽"


class LoginStartup:
    def __init__(self, launcher=None, *, registry=None):
        self.launcher = Path(launcher) if launcher is not None else Path(__file__).resolve().parents[1] / "信楽.exe"
        self._registry = registry

    @property
    def command(self):
        path = str(self.launcher.resolve())
        if any(c in path for c in ('"', '\r', '\n', '\0')):
            raise ValueError("Invalid login launcher path")
        command = '"' + path + '"'
        if len(command.encode("utf-16-le")) // 2 > 260:
            raise ValueError("Login launcher path is too long")
        return command

    @property
    def registry(self):
        if self._registry is None:
            import winreg
            self._registry = winreg
        return self._registry

    def _read(self):
        reg = self.registry
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ | reg.KEY_WOW64_64KEY) as key:
                return reg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None

    def registered(self):
        value = self._read()
        return value is not None and value[1] == self.registry.REG_SZ and value[0].casefold() == self.command.casefold()

    def set_enabled(self, enabled):
        command = self.command
        reg = self.registry
        value = self._read()
        if value is not None and not self.registered():
            raise FileExistsError("The login entry belongs to another installation")
        if enabled:
            if not self.launcher.is_file():
                raise FileNotFoundError("Install the app before enabling login startup")
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE | reg.KEY_WOW64_64KEY) as key:
                reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, command)
        elif value is not None:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE | reg.KEY_WOW64_64KEY) as key:
                reg.DeleteValue(key, VALUE_NAME)
