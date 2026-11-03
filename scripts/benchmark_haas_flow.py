#!/usr/bin/env python3
"""Measure one resident GGUF CPU worker with WAV input paced at recording speed.

No microphone is opened. Transcription text is saved only with --include-text.
Each invocation starts exactly one worker; peak RSS covers its whole lifetime.
"""
from __future__ import annotations
import argparse
import base64
import ctypes
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import sys
import time
import wave

RATE = 16000
sys.dont_write_bytecode = True

def sha(data):
    return hashlib.sha256(data).hexdigest()


def keywords_metadata(keywords):
    if not isinstance(keywords, str) or not keywords.strip():
        raise ValueError("config.keywords must be an explicit, non-empty fixed string shared by every variant")
    if "\0" in keywords or len(keywords.encode("utf-8")) > 2400:
        raise ValueError("config.keywords violates the Rust protocol keyword bounds")
    return {"text": keywords, "sha256": sha(keywords.encode("utf-8")),
            "characters": len(keywords), "term_count": sum(bool(v.strip()) for v in keywords.splitlines())}


def cpu_snapshot(pid):
    """Cumulative process user/system CPU seconds, not CPU percentage."""
    wakeups = {"pkg_idle_wakeups": None, "interrupt_wakeups": None}
    try:
        if sys.platform == "darwin":
            fields = ["user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups",
                      "pageins", "wired_size", "resident_size", "phys_footprint",
                      "proc_start_abstime", "proc_exit_abstime", "child_user_time",
                      "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups",
                      "child_pageins", "child_elapsed_abstime", "diskio_bytesread", "diskio_byteswritten"]
            class Usage(ctypes.Structure):
                _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(v, ctypes.c_uint64) for v in fields]
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.proc_pid_rusage.restype = ctypes.c_int
            usage = Usage()
            if lib.proc_pid_rusage(pid, 2, ctypes.byref(usage)):
                raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
            class Timebase(ctypes.Structure):
                _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
            system_lib = ctypes.CDLL(None)
            system_lib.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
            timebase = Timebase()
            if system_lib.mach_timebase_info(ctypes.byref(timebase)) or not timebase.denom:
                raise RuntimeError("mach_timebase_info failed")
            seconds_per_tick = timebase.numer / timebase.denom / 1e9
            user, system = usage.user_time * seconds_per_tick, usage.system_time * seconds_per_tick
            method = ("proc_pid_rusage(RUSAGE_INFO_V2), mach ticks * "
                      f"{timebase.numer}/{timebase.denom}/1e9")
            wakeups = {"pkg_idle_wakeups": usage.pkg_idle_wkups,
                       "interrupt_wakeups": usage.interrupt_wkups}
        elif sys.platform == "win32":
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            values = [wintypes.FILETIME() for _ in range(4)]
            try:
                if not kernel32.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel32.CloseHandle(handle)
            secs = lambda v: ((v.dwHighDateTime << 32) | v.dwLowDateTime) / 1e7
            user, system = secs(values[3]), secs(values[2])
            method = "GetProcessTimes, 100-nanosecond units / 1e7"
        elif sys.platform.startswith("linux"):
            raw = Path(f"/proc/{pid}/stat").read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            ticks = os.sysconf("SC_CLK_TCK")
            user, system = int(fields[11]) / ticks, int(fields[12]) / ticks
            method = "/proc/PID/stat utime+stime / SC_CLK_TCK"
        else:
            raise NotImplementedError(f"CPU timer unavailable on {sys.platform}")
        return {"method": method, "user_seconds": user, "system_seconds": system,
                "total_seconds": user + system, "error": None, **wakeups}
    except Exception as exc:
        return {"method": None, "user_seconds": None, "system_seconds": None,
                "total_seconds": None, "error": f"{type(exc).__name__}: {exc}",
                "pkg_idle_wakeups": None, "interrupt_wakeups": None}


def cpu_difference(before, after):
    if before["error"] or after["error"]:
        return {"user_seconds": None, "system_seconds": None, "total_seconds": None,
                "pkg_idle_wakeups": None, "interrupt_wakeups": None,
                "error": before["error"] or after["error"]}
    return {key: max(0, after[key] - before[key])
                if before.get(key) is not None and after.get(key) is not None else None
            for key in ("user_seconds", "system_seconds", "total_seconds",
                        "pkg_idle_wakeups", "interrupt_wakeups")} | {"error": None}


