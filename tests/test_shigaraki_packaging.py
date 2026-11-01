import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.install_portable import install, create_shortcut, copy_payload, remove_shortcuts
from shigaraki_win import install_portable

spec = importlib.util.spec_from_file_location("installer_builder", Path(__file__).resolve().parents[1] / "scripts/build_shigaraki_installer.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ShortcutFixture:
    def __init__(self, path, target=""):
        self.path = Path(path)
        self.TargetPath = target
        self.Arguments = ""
        self.IconLocation = ""
        self.WorkingDirectory = ""
        self.saves = 0

    def Save(self):
        self.saves += 1
        self.path.write_text("shortcut: " + self.TargetPath)


class ShellFixture:
    def __init__(self, root):
        self.menu, self.desktop = root / "Start Menu", root / "Desktop"
        self.menu.mkdir(); self.desktop.mkdir()
        self.links = {}

    def SpecialFolders(self, name):
        return str({"Programs": self.menu, "Desktop": self.desktop}[name])

    def CreateShortCut(self, value):
        path = Path(value)
        if path not in self.links:
            self.links[path] = ShortcutFixture(path)
        return self.links[path]

    def existing(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("existing shortcut")
        self.links[path] = ShortcutFixture(path, str(target))
        return self.links[path]


class PackagingTests(unittest.TestCase):
    def test_preview_copy_installs_speech_and_leaves_standard_app_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source"
            (source / "speech").mkdir(parents=True)
            (source / "speech" / "shigaraki-haas-flow.exe").write_bytes(b"haas Rust CPU worker")
            (source / "信楽.exe").write_bytes(b"preview launcher")
            standard = root / "Programs" / "Shigaraki"
            standard.mkdir(parents=True)
            (standard / "信楽.exe").write_bytes(b"standard sentinel")
            data = root / "ShigarakiHaasPreview"
            data.mkdir()
            for name in ("settings.json", "models.gguf", "recognition.jsonl"):
                (data / name).write_bytes(b"existing Haas data")
            destination = root / "Programs" / "ShigarakiHaasPreview"
            copy_payload(source, destination)
            self.assertEqual((destination / "speech" / "shigaraki-haas-flow.exe").read_bytes(), b"haas Rust CPU worker")
            self.assertEqual((destination / "信楽.exe").read_bytes(), b"preview launcher")
            self.assertEqual((standard / "信楽.exe").read_bytes(), b"standard sentinel")
            self.assertEqual(list(standard.iterdir()), [standard / "信楽.exe"])
            self.assertTrue(all(p.read_bytes() == b"existing Haas data" for p in data.iterdir()))

    def test_installed_native_launcher_is_used_after_models_are_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "信楽.exe").write_bytes(b"fixture")
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.setup_models.prepare_both"), patch("shigaraki_win.install_portable.verify_installed_models"), patch("shigaraki_win.install_portable.subprocess.run"), patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                install(root, root, lambda *_: None, threading.Event(), root / "log")
            self.assertEqual(launch.call_args.args[0], [str(root / "信楽.exe"), "--installed"])

    def test_a5_builder_emits_new_launcher_and_version_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / "payload"
            (payload / "shigaraki_win").mkdir(parents=True)
            (payload / "shigaraki_win/app.ico").write_bytes(b"fixture icon")
            output = root / "Shigaraki-Windows-0.1.0a5-setup.exe"
            build = root / "build"
            def run(command, **kwargs):
                if command[0] == "x86_64-w64-mingw32-gcc":
                    Path(command[-1]).write_bytes(b"launcher")
                if command[0] == "makensis":
                    output.write_bytes(b"installer")
            with patch.object(builder.shutil, "which", return_value="tool"), patch.object(builder.subprocess, "run", side_effect=run):
                result = builder.build_installer(payload, output, build, "0.1.0a5")
            self.assertEqual((payload / "信楽.exe").read_bytes(), b"launcher")
            self.assertFalse((payload / "信楽 Haasテスト.exe").exists())
            resource = (build / "launcher.rc").read_text()
            self.assertIn('VALUE "ProductName", "信楽\\0"', resource)
            self.assertIn('VALUE "OriginalFilename", "信楽.exe\\0"', resource)
            self.assertIn("FILEVERSION 0,1,0,5", resource)
            self.assertEqual(Path(result["file"]).name, "Shigaraki-Windows-0.1.0a5-setup.exe")

    def test_upgrade_uses_same_registration_and_guards_old_launcher(self):
        script = (builder.SOURCES / "Shigaraki.nsi").read_text()
        self.assertIn('Uninstall\\ShigarakiHaasPreview"', script)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\ShigarakiHaasPreview"', script)
        self.assertIn('WriteRegStr HKCU "${APP_KEY}" "DisplayName" "${NAME}"', script)
        self.assertIn('RefuseLockedFile "$INSTDIR\\信楽 Haasテスト.exe"', script)
        self.assertIn('Delete "$INSTDIR\\信楽 Haasテスト.exe"', script)
        copied = script.index('!include "payload-install.nsh"')
        old_removed = script.index('Delete "$INSTDIR\\信楽 Haasテスト.exe"')
        shortcuts = script.index("--shortcut")
        self.assertIn("AllowSkipFiles off", script[:copied])
        self.assertIn("ClearErrors", script[:copied])
        self.assertIn("${Errors}", script[copied:old_removed])
        self.assertIn("Abort", script[copied:old_removed])
        self.assertLess(copied, old_removed)
        self.assertLess(old_removed, shortcuts)
        self.assertIn("--remove-shortcuts", script)
        self.assertLess(script.index("--remove-shortcuts"), script.index('!include "payload-uninstall.nsh"'))
        self.assertNotIn('Delete "$DESKTOP\\信楽.lnk"', script)
        self.assertNotIn('Programs\\Shigaraki"', script)

    def test_old_launcher_is_rejected_as_an_input_but_owned_upgrade_cleanup_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / "payload"; build = root / "build"
            payload.mkdir(); build.mkdir()
            (payload / "信楽 Haasテスト.exe").write_bytes(b"old launcher")
            with self.assertRaises(ValueError):
                builder.payload_includes(payload, build)
            (payload / "信楽 Haasテスト.exe").unlink()
            (payload / "信楽.exe").write_bytes(b"new launcher")
            builder.payload_includes(payload, build)
            uninstall = (build / "payload-uninstall.nsh").read_text()
            self.assertIn('DeleteOwned "$INSTDIR\\信楽 Haasテスト.exe"', uninstall)
            self.assertIn('DeleteOwned "$INSTDIR\\信楽.exe"', uninstall)
            self.assertNotIn("Programs\\Shigaraki\\", uninstall)

    def test_uninstall_continues_owned_file_removal_when_shortcut_helper_fails(self):
        script = (builder.SOURCES / "Shigaraki.nsi").read_text()
        section = script.split('Section "Uninstall"', 1)[1]
        helper = section.index("--remove-shortcuts")
        failure = section.index('${If} $0 != "0"', helper)
        end = section.index('${EndIf}', failure)
        warning = section[failure:end]
        self.assertIn('MessageBox MB_OK|MB_ICONEXCLAMATION "${SHORTCUT_CLEANUP_FAILED}"', warning)
        for stop in ("Quit", "Abort", "Return", "SetErrorLevel"):
            self.assertNotIn(stop, warning)
        self.assertLess(end, section.index('!include "payload-uninstall.nsh"'))
        for location in ("$DESKTOP", "$SMPROGRAMS"):
            self.assertNotIn(f'Delete "{location}', section)

    def test_installer_rejects_paths_that_escape_payload_or_inject_nsis(self):
        for filename in ("../outside", "C:/outside", "/outside", "\\outside", "a\nFile x", 'a"b', "foo:stream", "*.dll"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                builder.checked_relative(filename)

    def test_uninstall_lists_owned_files_without_recursive_directory_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / "payload"; build = root / "build"
            (payload / "runtime").mkdir(parents=True); build.mkdir()
            (payload / "信楽.exe").write_bytes(b"exe")
            (payload / "runtime" / "some$file.dll").write_bytes(b"dll")
            files = builder.payload_includes(payload, build)
            self.assertEqual(set(files), {"信楽.exe", "runtime\\some$file.dll"})
            uninstall = (build / "payload-uninstall.nsh").read_text()
            self.assertIn('DeleteOwned "$INSTDIR\\runtime\\some$$file.dll"', uninstall)
            self.assertNotIn("RMDir /r", uninstall)
            self.assertNotIn("$LOCALAPPDATA\\Shigaraki", uninstall)
            self.assertNotIn("recognition.jsonl", uninstall)

    def test_symlinks_are_not_packaged_as_owned_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / "payload"; build = root / "build"
            payload.mkdir(); build.mkdir(); (root / "outside").write_text("private")
            (payload / "link").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                builder.payload_includes(payload, build)


class ShortcutOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.destination = root / "Programs/ShigarakiHaasPreview"
        self.destination.mkdir(parents=True)
        self.launcher = self.destination / "信楽.exe"
        self.launcher.write_bytes(b"fixture launcher")
        self.shell = ShellFixture(root)
        self.com = MagicMock()
        self.com.client.CreateObject.return_value = self.shell
        patcher = patch.dict(sys.modules, {"comtypes": self.com, "comtypes.client": self.com.client})
        patcher.start(); self.addCleanup(patcher.stop)
        self.start = self.shell.menu / "信楽/信楽.lnk"
        self.desktop = self.shell.desktop / "信楽.lnk"

    def test_new_shortcuts_use_owned_group_native_exe_and_icon(self):
        self.assertTrue(create_shortcut(self.destination))
        for path in (self.start, self.desktop):
            shortcut = self.shell.links[path]
            self.assertTrue(path.is_file())
            self.assertEqual(shortcut.TargetPath, str(self.launcher))
            self.assertEqual(shortcut.Arguments, "")
            self.assertEqual(shortcut.IconLocation, str(self.launcher) + ",0")
            self.assertEqual(shortcut.saves, 1)
        self.com.CoUninitialize.assert_called_once()

    def test_foreign_desktop_and_old_standard_flat_start_menu_link_are_preserved(self):
        old_standard = self.destination.parent / "Shigaraki/信楽.exe"
        desktop = self.shell.existing(self.desktop, old_standard)
        flat = self.shell.menu / "信楽.lnk"
        standard = self.shell.existing(flat, old_standard)
        self.assertFalse(create_shortcut(self.destination))
        self.assertEqual(self.desktop.read_text(), "existing shortcut")
        self.assertEqual(flat.read_text(), "existing shortcut")
        self.assertEqual(desktop.saves, 0)
        self.assertEqual(standard.saves, 0)
        self.assertEqual(self.shell.links[self.start].TargetPath, str(self.launcher))

    def test_own_desktop_is_updated_and_only_owned_old_haas_link_is_removed(self):
        old = self.destination / "信楽 Haasテスト.exe"
        desktop = self.shell.existing(self.desktop, old)
        owned = self.shell.menu / "信楽 Haasテスト.lnk"
        foreign = self.shell.desktop / "信楽 Haasテスト.lnk"
        self.shell.existing(owned, old)
        self.shell.existing(foreign, self.destination.parent / "other.exe")
        self.assertTrue(create_shortcut(self.destination))
        self.assertEqual(desktop.TargetPath, str(self.launcher))
        self.assertEqual(desktop.saves, 1)
        self.assertFalse(owned.exists())
        self.assertEqual(foreign.read_text(), "existing shortcut")

    def test_group_collision_fails_before_editing_any_shortcut(self):
        shortcut = self.shell.existing(self.start, self.destination.parent / "other.exe")
        with self.assertRaises(FileExistsError):
            create_shortcut(self.destination)
        self.assertEqual(self.start.read_text(), "existing shortcut")
        self.assertEqual(shortcut.saves, 0)
        self.assertFalse(self.desktop.exists())

    def test_uninstall_removes_only_own_current_and_old_links(self):
        self.shell.existing(self.start, self.launcher)
        foreign = self.shell.existing(self.desktop, self.destination.parent / "Shigaraki/信楽.exe")
        owned_old = self.shell.menu / "信楽 Haasテスト.lnk"
        foreign_old = self.shell.desktop / "信楽 Haasテスト.lnk"
        self.shell.existing(owned_old, self.destination / "runtime/pythonw.exe")
        self.shell.existing(foreign_old, self.destination.parent / "other.exe")
        unrelated = self.start.parent / "user-note.txt"
        unrelated.write_text("keep")
        remove_shortcuts(self.destination)
        self.assertFalse(self.start.exists())
        self.assertFalse(owned_old.exists())
        self.assertEqual(self.desktop.read_text(), "existing shortcut")
        self.assertEqual(foreign_old.read_text(), "existing shortcut")
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertEqual(foreign.saves, 0)

    def test_shortcut_cli_reports_desktop_conflict_and_removes_owned_links(self):
        self.shell.existing(self.desktop, self.destination.parent / "Shigaraki/信楽.exe")
        with patch.object(sys, "argv", ["install.py", "--shortcut", str(self.destination)]):
            self.assertEqual(install_portable.main(), 2)
        self.assertTrue(self.start.exists())
        with patch.object(sys, "argv", ["install.py", "--remove-shortcuts", str(self.destination)]):
            self.assertEqual(install_portable.main(), 0)
        self.assertFalse(self.start.exists())
        self.assertEqual(self.desktop.read_text(), "existing shortcut")


if __name__ == "__main__":
    unittest.main()
