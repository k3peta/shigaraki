"""Per-user installation with visible stages and cooperative cancellation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

PAYLOAD = ("runtime", "speech", "native", "proofreader", "punctuation", "punctuation_vendor", "shigaraki_win", "windows_voice_typer", "launch.py", "信楽.exe", "信楽を起動.cmd", "はじめに.html", "LICENSE", "upstream.json", "third_party")


def verify_installed_models(destination, progress, cancel, timeout=120):
    """Probe the installed runtime in isolation; native hangs stay cancellable."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen([str(destination / "runtime" / "python.exe"),
        str(destination / "launch.py"), "verify-models", "--allow-proof-policy-block", "--verify-punctuation"], cwd=destination,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=flags)
    ready = threading.Event()
    def read_progress():
        for raw in process.stdout:
            try:
                stage = json.loads(raw).get("stage")
            except (ValueError, AttributeError):
                continue
            if stage in ("check_asr", "check_proof", "check_punctuation"):
                progress(stage, 0, 0, "")
            elif stage == "models_ready":
                ready.set()
            elif stage == "asr_ready_proof_blocked":
                progress("proof_blocked", 0, 0, "")
                ready.set()
    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.wait(.05):
                raise InterruptedError("model check cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("model runtime check timed out")
        reader.join(timeout=1)
        if cancel.is_set():
            raise InterruptedError("model check cancelled")
        if process.returncode or not ready.is_set():
            raise RuntimeError("installed models did not both become ready")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        reader.join(timeout=1)
        process.stdout.close()


def copy_payload(source: Path, destination: Path, progress=lambda *_: None, cancel=None):
    if source.resolve() == destination.resolve():
        return
    files = []
    for name in PAYLOAD:
        item = source / name
        candidates = sorted(item.rglob("*")) if item.is_dir() else [item]
        files.extend(p for p in candidates if p.is_file() and not p.is_symlink())
    total = sum(p.stat().st_size for p in files)
    done = 0
    for item in files:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("installation cancelled")
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        done += item.stat().st_size
        progress(done, total, str(relative))


def _shortcut_paths(shell):
    menu = Path(shell.SpecialFolders("Programs"))
    desktop = Path(shell.SpecialFolders("Desktop"))
    return menu / "信楽" / "信楽.lnk", desktop / "信楽.lnk", (
        menu / "信楽 Haasテスト.lnk", desktop / "信楽 Haasテスト.lnk")


def _owned_shortcut(shell, path, destination):
    if not path.is_file():
        return False
    target = str(shell.CreateShortCut(str(path)).TargetPath)
    if not target:
        return False
    normalize = lambda value: os.path.normcase(os.path.abspath(os.path.expandvars(str(value))))
    return normalize(target) in {
        normalize(destination / name)
        for name in ("信楽.exe", "信楽 Haasテスト.exe", "runtime/pythonw.exe")}


def create_shortcut(destination):
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        start, desktop, legacy = _shortcut_paths(shell)
        if start.exists() and not _owned_shortcut(shell, start, destination):
            raise FileExistsError("start menu shortcut belongs to another application")
        start.parent.mkdir(parents=True, exist_ok=True)
        launcher = destination / "信楽.exe"
        def save(path):
            shortcut = shell.CreateShortCut(str(path))
            shortcut.TargetPath = str(launcher if launcher.is_file() else destination / "runtime" / "pythonw.exe")
            shortcut.Arguments = "" if launcher.is_file() else f'"{destination / "launch.py"}" run'
            shortcut.IconLocation = str(launcher if launcher.is_file() else destination / "shigaraki_win" / "app.ico") + ",0"
            shortcut.WorkingDirectory = str(destination)
            shortcut.Save()
        save(start)
        desktop_ok = not desktop.exists() or _owned_shortcut(shell, desktop, destination)
        if desktop_ok:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            save(desktop)
        for path in legacy:
            if _owned_shortcut(shell, path, destination):
                path.unlink()
        return desktop_ok
    finally:
        comtypes.CoUninitialize()


def remove_shortcuts(destination):
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        start, desktop, legacy = _shortcut_paths(shell)
        for path in (start, desktop, *legacy):
            if _owned_shortcut(shell, path, destination):
                path.unlink()
        try:
            start.parent.rmdir()  # Only this now-empty app group, never recursively.
        except OSError:
            pass
    finally:
        comtypes.CoUninitialize()


def install(source, destination, progress, cancel, log):
    progress("copy", 0, 0, "")
    copy_payload(source, destination, lambda done, total, name: progress("copy", done, total, name), cancel)
    if cancel.is_set():
        raise InterruptedError("installation cancelled")
    from shigaraki_win.setup_models import prepare_both
    prepare_both(progress, cancel)
    progress("check_asr", 0, 0, "")
    verify_installed_models(destination, progress, cancel)
    if cancel.is_set():
        raise InterruptedError("installation cancelled")
    progress("shortcut", 0, 0, "")
    shortcut_ok = True
    try:
        # Shell COM can stall. Isolate it so cancellation/timeout stays bounded.
        subprocess.run([sys.executable, str(source / "install.py"), "--shortcut", str(destination)],
                       timeout=15, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        shortcut_ok = False
        log.mkdir(parents=True, exist_ok=True)
        (log / "shortcut-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    if cancel.is_set():
        raise InterruptedError("installation cancelled")
    progress("launch", 0, 0, "")
    launcher = destination / "信楽.exe"
    command = ([str(launcher), "--installed"] if launcher.is_file() else
               [str(destination / "runtime" / "pythonw.exe"), str(destination / "launch.py"), "run", "--installed"])
    subprocess.Popen(command, cwd=destination)
    return shortcut_ok


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--shortcut":
        return 0 if create_shortcut(Path(sys.argv[2])) else 2
    if len(sys.argv) == 3 and sys.argv[1] == "--remove-shortcuts":
        remove_shortcuts(Path(sys.argv[2]))
        return 0
    source = Path(__file__).resolve().parent
    sys.path.insert(0, str(source / "punctuation_vendor"))
    site = source / "runtime" / "site-packages"
    handles = [os.add_dll_directory(str(p)) for p in [source / "runtime", site, site / "PySide6", site / "shiboken6"] if p.is_dir()]
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(site / "PySide6" / "plugins" / "platforms")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from shigaraki_win.ui import InstallWindow, COPY
    application = QApplication(["Shigaraki Haas setup"])
    from PySide6.QtGui import QIcon
    application.setWindowIcon(QIcon(str(source / "shigaraki_win" / "app.ico")))
    cancel = threading.Event()
    state = {"done": False, "stage": "copy", "bytes": 0, "total": 0,
             "file": "", "error": None, "shortcut_ok": True, "proof_blocked": False,
             "failed_stage": None}
    window = InstallWindow()
    window.cancel_install.connect(cancel.set)
    destination = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "ShigarakiHaasPreview"
    log = Path(os.environ["LOCALAPPDATA"]) / "ShigarakiHaasPreview"
    started = [time.monotonic()]
    last_log = [0.0, ""]
    def report(stage, done, total, name):
        if stage == "proof_blocked":
            state["proof_blocked"] = True
        state.update(stage=stage, bytes=done, total=total, file=name)
        now = time.monotonic()
        if now - last_log[0] > .5 or stage != last_log[1]:
            last_log[:] = [now, stage]
            log.mkdir(parents=True, exist_ok=True)
            (log / "install-progress.json").write_text(json.dumps({"stage": stage, "bytes": done, "total": total, "file": name, "elapsed_seconds": round(now-started[0], 1)}), encoding="utf-8")
    def work():
        try:
            state["shortcut_ok"] = install(source, destination, report, cancel, log)
            report("complete", 0, 0, "")
        except InterruptedError:
            state["stage"] = "cancelled"
        except Exception:
            state["failed_stage"] = state["stage"]
            state["stage"] = "failed"
            state["error"] = traceback.format_exc()
            log.mkdir(parents=True, exist_ok=True)
            (log / "install-error.txt").write_text(state["error"], encoding="utf-8")
        finally:
            state["done"] = True
    def poll():
        window.update_stage(state["stage"], state["bytes"], state["total"],
            time.monotonic()-started[0], finished=state["done"],
            cancelling=cancel.is_set(), shortcut_ok=state["shortcut_ok"],
            failed_stage=state["failed_stage"])
        if state["proof_blocked"] and state["stage"] == "complete":
            window.status.setText(COPY["install_complete_proof_blocked"])
    worker = [None]
    def begin():
        if worker[0] is not None and worker[0].is_alive():
            return
        cancel.clear()
        state.update(done=False, stage="copy", bytes=0, total=0, file="", error=None,
                     shortcut_ok=True, proof_blocked=False, failed_stage=None)
        started[0] = time.monotonic()
        poll()
        worker[0] = threading.Thread(target=work, daemon=True)
        worker[0].start()
    window.retry_install.connect(begin)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    window.show()
    begin()
    result = application.exec()
    timer.stop()
    import shiboken6
    shiboken6.delete(window)
    return result



if __name__ == "__main__":
    raise SystemExit(main())