def read_case(item):
    path = Path(item["wav"]).expanduser().resolve(strict=True)
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError(f"expected mono PCM16 16000-Hz WAV: {path}")
        frames = f.getnframes()
        if not 1 <= frames <= 305 * RATE:
            raise ValueError("WAV must contain 1..4880000 frames (at most 305 seconds)")
        pcm = f.readframes(frames)
    if not pcm:
        raise ValueError(f"empty audio: {path}")
    return {"name": item["name"], "wav": str(path), "pcm": pcm,
            "pcm_sha256": sha(pcm), "audio_seconds": len(pcm) / (2 * RATE)}


def import_worker(source):
    sys.path.insert(0, str(Path(source).resolve(strict=True)))
    RustWorker = importlib.import_module("shigaraki_win.rust_worker").RustWorker
    class TimedRustWorker(RustWorker):
        def _read(self):
            try:
                while True:
                    line = self.process.stdout.readline(1_048_577)
                    received = time.monotonic_ns()
                    if not line:
                        raise EOFError("Rust worker exited")
                    if len(line) > 1_048_576 or not line.endswith(b"\n"):
                        raise ValueError("oversized worker message")
                    message = json.loads(line)
                    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                        raise ValueError("invalid worker message")
                    if message["type"] != "progress":
                        message["_bench_received_ns"] = received
                        self._messages.put_nowait(message)
            except BaseException as exc:
                try:
                    self._messages.put_nowait(exc)
                except queue.Full:
                    pass
    return TimedRustWorker


def measure(worker, case, keywords, *, timeout=120, include_text=False):
    """Measure pipe receipt and worker CPU, retaining every final PCM sample."""
    pcm, sent, seq = case["pcm"], 0, 0
    samples = len(pcm) // 2
    duration = samples / RATE
    cpu_before = cpu_snapshot(worker.process.pid)
    started = time.monotonic_ns()
    audio_end = started + samples * 1_000_000_000 // RATE
    rid = worker.begin(keywords, preview=True)
    first = flush_start = flush_end = stop_start = stop_end = None
    events = []
    while True:
        now = time.monotonic_ns()
        if now - audio_end > timeout * 1e9:
            raise TimeoutError("worker did not complete before the deadline")
        if stop_start is None:
            captured = min(samples, int((now - started) / 1e9 * RATE))
            ending = captured == samples
            if ending:
                flush_start = time.monotonic_ns()
            while captured - sent >= 800 or ending and captured > sent:
                end = min(captured, sent + 800)
                worker._send([{"type": "audio", "id": rid, "seq": seq,
                               "pcm": base64.b64encode(pcm[sent * 2:end * 2]).decode("ascii")}])
                sent, seq = end, seq + 1
            if ending:
                flush_end = time.monotonic_ns()
                stop_start = time.monotonic_ns()
                worker._send([{"type": "stop", "id": rid}])
                stop_end = time.monotonic_ns()
        try:
            message = worker._messages.get(timeout=.005)
        except queue.Empty:
            if not worker.ready:
                raise RuntimeError("worker exited before complete")
            continue
        if isinstance(message, BaseException):
            raise RuntimeError("worker reader failed") from message
        received = message.pop("_bench_received_ns", time.monotonic_ns())
        if message.get("id") != rid or message.get("type") not in ("caption", "complete", "error"):
            raise RuntimeError("unexpected worker session/event")
        text = message.get("text", "")
        if not isinstance(text, str):
            raise ValueError("worker returned non-string text")
        event = {"received_seconds": (received - started) / 1e9,
                 "type": message["type"], "final": message.get("final"),
                 "characters": len(text), "text_sha256": sha(text.encode("utf-8")),
                 "metrics": message.get("metrics", {})}
        if include_text:
            event["text"] = text
        events.append(event)
        if message["type"] == "caption" and text and first is None:
            first = received
        if message["type"] == "error":
            raise RuntimeError("worker decode error: " + str(message.get("code", "unknown")))
        if message["type"] == "complete":
            finished = received
            break
    cpu_after = cpu_snapshot(worker.process.pid)
    if sent != samples or stop_start is None:
        raise AssertionError("complete without sending all final PCM and Stop")
    anchors = {"session_start": started, "audio_virtual_end": audio_end,
               "flush_start": flush_start, "flush_complete": flush_end,
               "stop_send_start": stop_start, "stop_send_complete": stop_end,
               "complete_receive": finished}
    row = {"case": case["name"], "wav": case["wav"], "pcm_sha256": case["pcm_sha256"],
           "audio_seconds": duration, "sent_samples": sent, "expected_samples": samples,
           "preview": True, "first_caption_seconds": (first - started) / 1e9 if first else None,
           "preview_before_audio_end": bool(first and first < audio_end),
           "preview_before_stop": bool(first and first < stop_start),
           "audio_end_to_complete_seconds": (finished - audio_end) / 1e9,
           "stop_send_to_complete_seconds": (finished - stop_start) / 1e9,
           "wall_seconds": (finished - started) / 1e9,
           "anchors_monotonic_ns": anchors,
           "anchors_seconds": {k: (v - started) / 1e9 for k, v in anchors.items()},
           "worker_cpu": cpu_difference(cpu_before, cpu_after),
           "cpu_before": cpu_before, "cpu_after": cpu_after,
           "rust_metrics": message.get("metrics", {}), "events": events,
           "text_sha256": sha(text.encode("utf-8")), "text_characters": len(text)}
    if include_text:
        row["text"] = text
    return text, row


