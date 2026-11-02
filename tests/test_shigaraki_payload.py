import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "payload_builder", Path(__file__).resolve().parents[1] / "scripts/build_windows_payload.py")
payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payload)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dependencies = self.root / "dependencies"
        self.dependencies.mkdir()
        for name, version in (("PySide6-Essentials", "6.12.0"), ("numpy", "2.5.4"),
                              ("sounddevice", "0.5.6"), ("comtypes", "1.4.17"),
                              ("onnxruntime", "1.31.1"), ("tokenizers", "0.23.3")):
            directory = self.dependencies / (name.replace("-", "_") + "-" + version + ".dist-info")
            directory.mkdir()
            (directory / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
            (directory / "LICENSE").write_text("upstream license fixture")
        self.python = self.root / "python.zip"
        with zipfile.ZipFile(self.python, "w") as archive:
            for name in ("python.exe", "pythonw.exe", "python314.dll", "python314.zip", "LICENSE.txt"):
                archive.writestr(name, b"runtime fixture")
        self.worker = self.root / "worker.exe"
        self.worker.write_bytes(b"pinned Haas fixture")
        self.punctuation = self.root / "punctuation"
        self.punctuation.mkdir()
        punc_files = []
        for name in ("punct.int8.onnx", "tokenizer.json"):
            path = self.punctuation / name
            path.write_bytes(b"punctuation fixture")
            punc_files.append((name, path.stat().st_size, payload.digest(path)))
        for target, name, value in ((payload, "PYTHON_SHA256", payload.digest(self.python)),
                                    (payload, "PUNCTUATION_FILES", tuple(punc_files)),
                                    (payload.engine, "WINDOWS_WORKER_BYTES", self.worker.stat().st_size),
                                    (payload.engine, "WINDOWS_WORKER_SHA256", payload.digest(self.worker))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output = self.root / "payload"

    def assemble(self, proofreader=None):
        return payload.build(self.output, self.dependencies, self.python,
                             self.worker, self.punctuation, proofreader)

    def test_haas_only_payload_preserves_sources_licenses_and_manifest(self):
        cache = self.dependencies / "__pycache__"
        cache.mkdir()
        (cache / "private.pyc").write_bytes(b"generated")
        (self.dependencies / "direct_url.json").write_text('{"url":"file:///private/build"}')
        result = self.assemble()
        self.assertFalse(result["proofreader_included"])
        self.assertFalse((self.output / "native").exists())
        self.assertFalse((self.output / "proofreader").exists())
        self.assertEqual([p.name for p in (self.output / "speech").iterdir()], ["shigaraki-haas-flow.exe"])
        self.assertFalse((self.output / "runtime/site-packages/__pycache__").exists())
        self.assertFalse((self.output / "runtime/site-packages/direct_url.json").exists())
        for name in ("CREDITS.md", "THIRD_PARTY_NOTICES.md", "docs/INSTALL.md",
                     "shigaraki_win/history.py", "shigaraki_win/history_ui.py", "shigaraki_win/history-copy.json",
                     "rust/haas-flow/src/main.rs", "third_party/rust/dependencies.json",
                     "punctuation/MODEL_LICENSE.txt", "punctuation/BASE_MODEL_LICENSE.txt"):
            self.assertTrue((self.output / name).is_file(), name)
        self.assertEqual((self.output / "runtime/python314._pth").read_text(),
                         "python314.zip\n.\n..\nsite-packages\nimport site\n")
        manifest = json.loads((self.output / "payload-manifest.json").read_text())
        self.assertEqual(manifest["stage"], "before-native-launcher-and-nsis")
        self.assertFalse(manifest["windows_execution_tested"])
        worker = next(entry for entry in manifest["files"] if entry["path"] == "speech/shigaraki-haas-flow.exe")
        self.assertEqual(worker["sha256"], hashlib.sha256(self.worker.read_bytes()).hexdigest())
        upstream = json.loads((self.output / "upstream.json").read_text())
        self.assertEqual(upstream["python_packages"]["numpy"], "2.5.4")
        self.assertFalse(upstream["model"]["included"])
        self.assertFalse(upstream["proofreading_model"]["included"])

    def test_optional_proofreader_copies_only_inference_binaries_and_notices(self):
        proof = self.root / "proof"
        proof.mkdir()
        for name in ("llama-server.exe", "ggml.dll", "llama-cli.exe", "unrelated.txt"):
            (proof / name).write_bytes(b"local runtime fixture")
        self.assertTrue(self.assemble(proof)["proofreader_included"])
        self.assertEqual({p.name for p in (self.output / "proofreader").iterdir()},
                         {"llama-server.exe", "ggml.dll", "llama.cpp-MIT.txt",
                          "Qwen3.5-Apache-2.0.txt", "LLVM-OpenMP.txt"})

    def test_existing_output_is_never_changed(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.assemble()
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep")

    def test_checksum_failures_leave_no_output(self):
        for path in (self.python, self.worker, self.punctuation / "punct.int8.onnx"):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    self.assemble()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_zip_paths_and_links_are_rejected_before_any_writes(self):
        original = self.python.read_bytes()
        for name in ("../outside", "/outside", "C:/outside", "C:relative", "a\\..\\outside", "NUL.txt"):
            with self.subTest(name=name):
                self.python.write_bytes(original)
                with zipfile.ZipFile(self.python, "a") as archive:
                    archive.writestr(name, b"bad")
                with patch.object(payload, "PYTHON_SHA256", payload.digest(self.python)), self.assertRaises(ValueError):
                    self.assemble()
                self.assertFalse(self.output.exists())
        archive_path = self.root / "link.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../private")
        with zipfile.ZipFile(archive_path) as archive, self.assertRaises(ValueError):
            payload.runtime_members(archive)

    def test_case_collisions_in_runtime_are_rejected(self):
        with zipfile.ZipFile(self.python, "a") as archive:
            archive.writestr("PYTHON.EXE", b"other")
        with patch.object(payload, "PYTHON_SHA256", payload.digest(self.python)), self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_wrong_release_package_version_is_rejected(self):
        metadata = self.dependencies / "numpy-2.5.4.dist-info/METADATA"
        metadata.write_text("Name: numpy\nVersion: 0.0.0\n")
        with self.assertRaisesRegex(ValueError, "numpy"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_links_and_user_logs_in_input_are_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("private")
        link = self.dependencies / "link"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())
        link.unlink()
        (self.dependencies / "paste-diagnostics.json").write_text("private")
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())
        self.assertEqual(outside.read_text(), "private")


if __name__ == "__main__":
    unittest.main()
