"""Tie the native process lifetime to an anonymous pipe owned by the app."""
import subprocess
import os
import sys
import threading
if __package__:
    from .runtime_status import POLICY_VIOLATION, loader_errors_as_status, policy_violation, signed_exit_code
else:
    # Windows' embeddable Python uses ._pth and does not add this script's
    # directory to sys.path. Resolve app imports from this file, never the cwd
    # or a user PYTHONPATH (the caller runs from the native runtime directory).
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shigaraki_win.runtime_status import POLICY_VIOLATION, loader_errors_as_status, policy_violation, signed_exit_code


def main():
    loader_errors_as_status()
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
             if sys.platform == "win32" else 0)
    try:
        process = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags)
    except OSError as error:
        if policy_violation(getattr(error, "winerror", None)):
            return POLICY_VIOLATION
        raise

    def parent_pipe():
        try:
            # A daemon thread must not hold BufferedReader's lock during Python
            # shutdown: that would abort the host and lose the native exit code.
            os.read(sys.stdin.fileno(), 1)  # EOF also arrives after an app crash.
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
    threading.Thread(target=parent_pipe, daemon=True).start()
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(signed_exit_code(main()))