def windows_peak_rss(pid):
    """PeakWorkingSetSize is the process lifetime peak resident working set."""
    try:
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak", "working", "peak_paged", "paged",
                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        value = Counters(); value.cb = ctypes.sizeof(value)
        try:
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(value), value.cb):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        return {"bytes": value.peak, "method": "GetProcessMemoryInfo.PeakWorkingSetSize", "error": None}
    except Exception as exc:
        return {"bytes": None, "method": None, "error": f"{type(exc).__name__}: {exc}"}


def fingerprint(path, *, hash_file=True):
    value = {"path": str(path), "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    if hash_file:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1_048_576), b""):
                h.update(chunk)
        value["sha256"] = h.hexdigest()
    return value


def resolve_library(path, platform_name=None):
    if (platform_name or sys.platform) == "win32":
        if path is not None:
            raise ValueError("omit --library on Windows; the worker uses its linked CPU engine")
        return None
    if path is None:
        raise ValueError("--library is required on macOS/Linux")
    return path.resolve(strict=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--library", type=Path, help="required on macOS/Linux; omit on Windows (linked engine)")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True, nargs="+")
    parser.add_argument("--keywords-file", type=Path, required=True, help="explicit common UTF-8 keywords, one term per line")
    parser.add_argument("--model-sha256", help="previously verified model SHA; recorded as supplied, not rehashed")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--warmup-seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--include-text", action="store_true", help="save raw transcription text in addition to hashes")
    parser.add_argument("--output", type=Path, required=True, help="new JSON file; never overwrite")
    args = parser.parse_args()
    if (not math.isfinite(args.warmup_seconds) or not math.isfinite(args.timeout)
            or min(args.threads, args.rounds, args.warmup_seconds, args.timeout) <= 0):
        parser.error("threads, rounds, warmup and timeout must be finite and positive")
    if args.output.exists():
        parser.error("output already exists")
    if args.model_sha256 and (len(args.model_sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in args.model_sha256)):
        parser.error("model-sha256 must contain exactly 64 hexadecimal characters")
    worker_path, model = (p.resolve(strict=True) for p in (args.worker, args.model))
    try:
        library = resolve_library(args.library)
    except ValueError as exc:
        parser.error(str(exc))
    keywords = args.keywords_file.read_text(encoding="utf-8")
    keyword_info = keywords_metadata(keywords)
    cases = [read_case({"name": p.stem, "wav": str(p)}) for p in args.audio]
    if len({c["name"] for c in cases}) != len(cases):
        parser.error("audio filenames must have distinct stems")
    Worker = import_worker(Path(__file__).resolve().parents[1])
    worker = Worker(worker_path, model, args.threads, library)
    result = {"format": 1, "host": f"{platform.system()}-{platform.release()}-{platform.machine()}", "python": sys.version,
              "threads": args.threads, "rounds": args.rounds, "include_text": args.include_text,
              "worker": fingerprint(worker_path), "library": fingerprint(library) if library is not None else None,
              "model": fingerprint(model, hash_file=False), "keywords": keyword_info,
              "model_sha256_supplied": args.model_sha256, "model_hash_verified_by_this_run": False,
              "trials": [], "warmup": None, "status": "running",
              "definitions": {"first_caption": "non-empty caption pipe receipt since Start write begins; not UI paint",
                  "audio_end": "virtual recording end = session start + exact WAV duration",
                  "stop_wait": "Stop write begins to complete pipe receipt; final PCM flush is before Stop",
                  "cpu": "worker user+system CPU seconds including native threads; not CPU percent",
                  "wakeups": "Darwin proc_pid_rusage package-idle and interrupt wakeup counter deltas; null when unsupported",
                  "peak_rss": "whole worker lifetime, including load/warmup; not a per-trial allocation",
                  "text_equal": "same-case first measured round consistency; not absolute recognition accuracy"},
              "limitations": ["WAV playback simulates recording cadence; microphone, UI paint and Windows hardware acceptance are not measured.",
                  "Mac/Linux resource CPU and RSS, and Windows API measurements have different platform definitions.",
                  "Model SHA is supplied from separate verification; this invocation records model path/size/mtime without reading all model bytes."]}
    child_before = None
    if sys.platform in ("darwin", "linux"):
        import resource
        child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = {"bytes": None, "method": None, "error": "measurement unavailable"}
    exit_code = 0
    # Reserve the output before starting inference; even a failed run is recorded.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            start = time.monotonic()
            worker.start()
            result["load_seconds"] = time.monotonic() - start
            result["ready"] = worker.info
            warm = dict(cases[0], name="warmup", pcm=cases[0]["pcm"][:int(args.warmup_seconds * RATE) * 2])
            warm["pcm_sha256"] = sha(warm["pcm"])
            _, result["warmup"] = measure(worker, warm, keywords, timeout=args.timeout, include_text=args.include_text)
            references = {}
            for round_i in range(args.rounds):
                for case in cases:
                    text, row = measure(worker, case, keywords, timeout=args.timeout, include_text=args.include_text)
                    references.setdefault(case["name"], text)
                    row.update(round=round_i + 1, reference_text_equal=text == references[case["name"]])
                    result["trials"].append(row)
                    print(json.dumps({"case": row["case"], "round": round_i + 1, "threads": args.threads,
                        "first_caption_seconds": row["first_caption_seconds"],
                        "audio_end_to_complete_seconds": row["audio_end_to_complete_seconds"],
                        "worker_cpu": row["worker_cpu"], "text_sha256": row["text_sha256"]}), flush=True)
            result["status"] = "complete"
            if not all(r["preview_before_audio_end"] for r in result["trials"]):
                result["status"] = "preview_requirement_unmet"
                exit_code = 2
        except BaseException as exc:
            result["status"] = "failed"
            result["failure"] = f"{type(exc).__name__}: {exc}"
            exit_code = 1
        finally:
            if sys.platform == "win32" and worker.process is not None:
                peak = windows_peak_rss(worker.process.pid)
            worker.close(kill=True)
            if child_before is not None:
                import resource
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                result["children_peak_rss_before"] = child_before.ru_maxrss
                result["children_peak_rss_after"] = usage.ru_maxrss
                no_prior = child_before.ru_maxrss == 0 and child_before.ru_utime == 0 and child_before.ru_stime == 0
                if no_prior or usage.ru_maxrss > child_before.ru_maxrss:
                    factor = 1 if sys.platform == "darwin" else 1024
                    peak = {"bytes": usage.ru_maxrss * factor,
                            "method": "getrusage(RUSAGE_CHILDREN).ru_maxrss; sole new child and peak exceeds any prior child",
                            "error": None}
                else:
                    peak = {"bytes": None, "method": None, "error": "prior child usage prevents attributing peak RSS to this worker"}
            result["worker_peak_rss"] = peak
            json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
