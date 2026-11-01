import contextlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.runtime_status import POLICY_VIOLATION, ProofreaderBlocked, policy_violation, signed_exit_code
from shigaraki_win.proofread_runtime import ProofreadRuntime


class RuntimePolicyTests(unittest.TestCase):
    def test_host_runs_in_isolated_python_without_script_directory_on_path(self):
        from shigaraki_win import proofreader_host
        with tempfile.TemporaryDirectory() as unrelated:
            # Model the embeddable ._pth search restrictions. A nearby module
            # or the current working directory must not provide app imports.
            Path(unrelated, "runtime_status.py").write_text("raise RuntimeError('untrusted cwd import')")
            process = subprocess.Popen([sys.executable, "-I", "-S", str(Path(proofreader_host.__file__).resolve()),
                sys.executable, "-I", "-c", "raise SystemExit(7)"], cwd=unrelated,
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                code = process.wait(timeout=10)
                self.assertEqual(code, 7, process.stderr.read().decode())
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=2)
                process.stdin.close(); process.stderr.close()

    def test_startup_failure_keeps_host_error_and_closes_pipe_handles(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch("shigaraki_win.proofread_runtime.valid_file", return_value=True))
            stack.enter_context(patch("shigaraki_win.proofread_runtime.os.setpriority"))
            process = MagicMock(); process.poll.return_value = 1
            process.stdin = io.BytesIO()
            process.stderr = io.BytesIO(b"ModuleNotFoundError: No module named 'runtime_status'\n")
            launch = stack.enter_context(patch("shigaraki_win.proofread_runtime.subprocess.Popen", return_value=process))
            runtime = ProofreadRuntime(executable=root/"llama-server.exe")
            with self.assertRaisesRegex(RuntimeError, "ModuleNotFoundError"):
                runtime.load()
            self.assertIs(launch.call_args.kwargs["stderr"], subprocess.PIPE)
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stderr.closed)
            self.assertFalse(runtime.ready)

    @unittest.skipUnless(sys.platform == "win32", "NTSTATUS process exit requires Windows")
    def test_windows_host_preserves_native_policy_status_bits(self):
        from shigaraki_win import proofreader_host
        process = subprocess.Popen([sys.executable, "-B", str(Path(proofreader_host.__file__)),
            sys.executable, "-B", "-c", "raise SystemExit(0xC0E90002 - 2**32)"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self.assertTrue(policy_violation(process.wait(timeout=10)), process.stderr.read().decode())
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=2)
            process.stdin.close(); process.stderr.close()

    def test_real_host_retains_child_exit_without_buffered_stdin_shutdown_crash(self):
        from shigaraki_win import proofreader_host
        process = subprocess.Popen([sys.executable, "-B", str(Path(proofreader_host.__file__)),
            sys.executable, "-B", "-c", "raise SystemExit(7)"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.wait(timeout=10), 7, process.stderr.read().decode())
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=2)
            process.stdin.close(); process.stderr.close()

    def test_status_bits_survive_c_int_exit_conversion(self):
        code = signed_exit_code(POLICY_VIOLATION)
        self.assertLess(code, 0)
        self.assertTrue(policy_violation(code))
        for ordinary in (0, 1, -9):
            self.assertEqual(signed_exit_code(ordinary), ordinary)

    def test_only_known_policy_code_is_treated_as_policy_block(self):
        for code in (POLICY_VIOLATION, POLICY_VIOLATION - 2**32, 4551):
            self.assertTrue(policy_violation(code))
        for code in (None, True, 0, 1, 193, 126, 577, 0xC000007B, 0xC000012F):
            self.assertFalse(policy_violation(code))

    def test_host_sets_error_presentation_before_launch_and_returns_policy_code(self):
        from shigaraki_win import proofreader_host
        order = []
        error = OSError("blocked"); error.winerror = 4551
        with patch.object(proofreader_host, "loader_errors_as_status", side_effect=lambda: order.append("mode")), \
                patch.object(proofreader_host.subprocess, "Popen", side_effect=lambda *a, **k: (order.append("launch"), (_ for _ in ()).throw(error))[1]):
            self.assertEqual(proofreader_host.main(), POLICY_VIOLATION)
        self.assertEqual(order, ["mode", "launch"])

    def test_host_does_not_hide_other_start_failures(self):
        from shigaraki_win import proofreader_host
        with patch.object(proofreader_host, "loader_errors_as_status"), \
                patch.object(proofreader_host.subprocess, "Popen", side_effect=OSError("missing")):
            with self.assertRaises(OSError):
                proofreader_host.main()

    def test_runtime_fingerprint_changes_after_a_dll_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/"llama-server.exe").write_bytes(b"exe")
            dll = root/"ggml.dll"; dll.write_bytes(b"dll1")
            runtime = ProofreadRuntime(executable=root/"llama-server.exe")
            first = runtime._runtime_fingerprint()
            dll.write_bytes(b"dll2")
            self.assertNotEqual(first, runtime._runtime_fingerprint())

    def test_policy_exit_is_cached_and_does_not_launch_again_automatically(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch("shigaraki_win.proofread_runtime.data_dir", return_value=root))
            runtime = ProofreadRuntime(executable=root/"llama-server.exe")
            stack.enter_context(patch("shigaraki_win.proofread_runtime.sys.platform", "win32"))
            stack.enter_context(patch("shigaraki_win.proofread_runtime.valid_file", return_value=True))
            stack.enter_context(patch("shigaraki_win.proofread_runtime.os.setpriority"))
            for name in ("CREATE_NO_WINDOW", "BELOW_NORMAL_PRIORITY_CLASS"):
                stack.enter_context(patch("shigaraki_win.proofread_runtime.subprocess."+name, 0, create=True))
            process = MagicMock(); process.poll.return_value = POLICY_VIOLATION - 2**32
            launch = stack.enter_context(patch("shigaraki_win.proofread_runtime.subprocess.Popen", return_value=process))
            stack.enter_context(patch.object(runtime, "_runtime_fingerprint", return_value="fixed-binary-hash"))
            with self.assertRaises(ProofreaderBlocked):
                runtime.load()
            launch.assert_called_once()
            self.assertTrue(json.loads((root/"proofreader-runtime-status.json").read_text())["blocked"])
            with self.assertRaises(ProofreaderBlocked) as caught:
                runtime.load()
            self.assertTrue(caught.exception.cached)
            launch.assert_called_once()
            # Explicit retry runs the exact original loader path; no alternative
            # DLL, policy edits, privilege changes or network source is used.
            process.poll.return_value = None
            with patch.object(runtime, "_request", return_value=io.BytesIO(b'{"data":[{"id":"shigaraki-proofreader"}]}')):
                runtime.load(retry_blocked=True)
            self.assertEqual(launch.call_count, 2)
            self.assertFalse(json.loads((root/"proofreader-runtime-status.json").read_text())["blocked"])
            runtime.close()

    def test_changed_runtime_does_not_reuse_old_block(self):
        with tempfile.TemporaryDirectory() as directory, patch("shigaraki_win.proofread_runtime.data_dir", return_value=Path(directory)):
            runtime = ProofreadRuntime()
            runtime._remember_policy("old", True)
            self.assertTrue(runtime._policy_cache("old"))
            self.assertFalse(runtime._policy_cache("new"))

