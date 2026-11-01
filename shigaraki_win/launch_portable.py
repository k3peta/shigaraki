"""Entry for the isolated Windows embeddable Python package."""
from pathlib import Path
import ctypes
import json
import os
import sys
import traceback

root = Path(__file__).resolve().parent
vendor = root / "punctuation_vendor"
if vendor.is_dir():
    sys.path.insert(0, str(vendor))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
site = root / "runtime" / "site-packages"
handles = []
# pythonw has no standard streams; libraries may still call .flush() on them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.platform == "win32":
    for directory in [root / "runtime", site, site / "PySide6", site / "shiboken6", site / "numpy.libs"]:
        if directory.is_dir():
            handles.append(os.add_dll_directory(str(directory)))
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(site / "PySide6" / "plugins" / "platforms")

try:
    from shigaraki_win.__main__ import main
    raise SystemExit(main(sys.argv[1:] or ["run"]))
except Exception:
    directory = Path(os.environ.get("LOCALAPPDATA", root)) / "ShigarakiHaasPreview"
    directory.mkdir(parents=True, exist_ok=True)
    checking = sys.argv[1:2] == ["verify-models"]
    (directory / ("model-check-error.txt" if checking else "startup-error.txt")).write_text(traceback.format_exc(), encoding="utf-8")
    if checking:
        raise SystemExit(1)
    if sys.platform == "win32":
        copy = json.loads((root / "shigaraki_win" / "copy.json").read_text(encoding="utf-8"))
        ctypes.windll.user32.MessageBoxW(None, copy["startup_failed"], copy["name"], 0x10)
        raise SystemExit(1)
    else:
        raise
