"""Run two isolated Qt/Python suites, then model-free host Rust tests."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
PYTHON_RUNNER = """
import sys
import unittest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
import shiboken6

# Keep one owner alive through discovery, all fixtures and widget destruction.
# Qt teardown must finish before Python begins destroying imported modules.
app = QApplication(["Shigaraki model-free tests"])
try:
    suite = unittest.defaultTestLoader.discover("tests", pattern=sys.argv[1])
    result = unittest.TextTestRunner().run(suite)
finally:
    for window in app.topLevelWidgets():
        # Fixtures may deliberately hold incomplete app state. Destroy widgets
        # without sending user-facing close/cancel signals after assertions.
        window.blockSignals(True)
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.quit()
    shiboken6.delete(app)
sys.exit(not result.wasSuccessful())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--rustc", default="rustc")
    args = parser.parse_args()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    for pattern in ("test_shigaraki*.py", "test_haas*.py"):
        print("Python suite: " + pattern, flush=True)
        subprocess.run([sys.executable, "-c", PYTHON_RUNNER, pattern],
                       cwd=PROJECT, env=env, check=True)
    cargo, rustc = shutil.which(args.cargo), shutil.which(args.rustc)
    if not cargo or not rustc:
        raise RuntimeError("Rust and Cargo 1.93.0 are required")
    version = subprocess.check_output([rustc, "--version"], text=True)
    cargo_version = subprocess.check_output([cargo, "--version"], text=True)
    if not version.startswith("rustc 1.93.0 ") or not cargo_version.startswith("cargo 1.93.0 "):
        raise RuntimeError("Rust and Cargo 1.93.0 are required")
    host = next(line.split(": ", 1)[1] for line in subprocess.check_output([rustc, "-vV"], text=True).splitlines()
                if line.startswith("host: "))
    env.update(RUSTC=str(Path(rustc).absolute()))
    print("Rust suite: host, no models", flush=True)
    subprocess.run([cargo, "test", "--locked", "--manifest-path", str(PROJECT / "rust/haas-flow/Cargo.toml"),
                    "--target", host], cwd=PROJECT, env=env, check=True)
    print("All test processes exited successfully.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"tests failed: {error}")
