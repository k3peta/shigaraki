"""Build a small Windows GUI launcher and a per-user NSIS installer.

The supplied payload must already contain the verified Windows runtime/models.
No model conversion or inference backend replacement happens here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
SOURCES = PROJECT / "installers" / "windows"
APP_EXE = "信楽.exe"
LEGACY_APP_EXE = "信楽 Haasテスト.exe"


def nsis_string(value):
    return value.replace("$", "$$").replace('"', '$\\"').replace("\r", "").replace("\n", "$\\r$\\n")


def checked_relative(value):
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or path.root or any(p in ("..", ".") for p in path.parts):
        raise ValueError(f"unsafe payload path: {value}")
    if any(c in value for c in ('\n', '\r', '"', ':', '*', '?')):
        raise ValueError(f"unsafe payload filename: {value}")
    return str(path)


def payload_includes(payload, build):
    groups = defaultdict(list)
    files = []
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"payload symlink: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = checked_relative(path.relative_to(payload).as_posix())
        if relative == LEGACY_APP_EXE:
            raise ValueError("old Haas launcher in payload; assemble a clean payload")
        groups[str(PureWindowsPath(relative).parent)].append(relative)
        files.append(relative)
    # The a1 CPU CLI is no longer shipped by a2. It was an owned application
    # file, so the current uninstaller must also remove that upgrade leftover.
    install = []
    uninstall = ['!insertmacro DeleteOwned "$INSTDIR\\speech\\speech.exe"',
                 f'!insertmacro DeleteOwned "$INSTDIR\\{LEGACY_APP_EXE}"']
    for directory, paths in groups.items():
        suffix = "" if directory == "." else "\\" + nsis_string(directory)
        install.append(f'SetOutPath "$INSTDIR{suffix}"')
        for relative in paths:
            path = payload / Path(relative.replace("\\", "/"))
            install.append(f'File "{nsis_string(str(path))}"')
            uninstall.append(f'!insertmacro DeleteOwned "$INSTDIR\\{nsis_string(relative)}"')
    # Old Python runtimes can have generated caches. Restrict cleanup to these
    # generated extensions in directories represented in the owned payload.
    for directory in groups:
        suffix = "" if directory == "." else "\\" + nsis_string(directory)
        uninstall.extend([f'Delete "$INSTDIR{suffix}\\__pycache__\\*.pyc"',
                          f'RMDir "$INSTDIR{suffix}\\__pycache__"'])
    directories = {str(p) for f in files for p in PureWindowsPath(f).parents if str(p) != "."}
    for directory in sorted(directories, key=lambda s: (s.count("\\"), s), reverse=True):
        uninstall.append(f'RMDir "$INSTDIR\\{nsis_string(directory)}"')
    (build / "payload-install.nsh").write_text("\n".join(install) + "\n", encoding="utf-8")
    (build / "payload-uninstall.nsh").write_text("\n".join(uninstall) + "\n", encoding="utf-8")
    return files


def build_installer(payload, output, build, version):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:a(\d+))?", version)
    if not match:
        raise ValueError("invalid app version")
    numeric_version = tuple(int(v or "0") for v in match.groups())
    for program in ("x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-windres", "makensis"):
        if not shutil.which(program):
            raise RuntimeError(f"missing build tool: {program}")
    if output.exists():
        raise FileExistsError(output)
    build.mkdir(parents=True, exist_ok=True)
    copy = json.loads((SOURCES / "app-copy.json").read_text(encoding="utf-8"))
    icon = payload / "shigaraki_win" / "app.ico"
    if not icon.is_file():
        raise FileNotFoundError(icon)
    header = "\n".join(f'#define COPY_{key.upper()} L{json.dumps(copy[key], ensure_ascii=True)}'
                       for key in ("name", "missing", "failed", "other_running"))
    (build / "launcher-copy.h").write_text(header + "\n", encoding="utf-8")
    (build / "app.manifest").write_text('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
<requestedExecutionLevel level="asInvoker" uiAccess="false"/>
</requestedPrivileges></security></trustInfo>
</assembly>''', encoding="utf-8")
    rc = (f'1 ICON "{icon.as_posix()}"\n1 24 "{(build / "app.manifest").as_posix()}"\n'
          f'1 VERSIONINFO\n FILEVERSION {",".join(map(str,numeric_version))}\n PRODUCTVERSION {",".join(map(str,numeric_version))}\n'
          ' FILEOS 0x40004\n FILETYPE 1\n BEGIN\n BLOCK "StringFileInfo"\n BEGIN\n'
          ' BLOCK "041104b0"\n BEGIN\n VALUE "ProductName", "信楽\\0"\n'
          f' VALUE "FileVersion", "{version}\\0"\n VALUE "FileDescription", "信楽\\0"\n'
          f' VALUE "OriginalFilename", "{APP_EXE}\\0"\n END\n END\n'
          ' BLOCK "VarFileInfo"\n BEGIN\n VALUE "Translation", 0x411, 1200\n END\n END\n')
    (build / "launcher.rc").write_text(rc, encoding="utf-8")
    subprocess.run(["x86_64-w64-mingw32-windres", "--codepage=65001", str(build / "launcher.rc"),
                    "-O", "coff", "-o", str(build / "launcher-res.o")], check=True)
    launcher = payload / APP_EXE
    subprocess.run(["x86_64-w64-mingw32-gcc", "-std=c11", "-Os", "-Wall", "-Wextra", "-Werror",
                    "-municode", "-mwindows", "-static", "-s", "-I", str(build),
                    str(SOURCES / "shigaraki-launcher.c"), str(build / "launcher-res.o"),
                    "-Wl,--nxcompat,--dynamicbase,--high-entropy-va,--no-insert-timestamp",
                    "-o", str(launcher)], check=True)
    (build / "installer-copy.nsh").write_text("\n".join(
        f'!define {key.upper()} "{nsis_string(value)}"' for key, value in copy.items()
    ) + "\n", encoding="utf-8")
    hashes = []
    for path in sorted(payload.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt" and "__pycache__" not in path.parts and path.suffix != ".pyc":
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            hashes.append(digest + "  " + path.relative_to(payload).as_posix() + "\n")
    (payload / "SHA256SUMS.txt").write_text("".join(hashes), encoding="utf-8")
    files = payload_includes(payload, build)
    kilobytes = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file()) // 1024
    subprocess.run(["makensis", "-V2", "-INPUTCHARSET", "UTF8",
                    "-DOUTPUT_EXE=" + str(output), "-DAPP_VERSION=" + version,
                    "-DPRODUCT_VERSION=" + ".".join(map(str, numeric_version)),
                    "-DAPP_ICON=" + str(icon), "-DINSTALL_KB=" + str(kilobytes),
                    "-X!addincludedir " + str(build), str(SOURCES / "Shigaraki.nsi")], check=True)
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"file": str(output), "bytes": output.stat().st_size, "sha256": digest,
              "payload_files": len(files), "windows_execution_tested": False}
    (build / "build-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(build_installer(args.payload.resolve(), args.output.resolve(),
                                     args.build.resolve(), args.version), indent=2))
