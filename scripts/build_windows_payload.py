"""Assemble a Windows Haas payload from local, verified release inputs only.

No downloads, model inference, installation, or user-data access are performed.
Pass the result to build_shigaraki_installer.py to add its native launcher/NSIS.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from shigaraki_win import __version__, engine
from shigaraki_win import proofread_runtime as proof
from shigaraki_win.punctuation import MODEL_SHA, TOKENIZER_SHA

PYTHON_VERSION = "3.14.8"
PYTHON_SHA256 = "80292f0e640e373a54bf09f3b94a1472f976f24a10c5bed0d9622e4e44d67447"
PUNCTUATION_FILES = (("punct.int8.onnx", 37174608, MODEL_SHA),
                     ("tokenizer.json", 6718593, TOKENIZER_SHA))
ROOT_FILES = ("LICENSE", "README.md", "CREDITS.md", "THIRD_PARTY_NOTICES.md",
              "CONTRIBUTING.md", "requirements.txt", "requirements-release.txt")
IGNORED = {"__pycache__", ".DS_Store", "direct_url.json"}
PRIVATE_NAMES = {"settings.json", "paste-diagnostics.json", "recognition.jsonl",
                 "startup-error.txt", "install-error.txt", "model-check-error.txt"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative_name(value):
    windows = PureWindowsPath(value)
    if (not value or "\\" in value or windows.drive or windows.root
            or any(p in (".", "..") for p in value.rstrip("/").split("/"))
            or any(ord(c) < 32 or c in ':*?"<>|' for c in value)):
        raise ValueError("unsafe release path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("absolute release path")
    for part in path.parts:
        if (part.endswith((".", " ")) or part.split(".", 1)[0].upper() in
                {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError("Windows-reserved release path")
    return path.as_posix()


def tree_files(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("release input must be a regular directory")
    seen = set()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("release inputs cannot contain links")
        relative = relative_name(path.relative_to(directory).as_posix())
        if any(part in IGNORED for part in path.relative_to(directory).parts) or path.suffix == ".pyc":
            continue
        if relative.casefold() in seen:
            raise ValueError("ambiguous release input")
        seen.add(relative.casefold())
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("non-file release input")
        if path.name in PRIVATE_NAMES or path.suffix in (".log", ".jsonl"):
            raise ValueError("user settings or logs cannot be release inputs")
        yield relative, path


def runtime_members(archive):
    members, seen = [], set()
    for info in archive.infolist():
        name = relative_name(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.flag_bits & 1:
            raise ValueError("linked or encrypted Python runtime member")
        if name.casefold() in seen:
            raise ValueError("ambiguous Python runtime member")
        seen.add(name.casefold())
        if not info.is_dir():
            members.append((info, name))
    required = {"python.exe", "pythonw.exe", "python314.dll", "python314.zip", "LICENSE.txt"}
    if not required.issubset(name for _, name in members):
        raise ValueError("incomplete Python embedded runtime")
    return members


def package_versions(files):
    versions = {}
    for relative, path in files:
        if path.name != "METADATA" or not path.parent.name.endswith(".dist-info"):
            continue
        metadata = Parser().parsestr(path.read_text(encoding="utf-8"), headersonly=True)
        name, version = metadata.get("Name"), metadata.get("Version")
        if not name or not version:
            raise ValueError("package metadata lacks name/version")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in versions:
            raise ValueError("duplicate package distribution")
        versions[canonical] = version
    for line in (PROJECT / "requirements-release.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line.strip())
        if not match:
            raise ValueError("release requirements must be exact, unmarked versions")
        name, expected = match.groups()
        if versions.get(re.sub(r"[-_.]+", "-", name).lower()) != expected:
            raise ValueError("release dependency version mismatch: " + name)
    return versions


def public_sources():
    entries = []
    for name in ROOT_FILES:
        path = PROJECT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing public source/document: " + name)
        entries.append((name, path))
    for directory in ("shigaraki_win", "windows_voice_typer", "scripts", "installers", "tests", "docs", "third_party"):
        for relative, path in tree_files(PROJECT / directory):
            if directory == "shigaraki_win" and not (path.suffix == ".py" or path.name in
                    {"app.ico", "copy.json", "setup-copy.json", "guide.json"}):
                continue
            entries.append((directory + "/" + relative, path))
    rust = PROJECT / "rust" / "haas-flow"
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "README.md"):
        path = rust / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing Rust source")
        entries.append(("rust/haas-flow/" + name, path))
    for relative, path in tree_files(rust / "src"):
        if path.suffix == ".rs":
            entries.append(("rust/haas-flow/src/" + relative, path))
    return entries


def copy_files(files, destination):
    for relative, source in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)


def write_manual(output):
    """Render approved guide text as a self-contained, offline HTML manual."""
    guide_path = output / "shigaraki_win/guide.json"
    if not guide_path.is_file():
        return
    import base64
    from html import escape
    guide = json.loads(guide_path.read_text(encoding="utf-8"))
    content = ["<!doctype html><html lang='ja'><meta charset='utf-8'>",
               "<meta name='viewport' content='width=device-width,initial-scale=1'>",
               "<style>body{font:18px/1.9 system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 24px}img{max-width:100%;height:auto}li{margin:12px 0}</style>",
               "<title>" + escape(guide["title"]) + "</title><h1>" + escape(guide["title"]) + "</h1>"]
    for key in ("intro", "status"):
        if guide.get(key):
            content.append("<p>" + escape(guide[key]) + "</p>")
    for key, values in guide.items():
        if isinstance(values, list):
            content.append("<h2>" + escape(guide.get(key + "_title", key)) + "</h2><ol>")
            content.extend("<li>" + escape(value) + "</li>" for value in values)
            content.append("</ol>")
    content.append("<p>" + escape(guide.get("preview_note", "")) + "</p>")
    for name in ("main", "caption", "settings", "setup"):
        path = output / "docs/images" / (name + ".png")
        if path.is_file():
            content.append("<img alt='" + name + "' src='data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii") + "'>")
    (output / "はじめに.html").write_text("\n".join(content) + "</html>\n", encoding="utf-8")


def build(output, dependencies, python_zip, helper, punctuation, proofreader=None):
    if output.exists() or output.is_symlink():
        raise FileExistsError("output exists; choose a new payload directory")
    if python_zip.is_symlink() or digest(python_zip) != PYTHON_SHA256:
        raise ValueError("Python runtime checksum mismatch")
    if helper.is_symlink() or not engine.valid_file(helper, engine.WINDOWS_WORKER_BYTES, engine.WINDOWS_WORKER_SHA256):
        raise ValueError("Haas worker differs from engine.py; use a verified build and explicitly update its pin")
    for name, size, sha in PUNCTUATION_FILES:
        path = punctuation / name
        if path.is_symlink() or path.stat().st_size != size or digest(path) != sha:
            raise ValueError("punctuation asset checksum/size mismatch")
    inputs = [dependencies, punctuation, *([proofreader] if proofreader is not None else [])]
    resolved = output.resolve()
    if any(resolved.is_relative_to(path.resolve()) for path in inputs):
        raise ValueError("output must be outside release input directories")
    dependency_files = list(tree_files(dependencies))
    versions = package_versions(dependency_files)
    source_files = public_sources()
    proof_files = []
    if proofreader is not None:
        proof_files = [(relative, path) for relative, path in tree_files(proofreader)
                       if "/" not in relative and (relative == "llama-server.exe" or path.suffix.lower() == ".dll")]
        if not any(name == "llama-server.exe" for name, _ in proof_files):
            raise ValueError("proofreader input lacks llama-server.exe")
    with zipfile.ZipFile(python_zip) as archive:
        members = runtime_members(archive)  # Preflight all paths before writing.
        output.mkdir(parents=True)
        runtime = output / "runtime"
        for info, relative in members:
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
    (runtime / "python314._pth").write_text("python314.zip\n.\n..\nsite-packages\nimport site\n", encoding="utf-8")
    copy_files(dependency_files, runtime / "site-packages")
    copy_files(source_files, output)
    copy_files([("shigaraki-haas-flow.exe", helper)], output / "speech")
    copy_files([(name, punctuation / name) for name, _, _ in PUNCTUATION_FILES], output / "punctuation")
    copy_files([("MODEL_LICENSE.txt", PROJECT / "third_party/punctuation-MODEL_LICENSE.txt"),
                ("NOTICE.md", PROJECT / "third_party/punctuation-NOTICE.md"),
                ("BASE_MODEL_LICENSE.txt", PROJECT / "third_party/SB-Intuitions-modernbert-MIT.txt")], output / "punctuation")
    if proofreader is not None:
        copy_files(proof_files, output / "proofreader")
        copy_files([(name, PROJECT / "third_party" / name) for name in
                    ("llama.cpp-MIT.txt", "Qwen3.5-Apache-2.0.txt", "LLVM-OpenMP.txt")], output / "proofreader")
    shutil.copy2(PROJECT / "shigaraki_win/launch_portable.py", output / "launch.py")
    shutil.copy2(PROJECT / "shigaraki_win/install_portable.py", output / "install.py")
    (output / "信楽を起動.cmd").write_bytes(b'@echo off\r\nstart "" "%~dp0runtime\\pythonw.exe" "%~dp0launch.py" run\r\n')
    provenance = {
        "edition": "ShigarakiHaasPreview", "version": __version__,
        "python": {"version": PYTHON_VERSION, "embedded_zip_sha256": PYTHON_SHA256},
        "python_packages": versions,
        "native_worker": {"filename": "shigaraki-haas-flow.exe", "bytes": engine.WINDOWS_WORKER_BYTES,
                          "sha256": engine.WINDOWS_WORKER_SHA256, "backend": "CPU static speech.cpp + Rust"},
        "model": {"filename": engine.MODEL_NAME, "bytes": engine.MODEL_BYTES, "revision": engine.MODEL_REVISION,
                  "sha256": engine.MODEL_SHA256, "url": engine.MODEL_URL, "included": False},
        "punctuation_model": {"revision": "226c5d4a461ed368264881fc3e6637cbeb68055e",
                              "files": [{"filename": name, "bytes": size, "sha256": sha} for name, size, sha in PUNCTUATION_FILES]},
        "proofreading_model": {"filename": proof.MODEL_NAME, "bytes": proof.MODEL_BYTES,
                               "sha256": proof.MODEL_SHA256, "revision": proof.MODEL_REVISION, "included": False},
        "proofreading_runtime": {"included": proofreader is not None, "source": "local release input",
                                 "files": [name for name, _ in proof_files]},
        "windows_execution_tested": False}
    (output / "upstream.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_manual(output)
    files = [{"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)}
             for relative, path in tree_files(output)]
    manifest = {"stage": "before-native-launcher-and-nsis", "files": files,
                "windows_execution_tested": False}
    (output / "payload-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"payload_files": len(files) + 1, "worker_sha256": engine.WINDOWS_WORKER_SHA256,
            "proofreader_included": proofreader is not None, "windows_execution_tested": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--python-zip", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, required=True, help="merged Windows site-packages, including punctuation dependencies")
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--punctuation", type=Path, required=True)
    parser.add_argument("--proofreader", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, args.dependencies, args.python_zip, args.helper, args.punctuation, args.proofreader), indent=2))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"payload assembly failed: {error}\n")
