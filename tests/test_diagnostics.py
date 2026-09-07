import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass import cli
from sandglass.diagnostics import (
    classify_component_error,
    clear_component_failure,
    diagnostics_path,
    record_component_failure,
    runtime_diagnostics,
)
from sandglass.serve import Handler


class _WindowsError(RuntimeError):
    def __init__(self, message: str, *, winerror=None, hresult=None):
        super().__init__(message)
        self.winerror = winerror
        self.hresult = hresult


class ConcurrentDiagnosticsTests(unittest.TestCase):
    """Three processes record into one file, and recording is read-edit-replace."""

    def _child(self, code: str, home: str, *args: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-c", code, *args],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, SANDGLASS_HOME=home),
        )

    def test_two_recording_processes_do_not_erase_each_other(self):
        """Measured before the file lock: 58 of 120 survived, one side entirely.

        The in-process RLock says nothing to the observer or the panel. Whoever
        read first wrote the other's failures back out of existence, so a
        machine with two things broken could show one of them, or neither.
        """
        code = (
            "import sys; "
            "from sandglass.diagnostics import record_component_failure; "
            "tag = sys.argv[1]; "
            "[record_component_failure(f'{tag}{i:03d}', OSError(13, 'measured')) "
            "for i in range(40)]"
        )
        with tempfile.TemporaryDirectory() as tmp:
            children = [self._child(code, tmp, tag) for tag in ("A", "B")]
            for child in children:
                self.assertEqual(child.wait(timeout=120), 0)
            components = json.loads(
                (Path(tmp) / "runtime-diagnostics.json").read_text(encoding="utf-8")
            )["components"]

        self.assertEqual(
            len(components), 80,
            "两个进程各记 40 条,文件里少一条就是有人被覆盖掉了",
        )
        self.assertEqual(
            sorted(k[0] for k in components).count("A"), 40, "A 侧被覆盖"
        )

    def test_a_second_process_waits_for_the_recorder_holding_the_file(self):
        """The erasure above is only closed if the lock is held across processes."""
        from sandglass import diagnostics

        code = (
            "import sys; from pathlib import Path; "
            "from sandglass.diagnostics import record_component_failure; "
            "Path(sys.argv[1]).write_text('ready', encoding='utf-8'); "
            "record_component_failure('child', OSError(13, 'measured')); "
            "Path(sys.argv[2]).write_text('done', encoding='utf-8')"
        )
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "child-ready"
            done = Path(tmp) / "child-done"
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}):
                with diagnostics._exclusive():
                    child = self._child(code, tmp, str(ready), str(done))
                    deadline = time.monotonic() + 60
                    while not ready.exists() and time.monotonic() < deadline:
                        self.assertIsNone(child.poll(), "子进程还没开始记录就退出了")
                        time.sleep(0.05)
                    self.assertTrue(ready.exists(), "子进程始终没有开始记录")
                    time.sleep(0.5)
                    self.assertFalse(done.exists(), "另一个进程没有等锁,它可以覆盖已记录的失败")
                self.assertEqual(child.wait(timeout=60), 0)
                self.assertTrue(done.exists(), "锁释放后记录没有完成")


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_windows_application_control_codes_and_text_are_classified(self):
        cases = (
            _WindowsError("blocked", winerror=1260),
            _WindowsError("blocked", hresult=0x800704EC),
            RuntimeError("An Application Control policy has blocked this file."),
        )
        for error in cases:
            with self.subTest(error=repr(error)):
                status, _code = classify_component_error(error)
                self.assertEqual(status, "blocked_by_application_control")

    def test_other_component_failures_remain_distinct(self):
        cases = (
            (FileNotFoundError("missing"), "missing_component"),
            (PermissionError("denied"), "access_denied"),
            (TimeoutError("slow"), "startup_timeout"),
            (RuntimeError("broken"), "component_failed"),
        )
        for error, expected in cases:
            with self.subTest(error=repr(error)):
                status, _code = classify_component_error(error)
                self.assertEqual(status, expected)

    def test_diagnostic_record_is_sandglass_owned_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandglass_home = Path(tmp) / "sandglass"
            secret_path = r"C:\Users\person\.codex\auth.json"
            error = _WindowsError(
                f"Application Control policy blocked {secret_path} token=very-secret",
                winerror=1260,
            )
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(sandglass_home)}, clear=False):
                issue = record_component_failure("native_panel", error)
                stored_path = diagnostics_path()
                stored_text = stored_path.read_text(encoding="utf-8")
                stored = json.loads(stored_text)

                self.assertTrue(stored_path.is_relative_to(sandglass_home))
                self.assertEqual(issue["status"], "blocked_by_application_control")
                self.assertEqual(stored["components"]["native_panel"]["code"], 1260)
                self.assertNotIn(secret_path, stored_text)
                self.assertNotIn("very-secret", stored_text)
                self.assertNotIn("Application Control policy", stored_text)

                clear_component_failure("native_panel")
                self.assertEqual(runtime_diagnostics()["components"], {})

    def test_missing_or_malformed_record_is_an_empty_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}, clear=False):
                self.assertEqual(runtime_diagnostics()["components"], {})
                diagnostics_path().write_text("not json", encoding="utf-8")
                self.assertEqual(runtime_diagnostics()["components"], {})

    def test_a_diagnostics_file_we_cannot_read_is_not_replaced_with_one_component(self):
        """A failed read is not empty diagnostics.

        `_read_payload` treated every OSError as "no file", so a locked
        runtime-diagnostics.json became `{}` and the next record wrote only
        the new component over the top. A machine with two things broken
        then showed one of them, or neither -- the same erasure the file
        lock closed for concurrent writers, reached through a failed read.
        """
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}, clear=False):
                record_component_failure("kept", OSError("kept"))
                path = diagnostics_path()
                original = path.read_text(encoding="utf-8")
                real_read = Path.read_text

                def read_text(self, *args, **kwargs):
                    if self.name == "runtime-diagnostics.json":
                        raise PermissionError("runtime-diagnostics.json is locked")
                    return real_read(self, *args, **kwargs)

                with patch.object(Path, "read_text", read_text):
                    record_component_failure("new", OSError("new"))

                self.assertEqual(path.read_text(encoding="utf-8"), original)
                stored = json.loads(original)
                self.assertIn("kept", stored["components"])
                self.assertNotIn("new", stored["components"])

    def test_runtime_diagnostic_is_exposed_on_the_loopback_api(self):
        payload = {
            "updated_at": "2026-08-29T00:00:00+00:00",
            "components": {
                "native_panel": {
                    "component": "native_panel",
                    "status": "blocked_by_application_control",
                    "code": 1260,
                    "observed_at": "2026-08-29T00:00:00+00:00",
                }
            },
        }
        with patch("sandglass.serve.runtime_diagnostics", return_value=payload):
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/runtime-diagnostics"
                ) as response:
                    received = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(received, payload)

    def test_doctor_json_includes_the_same_component_status(self):
        payload = {
            "updated_at": "2026-08-29T00:00:00+00:00",
            "components": {"native_panel": {"status": "access_denied", "code": 5}},
        }
        output = io.StringIO()
        with patch("sandglass.cli.runtime_diagnostics", return_value=payload), patch(
            "sandglass.cli.load_accounts", return_value=[]
        ), patch(
            "sandglass.cli.require_canonical_state_home"
        ), redirect_stdout(output):
            result = cli.main(["--json", "doctor"])

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["runtime_diagnostics"], payload)


if __name__ == "__main__":
    unittest.main()
