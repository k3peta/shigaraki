from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

from .engine import CpuEngine, default_model, prepare_model, read_wav


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shigaraki-haas-preview")
    command = parser.add_subparsers(dest="command", required=True)
    run = command.add_parser("run")
    run.add_argument("--setup", action="store_true")
    run.add_argument("--installed", action="store_true")
    verify = command.add_parser("verify-models")
    verify.add_argument("--asr-library", "--asr-executable", dest="asr_library", type=Path)
    verify.add_argument("--proof-executable", type=Path)
    verify.add_argument("--allow-proof-policy-block", action="store_true")
    verify.add_argument("--verify-punctuation", action="store_true")
    prepare = command.add_parser("prepare-model")
    prepare.add_argument("--model", type=Path, default=default_model())
    bench = command.add_parser("benchmark")
    bench.add_argument("wav", type=Path)
    bench.add_argument("--model", type=Path, default=default_model())
    bench.add_argument("--repeat", type=int, default=2)
    bench.add_argument("--include-text", action="store_true")
    bench.add_argument("--library", "--executable", dest="library", type=Path)
    preview = command.add_parser("render-preview")
    punctbench = command.add_parser("benchmark-punctuation")
    punctbench.add_argument("--repeat", type=int, default=10)
    punctbench.add_argument("--output", type=Path)
    preview.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "benchmark-punctuation":
        import statistics
        from .punctuation import PunctuationRuntime
        from . import __version__
        model = PunctuationRuntime()
        began = time.perf_counter()
        model.load()
        load_seconds = time.perf_counter()-began
        seed = "明日は資料を確認して参加者に伝えてもらえますか参加費は1000円で集合時刻は15時です予定が変わった場合は事前に連絡してください"
        rows = []
        try:
            for size in (200,400,800):
                text = (seed*20)[-size:]
                model.restore(text)
                runs = []
                for _ in range(max(3,min(30,args.repeat))):
                    began = time.perf_counter()
                    model.restore(text)
                    runs.append(time.perf_counter()-began)
                rows.append({"characters":size,"median_seconds":statistics.median(runs),"max_seconds":max(runs),"runs":runs})
        finally:
            model.close()
        result = {"version":__version__,"platform":platform.platform(),"processor":platform.processor(),
                  "threads":2,"gpu":False,"windows_executed":sys.platform=="win32",
                  "synthetic_inputs_only":True,"includes_audio_or_paste":False,
                  "load_seconds":load_seconds,"measurements":rows}
        rendered = json.dumps(result,ensure_ascii=False,indent=2)
        if args.output:
            args.output.write_text(rendered,encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "verify-models":
        from .setup_models import verify_both
        verify_both(lambda stage: print(json.dumps({"stage": stage}), flush=True),
                    asr_library=args.asr_library, proof_executable=args.proof_executable,
                    allow_policy_block=args.allow_proof_policy_block,
                    require_punctuation=args.verify_punctuation)
        return 0
    if args.command == "prepare-model":
        path = prepare_model(args.model)
        print(json.dumps({"model": str(path), "verified": True}))
        return 0
    if args.command == "benchmark":
        from . import __version__
        audio = read_wav(args.wav)
        engine = CpuEngine(args.model, library=args.library)
        before = time.perf_counter()
        engine.load()
        load_seconds = time.perf_counter() - before
        runs = []
        for _ in range(max(1, min(10, args.repeat))):
            before = time.perf_counter()
            text = engine.transcribe(audio)
            elapsed = time.perf_counter() - before
            row = {"inference_seconds": elapsed, "audio_seconds": len(audio) / 16000,
                   "rtf": elapsed / (len(audio) / 16000) if len(audio) else None, "characters": len(text)}
            if args.include_text:
                row["text"] = text
            runs.append(row)
        print(json.dumps({"version": __version__, "platform": platform.platform(), "machine": platform.machine(),
                          "engine": "Haas-ja-v1.0 Q4_K", "threads": engine.threads, "gpu": False, "model_load_seconds": load_seconds, "runs": runs},
                         ensure_ascii=False, indent=2))
        engine.close()
        return 0
    if args.command == "render-preview":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from .ui import MainWindow, CaptionPanel, SetupWindow, InstallWindow, COPY
        qt = QApplication(["Shigaraki preview"])
        window = MainWindow()
        window.log_enabled.setChecked(True)
        window.proof_enabled.setChecked(False)
        window.modifier.setCurrentIndex(0)
        window.punctuation_enabled.setChecked(True)
        window.context_enabled.setChecked(True)
        window.editor.setPlainText("信楽のWindows版を試しています。\n\n話している途中の文字を読みながら、\nPCの中だけで音声を文字にします。")
        window.busy(False)
        window.status.setText(COPY["ready"])
        window.show()
        panel = CaptionPanel()
        panel.text.setText("話している途中の文字が、\nここに少しずつ表示されます。")
        panel.wave.level = 0.75
        panel.show()
        args.directory.mkdir(parents=True, exist_ok=True)
        qt.processEvents()
        window.grab().save(str(args.directory / "main.png"))
        panel.grab().save(str(args.directory / "caption.png"))
        window.tabs.setCurrentIndex(2)
        qt.processEvents()
        window.grab().save(str(args.directory / "settings.png"))
        setup = SetupWindow()
        setup.show()
        qt.processEvents()
        setup.grab().save(str(args.directory / "setup.png"))
        installer = InstallWindow()
        installer.update_stage("asr_model", 210_000_000, 351_901_216, 58)
        installer.show()
        qt.processEvents()
        installer.grab().save(str(args.directory / "install-models.png"))
        installer.update_stage("complete", 0, 0, 68, finished=True)
        qt.processEvents()
        installer.grab().save(str(args.directory / "install-complete.png"))
        setup.hide(); panel.hide(); window.hide(); installer.hide()
        qt.processEvents()
        import shiboken6
        for widget in (setup, panel, window, installer):
            shiboken6.delete(widget)
        return 0
    if sys.platform != "win32":
        parser.error("run requires Windows; benchmark and render-preview also work on macOS")
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication
    from .engine import data_dir
    from .app import App
    import hashlib
    qt = QApplication(["Shigaraki Haas Preview"])
    qt.setApplicationName("ShigarakiHaasPreview")
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShigarakiHaasPreview.Desktop")
    from .ui import wave_icon
    from PySide6.QtGui import QIcon
    icon = Path(__file__).with_name("app.ico")
    qt.setWindowIcon(QIcon(str(icon)) if icon.is_file() else wave_icon())
    from .usage_guard import acquire, OtherEditionRunning
    from .ui import COPY
    try:
        active = acquire("haas")
    except OtherEditionRunning:
        ctypes.windll.user32.MessageBoxW(None, COPY["other_running"], COPY["name"], 0x40)
        return 2
    try:
        server_name = "shigaraki-haas-preview-" + hashlib.sha256(str(data_dir()).encode()).hexdigest()[:16]
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(250):
            socket.disconnectFromServer()
            return 0
        server = QLocalServer(qt)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(server_name):
            return 1
        app = App(qt, force_setup=args.setup, from_installer=args.installed)
        def show_existing():
            peer = server.nextPendingConnection()
            if peer:
                peer.close()
            app.show_main()
        server.newConnection.connect(show_existing)
        return qt.exec()
    finally:
        active.close()


if __name__ == "__main__":
    raise SystemExit(main())
