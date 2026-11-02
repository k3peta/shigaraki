import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("mac_native_builder", Path(__file__).resolve().parents[1] / "scripts/build-native.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class NativeBuilderTests(unittest.TestCase):
    def test_existing_outputs_and_broken_symlinks_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "native"; output.mkdir()
            sentinel = output / "keep"; sentinel.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                builder.fresh_paths(root / "work", output)
            link = root / "broken"; link.symlink_to(root / "absent")
            with self.assertRaises(FileExistsError):
                builder.fresh_paths(link, root / "new")
            self.assertEqual(sentinel.read_bytes(), b"original")
            self.assertTrue(link.is_symlink())

    def test_logs_and_sources_cannot_be_nested_in_native_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for work, output in [(root / "same", root / "same"), (root / "a", root / "a/out"), (root / "b/work", root / "b")]:
                with self.assertRaises(ValueError):
                    builder.fresh_paths(work, output)

    def test_only_system_dependencies_and_the_library_own_rpath_are_allowed(self):
        own = "/some/build/libspeech.4.dylib:\n\t@rpath/libspeech.4.dylib (compatibility version 4.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
        self.assertEqual(builder.check_dependencies(own, library=True)[0], "@rpath/libspeech.4.dylib")
        for dependency in ["/opt/homebrew/lib/libomp.dylib", "/private/build/dependency.dylib", "@rpath/libggml.dylib"]:
            with self.assertRaises(RuntimeError):
                builder.check_dependencies("helper:\n\t" + dependency + " (compatibility version 1.0.0)\n")
        with self.assertRaises(RuntimeError):
            builder.check_dependencies("library:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n", library=True)

    def test_cpu_arm64_deployment_recipe_has_no_accelerator_or_native_tuning(self):
        options = builder.native_options(Path("source"), Path("build"), "sdk")
        for value in ["-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=26.0", "-DGGML_CPU=ON", "-DGGML_CPU_REPACK=ON", "-DGGML_STATIC=OFF"]:
            self.assertIn(value, options)
        for key in ["METAL", "ACCELERATE", "OPENMP", "BLAS", "VULKAN", "NATIVE", "BACKEND_DL"]:
            self.assertIn("-DGGML_" + key + "=OFF", options)

    def archive(self, root, name, contents=b"fixture", kind=tarfile.REGTYPE):
        archive = root / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            entry = tarfile.TarInfo(name); entry.type = kind
            if kind == tarfile.REGTYPE:
                entry.size = len(contents); output.addfile(entry, io.BytesIO(contents))
            else:
                entry.linkname = "../../outside"; output.addfile(entry)
        return archive

    def test_pinned_source_extractor_rejects_traversal_before_writing_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root, "source/../outside")
            with self.assertRaises(ValueError):
                builder.SHARED.extract_sources(archive, root / "extracted", "source")
            self.assertFalse((root / "outside").exists())

    def test_upstream_metadata_links_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = self.archive(root, "source/link", kind=tarfile.SYMTYPE)
            omitted = builder.SHARED.extract_sources(archive, root / "extracted", "source")
            self.assertEqual(omitted, ["source/link"])
            self.assertEqual(list((root / "extracted").iterdir()), [])

    def test_bad_cached_download_is_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "speech.tar.gz"; archive.write_bytes(b"wrong archive")
            with self.assertRaises(ValueError):
                builder.SHARED.download_verified(builder.SHARED.SOURCES["speech"], archive)
            self.assertEqual(archive.read_bytes(), b"wrong archive")

    def test_source_and_notice_copy_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            (source / "link").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                builder.copy_tree_files(source, root / "copy")


if __name__ == "__main__":
    unittest.main()
