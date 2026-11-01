"""Fetch pinned local punctuation assets and an optional Windows CPU proofreader."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUNCT_REV = "226c5d4a461ed368264881fc3e6637cbeb68055e"
PUNCT_BASE = (
    f"https://huggingface.co/oboroge0/hayamimi-punct-ja-4class/resolve/{PUNCT_REV}/"
)
ASSETS = (
    (
        "punct.int8.onnx",
        "quantized_ort/punct_4class.int8.onnx",
        37174608,
        "8295f79d0e6c9b2ea8dd2276813a6e5fe121799aaf9a80def45456f28be41461",
    ),
    (
        "tokenizer.json",
        "hf/tokenizer.json",
        6718593,
        "368d21a3ac020cf7795d4ebe1c7b66ab661fbded97c007250a828253905a803c",
    ),
)
PROOF_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b10566/llama-b10566-bin-win-cpu-x64.zip"
PROOF_SIZE = 18582008
PROOF_SHA = "594f822c829ea097e1f0528a2b5b9f9953dd00254b48c44438104ddfb87a1ab9"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url, path, size, sha):
    if not url.startswith("https://"):
        raise ValueError("HTTPS is required")
    if path.exists():
        if (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size == size
            and digest(path) == sha
        ):
            return path
        raise FileExistsError(
            f"Existing file differs; keep it and use a clean asset directory: {path.name}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".part")
    try:
        h = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(url, timeout=30) as source, temporary.open(
            "xb"
        ) as target:
            if not source.geturl().startswith("https://"):
                raise ValueError("Download redirected away from HTTPS")
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > size:
                    raise ValueError("Asset exceeds pinned size")
                target.write(block)
                h.update(block)
        if count != size or h.hexdigest() != sha:
            raise ValueError("Asset checksum mismatch")
        os.link(temporary, path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def install_proofreader(archive, directory):
    """Copy only inference binaries, keeping prior files and rejecting unsafe paths."""
    with zipfile.ZipFile(archive) as source:
        candidates = []
        for item in source.infolist():
            name = PurePosixPath(item.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or ":" in item.filename:
                raise ValueError("Unsafe runtime archive path")
            if item.is_dir():
                continue
            if name.name == "llama-server.exe" or name.suffix.lower() == ".dll":
                candidates.append((item, name.name))
        names = [name for _, name in candidates]
        if "llama-server.exe" not in names or len(set(names)) != len(names):
            raise ValueError("Incomplete or ambiguous runtime archive")
        # Preflight every existing file before writing any new binary.
        for item, name in candidates:
            path = directory / name
            if path.exists() and (
                path.is_symlink() or path.read_bytes() != source.read(item)
            ):
                raise FileExistsError("Existing proofreader differs: " + name)
        directory.mkdir(parents=True, exist_ok=True)
        for item, name in candidates:
            path = directory / name
            if not path.exists():
                with source.open(item) as src, path.open("xb") as dst:
                    shutil.copyfileobj(src, dst)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", type=Path, default=ROOT, help="development asset root"
    )
    parser.add_argument("--with-proofreader", action="store_true")
    args = parser.parse_args()
    target = args.directory.resolve()
    for local, remote, size, sha in ASSETS:
        fetch(PUNCT_BASE + remote, target / "punctuation" / local, size, sha)
        print("verified:", local)
    notices = {
        "punctuation-MODEL_LICENSE.txt": "MODEL_LICENSE.txt",
        "punctuation-NOTICE.md": "NOTICE.md",
        "SB-Intuitions-modernbert-MIT.txt": "BASE_MODEL_LICENSE.txt",
    }
    for source, dest in notices.items():
        path = target / "punctuation" / dest
        expected = (ROOT / "third_party" / source).read_bytes()
        if path.exists() and path.read_bytes() != expected:
            raise FileExistsError("Existing license differs: " + dest)
        if not path.exists():
            path.write_bytes(expected)
    if args.with_proofreader:
        with tempfile.TemporaryDirectory(prefix="shigaraki-proofreader-") as folder:
            archive = fetch(
                PROOF_URL, Path(folder) / "runtime.zip", PROOF_SIZE, PROOF_SHA
            )
            install_proofreader(archive, target / "proofreader")
        for name in ["llama.cpp-MIT.txt", "Qwen3.5-Apache-2.0.txt", "LLVM-OpenMP.txt"]:
            src = ROOT / "third_party" / name
            dst = target / "proofreader" / name
            if dst.exists() and dst.read_bytes() != src.read_bytes():
                raise FileExistsError("Existing runtime license differs: " + name)
            if not dst.exists():
                dst.write_bytes(src.read_bytes())
        print(
            "verified: llama.cpp b10566 CPU runtime (Qwen weights download when enabled)"
        )


if __name__ == "__main__":
    main()
