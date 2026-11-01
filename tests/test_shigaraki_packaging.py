import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.install_portable import install, create_shortcut, copy_payload

spec = importlib.util.spec_from_file_location("installer_builder", Path(__file__).resolve().parents[1] / "scripts/build_shigaraki_installer.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackagingTests(unittest.TestCase):
    def test_preview_copy_installs_speech_and_leaves_standard_app_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source"
            (source / "speech").mkdir(parents=True)
            (source / "speech" / "shigaraki-haas-flow.exe").write_bytes(b"haas Rust CPU worker")
            (source / "信楽 Haasテスト.exe").write_bytes(b"preview launcher")
            standard = root / "Programs" / "Shigaraki"
            standard.mkdir(parents=True)
            (standard / "信楽.exe").write_bytes(b"standard sentinel")
            destination = root / "Programs" / "ShigarakiHaasPreview"
            copy_payload(source, destination)
            self.assertEqual((destination / "speech" / "shigaraki-haas-flow.exe").read_bytes(), b"haas Rust CPU worker")
            self.assertEqual((destination / "信楽 Haasテスト.exe").read_bytes(), b"preview launcher")
            self.assertEqual((standard / "信楽.exe").read_bytes(), b"standard sentinel")
            self.assertEqual(list(standard.iterdir()), [standard / "信楽.exe"])

    def test_installed_native_launcher_is_used_after_models_are_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "信楽 Haasテスト.exe").write_bytes(b"fixture")
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.setup_models.prepare_both"), patch("shigaraki_win.install_portable.verify_installed_models"), patch("shigaraki_win.install_portable.subprocess.run"), patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                install(root, root, lambda *_: None, threading.Event(), root / "log")
            self.assertEqual(launch.call_args.args[0], [str(root / "信楽 Haasテスト.exe"), "--installed"])

    def test_start_menu_uses_native_exe_and_its_icon_without_script_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "信楽 Haasテスト.exe").write_bytes(b"fixture")
            com = MagicMock()
            shortcut = com.client.CreateObject.return_value.CreateShortCut.return_value
            with patch.dict(sys.modules, {"comtypes": com, "comtypes.client": com.client}), patch.dict(os.environ, {"APPDATA": folder}):
                create_shortcut(root)
            self.assertEqual(shortcut.TargetPath, str(root / "信楽 Haasテスト.exe"))
            self.assertEqual(shortcut.Arguments, "")
            self.assertEqual(shortcut.IconLocation, str(root / "信楽 Haasテスト.exe") + ",0")
            shortcut.Save.assert_called_once()

    def test_installer_rejects_paths_that_escape_payload_or_inject_nsis(self):
        for filename in ("../outside", "C:/outside", "/outside", "\\outside", "a\nFile x", 'a"b', "foo:stream", "*.dll"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                builder.checked_relative(filename)

    def test_uninstall_lists_owned_files_without_recursive_directory_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = root / "payload"; build = root / "build"
            (payload / "runtime").mkdir(parents=True); build.mkdir()
            (payload / "信楽 Haasテスト.exe").write_bytes(b"exe")
            (payload / "runtime" / "some$file.dll").write_bytes(b"dll")
            files = builder.payload_includes(payload, build)
            self.assertEqual(set(files), {"信楽 Haasテスト.exe", "runtime\\some$file.dll"})
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


if __name__ == "__main__":
    unittest.main()
