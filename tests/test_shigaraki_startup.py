import tempfile
import unittest
from pathlib import Path

from shigaraki_win.startup import LoginStartup, RUN_KEY, VALUE_NAME


class RegistryFixture:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, KEY_WOW64_64KEY, REG_SZ = range(1, 6)

    def __init__(self):
        self.values = {"Other app": ('"C:\\Other\\app.exe"', self.REG_SZ)}
        self.writes = []
        self.fail_write = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def OpenKey(self, hive, path, _, access):
        assert hive == self.HKEY_CURRENT_USER and path == RUN_KEY
        return self

    CreateKeyEx = OpenKey

    def QueryValueEx(self, _, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, _, name, reserved, kind, command):
        if self.fail_write:
            raise PermissionError("fixture")
        self.writes.append((name, command))
        self.values[name] = (command, kind)

    def DeleteValue(self, _, name):
        if self.fail_write:
            raise PermissionError("fixture")
        self.writes.append((name, None))
        del self.values[name]


class LoginStartupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.launcher = Path(self.folder.name) / "space 日本語" / "信楽.exe"
        self.launcher.parent.mkdir()
        self.launcher.write_bytes(b"launcher fixture")
        self.registry = RegistryFixture()
        self.startup = LoginStartup(self.launcher, registry=self.registry)

    def test_initial_read_does_not_enable_or_create_registration(self):
        before = dict(self.registry.values)
        self.assertFalse(self.startup.registered())
        self.assertEqual(self.registry.values, before)
        self.assertEqual(self.registry.writes, [])

    def test_enable_quotes_launcher_and_disable_keeps_other_entries(self):
        other = self.registry.values["Other app"]
        self.startup.set_enabled(True)
        command, kind = self.registry.values[VALUE_NAME]
        self.assertEqual(command, '"' + str(self.launcher.resolve()) + '"')
        self.assertTrue(self.startup.registered())
        self.startup.set_enabled(False)
        self.assertFalse(self.startup.registered())
        self.assertEqual(self.registry.values, {"Other app": other})
        self.startup.set_enabled(False)

    def test_missing_launcher_cannot_be_registered_but_can_be_removed(self):
        self.launcher.unlink()
        with self.assertRaises(FileNotFoundError):
            self.startup.set_enabled(True)
        self.assertEqual(self.registry.writes, [])
        self.registry.values[VALUE_NAME] = (self.startup.command, self.registry.REG_SZ)
        self.startup.set_enabled(False)
        self.assertNotIn(VALUE_NAME, self.registry.values)

    def test_conflicting_registration_is_preserved_on_both_actions(self):
        self.registry.values[VALUE_NAME] = ('"C:\\Other\\信楽.exe"', self.registry.REG_SZ)
        before = dict(self.registry.values)
        self.assertFalse(self.startup.registered())
        for enabled in (True, False):
            with self.assertRaises(FileExistsError):
                self.startup.set_enabled(enabled)
            self.assertEqual(self.registry.values, before)

    def test_permission_failure_does_not_report_registration_success(self):
        self.registry.fail_write = True
        with self.assertRaises(PermissionError):
            self.startup.set_enabled(True)
        self.assertFalse(self.startup.registered())

    def test_command_is_not_silently_truncated(self):
        self.startup.launcher = self.launcher.parent / ("a" * 260) / "信楽.exe"
        with self.assertRaises(ValueError):
            self.startup.set_enabled(True)
        self.assertEqual(self.registry.writes, [])


if __name__ == "__main__":
    unittest.main()
