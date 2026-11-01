import json
from pathlib import Path
import tempfile
import unittest

from shigaraki_win.usage_guard import acquire, OtherEditionRunning, COMMON, MARKERS
from shigaraki_win.paste_diagnostics import with_history, MAX_READ_BYTES


class Kernel:
    """Model named-object reference lifetimes, without any Windows processes."""
    def __init__(self):
        self.objects = {}; self.handles = {}; self.sequence = 0; self.error = 0
        self.denied = set()

    def GetLastError(self):
        return self.error

    def SetLastError(self, value):
        self.error = value

    def _open(self, name):
        self.sequence += 1
        self.handles[self.sequence] = name
        self.objects[name] = self.objects.get(name, 0)+1
        return self.sequence

    def OpenMutexW(self, access, inherited, name):
        if name in self.denied:
            self.error = 5; return None
        if name not in self.objects:
            self.error = 2; return None
        self.error = 0
        return self._open(name)

    def CreateMutexW(self, security, owned, name):
        if name in self.denied:
            self.error = 5; return None
        self.error = 183 if name in self.objects else 0
        return self._open(name)

    def CloseHandle(self, handle):
        name = self.handles.pop(handle)
        self.objects[name] -= 1
        if not self.objects[name]:
            del self.objects[name]
        return True


class UsageGuardTests(unittest.TestCase):
    def test_other_edition_is_refused_without_closing_the_running_app(self):
        kernel = Kernel(); standard = acquire("standard", kernel=kernel)
        before = dict(kernel.objects)
        with self.assertRaises(OtherEditionRunning):
            acquire("haas", kernel=kernel)
        self.assertEqual(kernel.objects, before)
        standard.close()
        haas = acquire("haas", kernel=kernel)
        haas.close()
        self.assertEqual(kernel.objects, {})

    def test_wrapper_and_python_handles_keep_guard_until_both_have_ended(self):
        kernel = Kernel()
        wrapper = acquire("haas", kernel=kernel)
        python = acquire("haas", kernel=kernel)
        wrapper.close()
        with self.assertRaises(OtherEditionRunning):
            acquire("standard", kernel=kernel)
        python.close(); python.close()
        self.assertEqual(kernel.objects, {})

    def test_second_same_edition_launch_can_reach_existing_window(self):
        kernel = Kernel(); first = acquire("standard", kernel=kernel)
        second = acquire("standard", kernel=kernel)
        second.close()
        self.assertIn(COMMON, kernel.objects)
        self.assertIn(MARKERS["standard"], kernel.objects)
        first.close()

    def test_legacy_other_edition_marker_blocks_a_new_edition(self):
        for legacy, new in (("standard", "haas"), ("haas", "standard")):
            with self.subTest(legacy=legacy):
                kernel = Kernel(); old = kernel.CreateMutexW(None, False, MARKERS[legacy])
                with self.assertRaises(OtherEditionRunning):
                    acquire(new, kernel=kernel)
                self.assertNotIn(COMMON, kernel.objects)
                kernel.CloseHandle(old)

    def test_unmarked_concurrent_start_is_rejected_without_a_handle_leak(self):
        kernel = Kernel(); earlier = kernel.CreateMutexW(None, False, COMMON)
        with self.assertRaises(OtherEditionRunning):
            acquire("standard", kernel=kernel)
        self.assertEqual(kernel.objects, {COMMON: 1})
        kernel.CloseHandle(earlier)

    def test_access_denied_never_falls_through_to_an_unprotected_start(self):
        kernel = Kernel(); kernel.denied.add(MARKERS["haas"])
        with self.assertRaises(OSError):
            acquire("standard", kernel=kernel)
        self.assertEqual(kernel.objects, {})

    def test_failed_edition_marker_releases_the_shared_handle(self):
        kernel = Kernel(); kernel.denied.add(MARKERS["standard"])
        with self.assertRaises(OSError):
            acquire("standard", kernel=kernel)
        self.assertEqual(kernel.objects, {})


class DiagnosticHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "paste-diagnostics.json"

    def attempt(self, outcome, **extra):
        latest = {"version": "0.1.0a2", "outcome": outcome,
                  "readback": "matched" if outcome == "confirmed" else "not_checked",
                  "asr_ready": True, "proofreading": {"sentences_checked": 3}}
        latest.update(extra)
        value = with_history(self.path, latest, edition="haas",
                             target_app=r"C:\Program Files\Chrome\chrome.exe",
                             timestamp="2026-11-01T10:00:00+00:00")
        self.path.write_text(json.dumps(value, ensure_ascii=False))
        return value

    def test_success_keeps_last_failure_and_preserves_current_diagnostic_fields(self):
        failed = self.attempt("sendFailed")
        succeeded = self.attempt("confirmed")
        self.assertEqual(succeeded["outcome"], "confirmed")
        self.assertEqual(succeeded["proofreading"], {"sentences_checked": 3})
        self.assertEqual(succeeded["latest_failure"], failed["latest_failure"])
        self.assertEqual(succeeded["target_app"], "chrome.exe")
        self.assertEqual([x["outcome"] for x in succeeded["recent_attempts"]], ["sendFailed", "confirmed"])

    def test_history_is_bounded_but_old_failure_outlives_the_recent_window(self):
        self.attempt("notAttempted")
        for _ in range(12):
            current = self.attempt("confirmed")
        self.assertEqual(len(current["recent_attempts"]), 8)
        self.assertTrue(all(x["outcome"] == "confirmed" for x in current["recent_attempts"]))
        self.assertEqual(current["latest_failure"]["outcome"], "notAttempted")
        self.assertLess(self.path.stat().st_size, MAX_READ_BYTES)

    def test_unverified_dispatch_is_not_fabricated_as_a_failed_paste(self):
        current = self.attempt("sentUnverified", readback="unchanged")
        self.assertIsNone(current["latest_failure"])
        self.attempt("sendFailed")
        current = self.attempt("sentUnverified", readback="unavailable_or_formatted")
        self.assertEqual(current["latest_failure"]["outcome"], "sendFailed")

    def test_old_or_hostile_diagnostics_are_filtered_to_metadata(self):
        previous = {"version": "0.1.0a1", "outcome": "notAttempted", "text": "秘密の本文",
                    "window_title": "秘密のページ", "target_app": "https://private.example/secret.exe"}
        self.path.write_text(json.dumps(previous, ensure_ascii=False))
        current = self.attempt("confirmed")
        encoded = json.dumps(current, ensure_ascii=False)
        self.assertNotIn("秘密", encoded)
        self.assertNotIn("private.example", encoded)
        self.assertIsNone(current["latest_failure"]["timestamp"])

    def test_broken_or_oversized_history_does_not_prevent_current_diagnostics(self):
        for data in (b"not json", b"[null]", b"x"*(MAX_READ_BYTES+1),
                     b'{"recent_attempts":[{"outcome":[]},{"outcome":{}}]}'):
            with self.subTest(data_length=len(data)):
                self.path.write_bytes(data)
                current = self.attempt("confirmed")
                self.assertEqual(current["outcome"], "confirmed")
                self.assertEqual(len(current["recent_attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
