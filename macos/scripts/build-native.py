#!/usr/bin/env python3
"""Build the pinned arm64/macOS 26 CPU helper and speech.cpp library.

Requires macOS 26+, an arm64 host, the macOS 26+ SDK, Python 3.11+, CMake,
Make, patch, and Rust/Cargo 1.93.0. RUSTUP_TOOLCHAIN=1.93.0 works with the
standard rustup shims. Explicit --rustc/--cargo also support isolated toolchains.
No model weights, microphone, signing identity, or installed app are used.
Both --work and --output-root must be new, separate directories. The result
is directly usable as KOEFLOW_NATIVE_ROOT by macos/scripts/build.sh.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("shigaraki_source_builder", ROOT / "scripts/build_cpu_engine.py")
SHARED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHARED)
TARGET = "aarch64-apple-darwin"
DEPLOYMENT = "26.0"
OFF = ("NATIVE", "CPU_ALL_VARIANTS", "CCACHE", "LLAMAFILE", "METAL", "ACCELERATE",
       "OPENMP", "BLAS", "VULKAN", "CUDA", "HIP", "SYCL", "OPENCL", "WEBGPU", "RPC", "BACKEND_DL")


def fresh_paths(work, output):
    for path in (work, output):
        if path.exists() or path.is_symlink():
            raise FileExistsError("--work and --output-root must both be new directories")
    work, output = work.resolve(), output.resolve()
    if work == output or work.is_relative_to(output) or output.is_relative_to(work):
        raise ValueError("--work and --output-root must be separate, non-nested directories")
    return work, output


def compiler_environment(source):
    env = SHARED.compiler_environment(source)
    # Include both spellings when a checkout is reached through a directory alias.
    import shlex
    for key in ("CFLAGS", "CXXFLAGS"):
        flag = shlex.quote(f"-ffile-prefix-map={source.resolve()}=speech.cpp")
        env[key] = (env.get(key, "") + " " + flag).strip()
    env["MACOSX_DEPLOYMENT_TARGET"] = DEPLOYMENT
    return env


def native_options(source, native, sdk):
    return ["-S", str(source), "-B", str(native), "-G", "Unix Makefiles",
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_ARCHITECTURES=arm64",
            "-DCMAKE_OSX_DEPLOYMENT_TARGET=" + DEPLOYMENT, "-DCMAKE_OSX_SYSROOT=" + sdk,
            "-DBUILD_SHARED_LIBS=OFF", "-DGGML_STATIC=OFF", "-DGGML_CPU=ON",
            "-DGGML_CPU_REPACK=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_Git=TRUE",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", *["-DGGML_" + name + "=OFF" for name in OFF]]


def check_dependencies(text, library=False):
    dependencies = [line.strip().split(" (", 1)[0] for line in text.splitlines()[1:] if line.strip()]
    for name in dependencies:
        if library and name == "@rpath/libspeech.4.dylib":
            continue
        if not name.startswith(("/usr/lib/", "/System/Library/")):
            raise RuntimeError("a native binary imports a non-system library")
    if library and (not dependencies or dependencies[0] != "@rpath/libspeech.4.dylib"):
        raise RuntimeError("the speech library must use its self-contained @rpath install name")
    return dependencies


def copy_tree_files(source, target):
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("source/notice trees must not contain symlinks")
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as incoming, destination.open("xb") as output:
                shutil.copyfileobj(incoming, output)


def build(args):
    work, output = fresh_paths(args.work, args.output_root)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("this builder requires an Apple Silicon Mac")
    if int(platform.mac_ver()[0].split(".")[0]) < 26:
        raise RuntimeError("macOS 26 or later is required for native verification")
    programs = {name: SHARED.tool(name) for name in ("cmake", "make", "patch", "xcrun")}
    rustc, cargo = SHARED.tool(args.rustc), SHARED.tool(args.cargo)
    versions = {"rustc": subprocess.check_output([rustc, "--version"], text=True).strip(),
                "cargo": subprocess.check_output([cargo, "--version"], text=True).strip()}
    if not versions["rustc"].startswith("rustc 1.93.0 ") or not versions["cargo"].startswith("cargo 1.93.0 "):
        raise RuntimeError("Rust and Cargo 1.93.0 are required; use RUSTUP_TOOLCHAIN or explicit tool paths")
    target_lib = subprocess.check_output([rustc, "--print", "target-libdir", "--target", TARGET], text=True).strip()
    if not Path(target_lib).is_dir():
        raise RuntimeError("the aarch64-apple-darwin standard library for Rust 1.93.0 is missing")
    sdk_version = subprocess.check_output([programs["xcrun"], "--show-sdk-version"], text=True).strip()
    if int(sdk_version.split(".")[0]) < 26:
        raise RuntimeError("the selected Xcode/Command Line Tools must include a macOS 26+ SDK")
    sdk = subprocess.check_output([programs["xcrun"], "--show-sdk-path"], text=True).strip()
    for name in ("otool", "vtool", "nm", "lipo"):
        programs[name] = subprocess.check_output([programs["xcrun"], "--find", name], text=True).strip()
    for pin in SHARED.NATIVE_PATCHES:
        patch = ROOT / pin["file"]
        if patch.is_symlink() or SHARED.sha256(patch) != pin["sha256"]:
            raise ValueError("CPU abort patch checksum mismatch")
    if args.offline and args.archive_cache is None:
        raise ValueError("--offline requires --archive-cache with speech.tar.gz and ggml.tar.gz")
    work.mkdir(parents=True, exist_ok=False)
    (work / "downloads").mkdir(); (work / "logs").mkdir()
    commands = []

    def run(label, command, env=None):
        print(label, flush=True); commands.append({"stage": label, "argv": command})
        with (work / "logs" / (label + ".txt")).open("x", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        if completed.returncode:
            raise RuntimeError(label + " failed; inspect --work/logs")

    source = work / "source-speech"
    omitted = []
    for name, pin in SHARED.SOURCES.items():
        archive = work / "downloads" / (name + ".tar.gz")
        if args.archive_cache is not None:
            cached = args.archive_cache / archive.name
            if cached.is_symlink() or not cached.is_file() or SHARED.sha256(cached) != pin["sha256"]:
                raise ValueError("archive cache file is absent or has the wrong checksum")
            with cached.open("rb") as incoming, archive.open("xb") as target:
                shutil.copyfileobj(incoming, target)
        SHARED.download_verified(pin, archive)
        destination = source if name == "speech" else source / "ggml"
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()  # Only the empty submodule directory in our fresh extraction.
        omitted.extend(SHARED.extract_sources(archive, destination, pin["root"]))
    for index, pin in enumerate(SHARED.NATIVE_PATCHES):
        run(f"cpu-abort-patch-{index}", [programs["patch"], "--batch", "--forward", "--fuzz=0", "-p1",
            "--directory", str(source), "-i", str(ROOT / pin["file"])])
    native = work / "build-speech"
    run("configure", [programs["cmake"], *native_options(source, native, sdk)], compiler_environment(source))
    run("native-library", [programs["cmake"], "--build", str(native), "--target", "speech", "--parallel", str(args.jobs)])
    rust_source = work / "source-rust"; rust_source.mkdir()
    for name in ("Cargo.toml", "Cargo.lock", "build.rs", "README.md"):
        path = ROOT / "rust/haas-flow" / name
        if path.is_symlink():
            raise ValueError("Rust source files must not be symlinks")
        with path.open("rb") as incoming, (rust_source / name).open("xb") as target:
            shutil.copyfileobj(incoming, target)
    copy_tree_files(ROOT / "rust/haas-flow/src", rust_source / "src")
    env = dict(os.environ)
    cargo_home = Path(env.get("CARGO_HOME", str(work / "cargo-home"))).expanduser().resolve()
    env.update(RUSTC=rustc, CARGO_HOME=str(cargo_home), CARGO_TARGET_DIR=str(work / "rust-target"),
               MACOSX_DEPLOYMENT_TARGET=DEPLOYMENT,
               CARGO_ENCODED_RUSTFLAGS=SHARED.rust_encoded_flags(env, work, cargo_home))
    command = [cargo, "build", "--locked", "--release", "--jobs", str(args.jobs),
               "--target", TARGET, "--manifest-path", str(rust_source / "Cargo.toml")]
    if args.offline:
        command.append("--offline")
    run("rust-helper", command, env)
    helper = work / "rust-target" / TARGET / "release/shigaraki-haas-flow"
    library = native / "libspeech.4.dylib"
    dependencies = {}
    for path in (helper, library):
        arch = subprocess.check_output([programs["lipo"], "-archs", str(path)], text=True).strip()
        if arch != "arm64":
            raise RuntimeError("the native output is not arm64-only")
        load = subprocess.check_output([programs["vtool"], "-show-build", str(path)], text=True)
        if not re.search(r"\bminos\s+26\.0\b", load):
            raise RuntimeError("the native output has an unexpected minimum macOS version")
        imports = subprocess.check_output([programs["otool"], "-L", str(path)], text=True)
        dependencies[path.name] = check_dependencies(imports, library=path == library)
    api = ctypes.CDLL(str(library)); api.speech_version.restype = ctypes.c_char_p
    if api.speech_api_version_major() != 4 or api.speech_version() != b"0.10.0":
        raise RuntimeError("the native library does not implement speech.cpp 0.10.0 API 4")
    output.mkdir(parents=True, exist_ok=False)  # Recheck exclusively after the potentially long build.
    (output / "files").mkdir()
    for path in (helper, library):
        target = output / "files" / path.name
        with path.open("rb") as incoming, target.open("xb") as destination:
            shutil.copyfileobj(incoming, destination)
        target.chmod(0o755)
    licenses = output / "licenses"; licenses.mkdir()
    for path, name in ((source / "LICENSE", "speech.cpp-MIT.txt"),
                       (source / "ggml/LICENSE", "ggml-MIT.txt"), (ROOT / "LICENSE", "Shigaraki-MIT.txt")):
        with path.open("rb") as incoming, (licenses / name).open("xb") as target:
            shutil.copyfileobj(incoming, target)
    copy_tree_files(ROOT / "third_party/rust", licenses / "rust")
    manifest = {"format": 1, "layout": "paths relative to this native root", "arch": "arm64",
        "macos_deployment_target": DEPLOYMENT, "cpu_only": True, "pool_patch": False,
        "speech_version": "0.10.0", "speech_api_version": 4, "sources": SHARED.SOURCES,
        "patches": list(SHARED.NATIVE_PATCHES), "rust_version": versions["rustc"],
        "cargo_version": versions["cargo"], "sdk_version": sdk_version,
        "cpu_flags": {**{name: "OFF" for name in OFF}, "CPU": "ON", "CPU_REPACK": "ON", "STATIC": "OFF"},
        "files": {path.name: {"path": "files/" + path.name, "bytes": (output / "files" / path.name).stat().st_size,
            "sha256": SHARED.sha256(output / "files" / path.name), "system_dependencies": dependencies[path.name]}
            for path in (helper, library)},
        "licenses": [{"path": path.relative_to(output).as_posix(), "sha256": SHARED.sha256(path)}
            for path in sorted(licenses.rglob("*")) if path.is_file()],
        "rust_source_sha256": {path.relative_to(rust_source).as_posix(): SHARED.sha256(path)
            for path in sorted(rust_source.rglob("*")) if path.is_file()},
        "model_weights_used": False, "signing_performed": False,
        "upstream_metadata_symlinks_omitted": omitted}
    with (output / "native-manifest.json").open("x", encoding="utf-8") as destination:
        json.dump(manifest, destination, indent=2); destination.write("\n")
    (work / "build-result.json").write_text(json.dumps({"commands": commands, "manifest": manifest}, indent=2) + "\n")
    print(json.dumps({"files": manifest["files"], "model_weights_used": False, "signing_performed": False}), flush=True)
    return manifest


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--work", type=Path, required=True, help="new sources/build/log directory")
    result.add_argument("--output-root", type=Path, required=True, help="new native root consumed by macos/scripts/build.sh")
    result.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    result.add_argument("--cargo", default=os.environ.get("CARGO", "cargo"))
    result.add_argument("--rustc", default=os.environ.get("RUSTC", "rustc"))
    result.add_argument("--archive-cache", type=Path, help="read-only verified speech.tar.gz and ggml.tar.gz cache")
    result.add_argument("--offline", action="store_true", help="require archive cache and available locked Cargo crates; no network")
    return result


if __name__ == "__main__":
    cli = parser(); args = cli.parse_args()
    if not 1 <= args.jobs <= 32:
        cli.error("--jobs must be between 1 and 32")
    try:
        build(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        cli.exit(1, f"native build failed: {error}\n")
