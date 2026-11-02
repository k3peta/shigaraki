"""Build the Windows GNU CPU helper from pinned, verified upstream sources.

Requires Python 3.11+, CMake 3.20+, Make, Rust/Cargo 1.93.0 with the Windows GNU
standard library, and x86_64-w64-mingw32-{gcc,g++,windres,objdump}. The MinGW
toolchain must provide the UCRT/static archives used by rust/haas-flow/build.rs.
No model weights are downloaded or read. Existing build directories are refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import urllib.request
import uuid

PROJECT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-gnu"
RUST_VERSION = "1.93.0"
SOURCES = {
    "speech": {"commit": "06590811a90041f935a470ba0e2b85346d2fc041",
               "url": "https://codeload.github.com/nyosegawa/speech.cpp/tar.gz/06590811a90041f935a470ba0e2b85346d2fc041",
               "sha256": "6fbbb815cc0ec62f0c9f15212cf2a5425d9643ba413717fd116bb3627ef6f625",
               "root": "speech.cpp-06590811a90041f935a470ba0e2b85346d2fc041"},
    "ggml": {"commit": "700c9d743ed7cc583c0f1b6507afebb6c0e88bc0",
             "url": "https://codeload.github.com/nyosegawa/ggml/tar.gz/700c9d743ed7cc583c0f1b6507afebb6c0e88bc0",
             "sha256": "74be9deef0460be7f937b31b5ca1265ebee2782b34d7213c839213bb2f0f5815",
             "root": "ggml-700c9d743ed7cc583c0f1b6507afebb6c0e88bc0"},
}
NATIVE_PATCHES = ({
    "file": "third_party/speech-cpu-abort.patch",
    "sha256": "18d159426605695eb665a5a44323ac981860768aa38603108da4dcc3c509998a",
},)
OFF = ("VULKAN", "METAL", "ACCELERATE", "CUDA", "HIP", "SYCL", "OPENCL", "WEBGPU",
       "OPENMP", "BLAS", "NATIVE", "BACKEND_DL", "CPU_ALL_VARIANTS", "CCACHE",
       "AVX512", "AVX512_VBMI", "AVX512_VNNI", "AVX512_BF16", "AVX_VNNI",
       "AMX_TILE", "AMX_INT8", "AMX_BF16", "RPC")


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def tool(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"missing required tool: {name}")
    # Preserve rustup's argv[0]-based cargo/rustc dispatch through its symlinks.
    return str(Path(path).absolute())


def download_verified(pin, destination):
    if not destination.exists():
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
        try:
            count = 0
            with urllib.request.urlopen(pin["url"], timeout=30) as response, temporary.open("xb") as output:
                if not response.geturl().startswith("https://"):
                    raise ValueError("source download redirected away from HTTPS")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    count += len(block)
                    if count > 32 * 1024 * 1024:
                        raise ValueError("source archive is unexpectedly large")
                    output.write(block)
            if sha256(temporary) != pin["sha256"]:
                raise ValueError("source archive checksum mismatch")
            if destination.exists():
                raise FileExistsError(destination)
            temporary.rename(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(destination) != pin["sha256"]:
        raise ValueError("cached source archive checksum mismatch")


def extract_sources(archive, destination, expected_root):
    destination.mkdir(parents=True, exist_ok=False)
    skipped_links = []
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != expected_root:
                raise ValueError("unsafe source archive path")
            relative = Path(*name.parts[1:])
            if member.issym() or member.islnk():
                # Upstream's editor/agent metadata links are not needed to build.
                skipped_links.append(name.as_posix())
                continue
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError("unsupported source archive member")
    return skipped_links


def update_worker_pin(path, size, digest):
    original = path.read_text(encoding="utf-8")
    changed, sizes = re.subn(r"^WINDOWS_WORKER_BYTES\s*=\s*[0-9_]+$",
                            f"WINDOWS_WORKER_BYTES = {size:_}", original, flags=re.MULTILINE)
    changed, hashes = re.subn(r'^WINDOWS_WORKER_SHA256\s*=\s*"[0-9a-f]{64}"$',
                             f'WINDOWS_WORKER_SHA256 = "{digest}"', changed, flags=re.MULTILINE)
    if sizes != 1 or hashes != 1:
        raise ValueError("worker pin declarations were not found exactly once")
    path.write_text(changed, encoding="utf-8")


def compiler_environment(source):
    env = dict(os.environ)
    flag = shlex.quote(f"-ffile-prefix-map={source}=speech.cpp")
    for name in ("CFLAGS", "CXXFLAGS"):
        env[name] = (env.get(name, "") + " " + flag).strip()
    return env


def rust_encoded_flags(env, work, cargo_home):
    # Match Cargo's precedence and whitespace splitting for existing flags.
    # Encoded flags support arguments containing spaces; do not decode via shlex.
    if "CARGO_ENCODED_RUSTFLAGS" in env:
        value = env["CARGO_ENCODED_RUSTFLAGS"]
        flags = value.split("\x1f") if value else []
    else:
        flags = env.get("RUSTFLAGS", "").split()
    # More specific paths follow broad ones because the last matching map wins.
    flags.extend(f"--remap-path-prefix={path}={relative}" for path, relative in (
        (work, "build"), (PROJECT, "shigaraki"), (cargo_home, "cargo-home")))
    return "\x1f".join(flags)


def build(args):
    work, output = args.work.resolve(), args.output.resolve()
    if work.exists():
        raise FileExistsError("use a new --work directory for a clean build")
    if output.exists():
        raise FileExistsError("output already exists; choose a new --output path")
    programs = {name: tool(name) for name in ("cmake", "make", "patch", "x86_64-w64-mingw32-gcc",
        "x86_64-w64-mingw32-g++", "x86_64-w64-mingw32-windres", "x86_64-w64-mingw32-objdump")}
    rustc, cargo = tool(args.rustc), tool(args.cargo)
    rust_version = subprocess.check_output([rustc, "--version"], text=True).strip()
    cargo_version = subprocess.check_output([cargo, "--version"], text=True).strip()
    if not rust_version.startswith("rustc " + RUST_VERSION + " ") or not cargo_version.startswith("cargo " + RUST_VERSION + " "):
        raise RuntimeError("Rust and Cargo 1.93.0 are required")
    target_lib = subprocess.check_output([rustc, "--print", "target-libdir", "--target", TARGET], text=True).strip()
    if not Path(target_lib).is_dir():
        raise RuntimeError("install the x86_64-pc-windows-gnu Rust 1.93.0 standard library first")
    for library in ("libucrt.a", "libwinpthread.a", "libstdc++.a"):
        path = subprocess.check_output([programs["x86_64-w64-mingw32-g++"], "-print-file-name=" + library], text=True).strip()
        if not Path(path).is_file():
            raise RuntimeError(f"the MinGW/UCRT toolchain is missing {library}")
    work.mkdir(parents=True)
    (work / "downloads").mkdir()
    logs = work / "logs"; logs.mkdir()
    def run(label, command, env=None):
        print(label, flush=True)
        with (logs / (label + ".txt")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"{label} failed with code {result.returncode}; inspect --work/logs")
    source = work / "source"
    skipped = []
    for name, pin in SOURCES.items():
        print("download-verified-" + name, flush=True)
        archive = work / "downloads" / (name + ".tar.gz")
        download_verified(pin, archive)
        destination = source if name == "speech" else source / "ggml"
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()  # An empty ggml submodule directory in the tar.
        skipped.extend(extract_sources(archive, destination, pin["root"]))
    for index, pin in enumerate(NATIVE_PATCHES):
        patch = PROJECT / pin["file"]
        if patch.is_symlink() or sha256(patch) != pin["sha256"]:
            raise ValueError("native patch checksum mismatch")
        run(f"native-patch-{index}", [programs["patch"], "--batch", "--forward", "--fuzz=0",
            "-p1", "--directory", str(source), "-i", str(patch)])
    native = work / "native"
    toolchain = work / "mingw.cmake"
    toolchain.write_text('set(CMAKE_SYSTEM_NAME Windows)\nset(CMAKE_SYSTEM_PROCESSOR AMD64)\n' +
        '\n'.join(f'set({key} "{programs[value]}")' for key, value in (
            ("CMAKE_C_COMPILER", "x86_64-w64-mingw32-gcc"), ("CMAKE_CXX_COMPILER", "x86_64-w64-mingw32-g++"),
            ("CMAKE_RC_COMPILER", "x86_64-w64-mingw32-windres"))) +
        '\nset(CMAKE_EXE_LINKER_FLAGS_INIT "-static -static-libgcc -static-libstdc++ -Wl,--no-insert-timestamp")\n')
    options = ["-DGGML_" + name + "=OFF" for name in OFF]
    options += ["-DGGML_" + name + "=ON" for name in ("CPU", "STATIC", "AVX", "AVX2", "F16C", "FMA")]
    run("configure", [programs["cmake"], "-S", str(source), "-B", str(native), "-G", "Unix Makefiles",
        "-DCMAKE_TOOLCHAIN_FILE=" + str(toolchain), "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
        "-DCMAKE_DISABLE_FIND_PACKAGE_Git=TRUE", "-DSPEECH_INSTALLED_GGML=OFF", *options], compiler_environment(source))
    run("native-static", [programs["cmake"], "--build", str(native), "--target", "speech-static", "--parallel", str(args.jobs)])
    env = dict(os.environ)
    cargo_home = work / "cargo-home"
    env.update(RUSTC=rustc, SPEECH_STATIC_DIR=str(native), CARGO_HOME=str(cargo_home),
        CARGO_TARGET_DIR=str(work / "rust-target"), CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=programs["x86_64-w64-mingw32-gcc"],
        CARGO_ENCODED_RUSTFLAGS=rust_encoded_flags(env, work, cargo_home))
    run("rust-release", [cargo, "build", "--locked", "--release", "--manifest-path", str(PROJECT / "rust/haas-flow/Cargo.toml"), "--target", TARGET], env)
    executable = work / "rust-target" / TARGET / "release/shigaraki-haas-flow.exe"
    data = executable.read_bytes(); offset = struct.unpack_from("<I", data, 0x3c)[0]
    if data[:2] != b"MZ" or data[offset:offset+4] != b"PE\0\0" or struct.unpack_from("<H", data, offset+4)[0] != 0x8664:
        raise RuntimeError("the built helper is not a Windows x64 executable")
    imports = subprocess.check_output([programs["x86_64-w64-mingw32-objdump"], "-p", str(executable)], text=True)
    dlls = re.findall(r"DLL Name: (.+)", imports)
    allowed = {"advapi32.dll", "bcrypt.dll", "kernel32.dll", "msvcrt.dll", "ntdll.dll", "shell32.dll",
               "user32.dll", "userenv.dll", "ws2_32.dll", "ole32.dll", "ucrtbase.dll"}
    if any(name.lower() not in allowed and not name.lower().startswith("api-ms-win-") for name in dlls):
        raise RuntimeError("the helper unexpectedly imports a non-system DLL")
    output.parent.mkdir(parents=True, exist_ok=True)
    # The build can take minutes: another build may have populated output since
    # the initial check. Exclusive creation preserves that file atomically.
    with executable.open("rb") as source, output.open("xb") as target:
        shutil.copyfileobj(source, target)
    digest = sha256(output)
    if args.update_worker_pin:
        update_worker_pin(PROJECT / "shigaraki_win/engine.py", output.stat().st_size, digest)
    report = {"sources": SOURCES, "patches": list(NATIVE_PATCHES),
        "rust": rust_version, "cargo": cargo_version, "target": TARGET,
        "mingw": subprocess.check_output([programs["x86_64-w64-mingw32-g++"], "-dumpfullversion"], text=True).strip(),
        "helper_bytes": output.stat().st_size, "helper_sha256": digest, "system_dll_imports": dlls,
        "worker_pin_updated": args.update_worker_pin, "model_weights_used": False,
        "windows_execution_tested": False, "upstream_metadata_symlinks_omitted": skipped}
    (work / "build-result.json").write_text(json.dumps(report, indent=2) + "\n")
    if args.update_worker_pin:
        (PROJECT / "resources/cpu-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"bytes": report["helper_bytes"], "sha256": digest, "worker_pin_updated": args.update_worker_pin,
                      "windows_execution_tested": False}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="new directory for verified sources, caches and build logs")
    parser.add_argument("--output", type=Path, required=True, help="new destination for shigaraki-haas-flow.exe")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--rustc", default="rustc")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--update-worker-pin", action="store_true", help="explicitly update engine.py's Windows worker size/hash")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error("--jobs must be between 1 and 32")
    try:
        build(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"build failed: {error}\n")
