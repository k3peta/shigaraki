import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "fetch_assets", Path(__file__).resolve().parents[1] / "scripts/fetch_assets.py"
)
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


class AssetTests(unittest.TestCase):
    def test_failed_download_never_replaces_existing_or_leaves_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "model"
            p.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                assets.fetch("https://example.com/model", p, 5, "bad")
            self.assertEqual(p.read_bytes(), b"original")
            p = Path(folder) / "new"
            response = io.BytesIO(b"oversized")
            response.geturl = lambda: "https://example.com/model"
            with patch.object(
                assets.urllib.request, "urlopen", return_value=response
            ), self.assertRaises(ValueError):
                assets.fetch("https://example.com/model", p, 2, "bad")
            self.assertFalse(p.exists())
            self.assertFalse(list(Path(folder).glob("*.part")))

    def test_runtime_traversal_and_duplicate_names_are_rejected_before_writes(self):
        for names in [
            ["../outside.dll", "llama-server.exe"],
            ["a/llama-server.exe", "b/llama-server.exe"],
        ]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / "runtime.zip"
                out = Path(folder) / "out"
                with zipfile.ZipFile(archive, "w") as z:
                    for name in names:
                        z.writestr(name, b"x")
                with self.assertRaises(ValueError):
                    assets.install_proofreader(archive, out)
                self.assertFalse(out.exists())

    def test_runtime_keeps_user_file_when_it_differs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / "out"
            out.mkdir()
            (out / "llama-server.exe").write_bytes(b"original")
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("llama-server.exe", b"new")
                z.writestr("ggml.dll", b"dll")
            with self.assertRaises(FileExistsError):
                assets.install_proofreader(archive, out)
            self.assertEqual((out / "llama-server.exe").read_bytes(), b"original")
            self.assertFalse((out / "ggml.dll").exists())


if __name__ == "__main__":
    unittest.main()
