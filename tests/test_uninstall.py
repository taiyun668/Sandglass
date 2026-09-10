import ctypes
import inspect
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sandglass import uninstall


WINDOWS = unittest.skipUnless(os.name == "nt", "Windows cmd.exe mechanics are required")


class UninstallSafetyTests(unittest.TestCase):
    @staticmethod
    def _delete_registry_tree(path: str) -> None:
        """Delete only the UUID-namespaced test subtree."""
        import winreg

        if re.fullmatch(r"Software\\SandglassTests\\[0-9a-f]{32}", path,
                        flags=re.IGNORECASE) is None:
            raise AssertionError(f"refusing non-test registry cleanup: {path!r}")

        def remove(relative: str) -> None:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative,
                                    0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    while True:
                        try:
                            child = winreg.EnumKey(key, 0)
                        except OSError:
                            break
                        remove(relative + "\\" + child)
            except FileNotFoundError:
                return
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, relative)
            except FileNotFoundError:
                pass

        remove(path)

    @classmethod
    def _registry_fixture(cls, root: Path, *, product_extra: bool = False):
        """Create a UUID-only registry fixture and a cleanup callback."""
        import winreg

        token = uuid.uuid4().hex
        base = rf"Software\SandglassTests\{token}"
        keys = {
            "root": base,
            "uninstall": rf"HKCU\{base}\Uninstall",
            "product": rf"HKCU\{base}\Product",
            "run": rf"HKCU\{base}\Run",
            "unrelated": rf"HKCU\{base}\Unrelated",
        }
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keys["uninstall"][5:]) as key:
            winreg.SetValueEx(key, "marker", 0, winreg.REG_SZ, "owned")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keys["product"][5:]) as key:
            winreg.SetValueEx(key, "InstallDir", 0, winreg.REG_SZ, str(root))
            if product_extra:
                winreg.SetValueEx(key, "owner_value", 0, winreg.REG_SZ, "keep")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keys["run"][5:]) as key:
            executable = root / "Sandglass.exe"
            command = f'"{executable}" --background'
            winreg.SetValueEx(key, "sandglass", 0, winreg.REG_SZ, command)
            winreg.SetValueEx(key, "other-startup", 0, winreg.REG_SZ, "keep")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, keys["unrelated"][5:]) as key:
            winreg.SetValueEx(key, "marker", 0, winreg.REG_SZ, "keep")

        def cleanup() -> None:
            cls._delete_registry_tree(base)

        return keys, cleanup

    def _assert_helper_isolated(self, script: Path,
                                formal_paths: tuple[Path, ...] = ()) -> str:
        """Inspect the complete helper before any cmd process is started."""
        text = script.read_text(encoding="ascii")
        upper = text.upper()
        self.assertNotIn("HKCU", upper)
        self.assertNotIn(r"SOFTWARE\SANDGLASS", upper)
        self.assertNotIn(r"CURRENTVERSION\UNINSTALL", upper)
        # No drive/UNC path may be baked into a helper.  All paths are env
        # values so this assertion catches formal checkouts and direct tool
        # literals alike.
        self.assertIsNone(re.search(r"(?i)(?:[A-Z]:\\|\\\\)", text), text)
        for path in formal_paths:
            self.assertNotIn(str(path).casefold(), text.casefold())
        for variable in (
            "SANDGLASS_ROOT", "SANDGLASS_EXE", "SANDGLASS_RECEIPT",
            "SANDGLASS_RECEIPT_TMP", "SANDGLASS_DESKTOP", "SANDGLASS_PROGRAMS",
            "SANDGLASS_PING", "SANDGLASS_REG", "SANDGLASS_FC",
            "SANDGLASS_UNINSTALL_KEY", "SANDGLASS_PRODUCT_KEY",
            "SANDGLASS_RUN_KEY", "SANDGLASS_MANIFEST",
            "SANDGLASS_MANIFEST_SNAPSHOT", "SANDGLASS_HELPER",
            "SANDGLASS_DELETE_RUN", "SANDGLASS_DELETE_INSTALL_DIR",
            "SANDGLASS_DELETE_PRODUCT_KEY",
        ):
            self.assertIn(variable, text)
        self.assertNotIn("CALL ", upper)
        self.assertIn("exit %sg_rc%", text.lower())
        self.assertNotIn("exit /b %sg_rc%", text.lower())
        self.assertNotIn('RMDIR /S /Q "%SANDGLASS_ROOT%"', upper)
        return text

    @WINDOWS
    def _run_helper(self, root: Path, names: list[str], base: Path,
                    desktop: Path, programs: Path, registry_keys: dict[str, str],
                    *, snapshot: Path | None = None,
                    state: Path | None = None,
                    script_text: str | None = None,
                    inspect: bool = True,
                    delete_run: str = "1",
                    delete_install_dir: str = "1",
                    delete_product_key: str = "1") -> subprocess.CompletedProcess:
        system32 = Path(os.environ["SystemRoot"]) / "System32"
        cmd = system32 / "cmd.exe"
        state = state or base / "state"
        state.mkdir(exist_ok=True)
        manifest = root / uninstall.PRODUCT_MANIFEST
        snapshot = snapshot or state / "manifest.snapshot"
        if not snapshot.exists():
            snapshot.write_bytes(manifest.read_bytes())
        receipt = state / "uninstall-result.json"
        script = base / "uninstall-helper.cmd"
        if script_text is None:
            script_text = uninstall._cmd_script(names)
        script.write_text(script_text, encoding="ascii", newline="")
        formal_paths = (Path(r"D:\Sandglass"), Path(r"D:\Sandglass-codex-uninstall-main-mode"))
        if inspect:
            self._assert_helper_isolated(script, formal_paths)
        environment = os.environ.copy()
        environment.update({
            "SANDGLASS_ROOT": str(root),
            "SANDGLASS_EXE": str(root / "Sandglass.exe"),
            "SANDGLASS_RECEIPT": str(receipt),
            "SANDGLASS_RECEIPT_TMP": str(receipt) + ".tmp",
            "SANDGLASS_DESKTOP": str(desktop),
            "SANDGLASS_PROGRAMS": str(programs),
            "SANDGLASS_PING": str(system32 / "ping.exe"),
            "SANDGLASS_REG": str(system32 / "reg.exe"),
            "SANDGLASS_FC": str(system32 / "fc.exe"),
            "SANDGLASS_UNINSTALL_KEY": registry_keys["uninstall"],
            "SANDGLASS_PRODUCT_KEY": registry_keys["product"],
            "SANDGLASS_RUN_KEY": registry_keys["run"],
            "SANDGLASS_MANIFEST": str(manifest),
            "SANDGLASS_MANIFEST_SNAPSHOT": str(snapshot),
            "SANDGLASS_HELPER": str(script),
            "SANDGLASS_DELETE_RUN": delete_run,
            "SANDGLASS_DELETE_INSTALL_DIR": delete_install_dir,
            "SANDGLASS_DELETE_PRODUCT_KEY": delete_product_key,
        })
        command_line = f'"{cmd}" /d /q /v:off /c ""%SANDGLASS_HELPER%""'
        return subprocess.run(
            command_line,
            executable=str(cmd),
            env=environment,
            cwd=str(system32),
            capture_output=True,
            text=True,
            timeout=30,
        )

    @staticmethod
    def _make_install_fixture(base: Path):
        root = base / "install & (%!)"
        state = base / "state home & (%)"
        desktop = base / "Desktop (&)"
        programs = base / "Programs (%)"
        root.mkdir()
        desktop.mkdir()
        programs.mkdir()
        state.mkdir()
        (root / "Sandglass.exe").write_bytes(b"MZ")
        (root / uninstall.PRODUCT_MANIFEST).write_text(
            json.dumps({
                "schema": 1,
                "paths": [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"],
            }, separators=(",", ":")),
            encoding="utf-8",
        )
        (root / "owner-extra.txt").write_text("keep", encoding="utf-8")
        (state / "state-marker.json").write_text("keep", encoding="utf-8")
        (desktop / "Sandglass.lnk").write_text("owned", encoding="utf-8")
        shortcut_dir = programs / "Sandglass"
        shortcut_dir.mkdir()
        (shortcut_dir / "Sandglass.lnk").write_text("owned", encoding="utf-8")
        (shortcut_dir / "owner-shortcut.txt").write_text("keep", encoding="utf-8")
        return root, state, desktop, programs

    def test_manifest_rejects_unknown_top_level_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Sandglass.exe").write_bytes(b"MZ")
            (root / uninstall.PRODUCT_MANIFEST).write_text(
                json.dumps({
                    "schema": 1,
                    "paths": ["Sandglass.exe", uninstall.PRODUCT_MANIFEST, "user.dat"],
                }),
                encoding="utf-8",
            )
            with self.assertRaises(uninstall.UninstallError):
                uninstall._manifest(root)

    def test_manifest_rejects_a_non_object_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / uninstall.PRODUCT_MANIFEST).write_text("[]", encoding="utf-8")
            with self.assertRaises(uninstall.UninstallError):
                uninstall._manifest(root)

    def test_manifest_accepts_only_built_in_product_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("Sandglass.exe", uninstall.PRODUCT_MANIFEST):
                (root / name).write_bytes(b"MZ" if name.endswith(".exe") else b"{}")
            names = [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"]
            (root / uninstall.PRODUCT_MANIFEST).write_text(
                json.dumps({"schema": 1, "paths": sorted(names)}), encoding="utf-8"
            )
            self.assertEqual(uninstall._manifest(root), sorted(names))

    def test_cmd_path_allows_quoted_env_safe_user_paths(self):
        root = Path.cwd() / "Users & (test)" / "state%home!^"
        self.assertEqual(uninstall._cmd_path(root, "test path"), str(root))

    def test_cmd_path_rejects_quotes_controls_and_wildcards(self):
        root = str(Path.cwd())
        for bad in (
            root + '"name', root + "\r\nname", root + "*name", root + "?name",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(uninstall.UninstallError):
                    uninstall._cmd_path(bad, "test path")

    def test_cmd_registry_accepts_uuid_and_rejects_other_hives(self):
        value = r"HKCU\Software\SandglassTests\abc\Run"
        self.assertEqual(uninstall._cmd_registry(value, "registry"), value)
        for bad in (r"HKLM\Software\Sandglass", r"HKCU:\Software\Sandglass",
                    r"HKCU\Software\Sandglass\..\Run", r"HKCU\Software\Sandglass\x?y"):
            with self.subTest(bad=bad):
                with self.assertRaises(uninstall.UninstallError):
                    uninstall._cmd_registry(bad, "registry")

    def test_run_value_ownership_requires_exact_installed_executable(self):
        executable = Path.cwd() / "Users & (test)" / "Sandglass.exe"
        other = executable.with_name("other.exe")
        self.assertTrue(uninstall._run_value_targets(executable, f'"{executable}" --background'))
        self.assertFalse(uninstall._run_value_targets(executable, f'"{other}"'))
        self.assertFalse(uninstall._run_value_targets(executable, r'"%LOCALAPPDATA%\Sandglass.exe"'))

    def test_state_home_overlap_is_rejected_before_runtime_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "install"
            root.mkdir()
            internal = root / "_internal"
            internal.mkdir()
            names = ["_internal", "Sandglass-owned-paths.json", "Sandglass.exe"]
            with patch("sandglass.paths.meter_home", return_value=internal / "state"), \
                 patch.object(uninstall, "_context", return_value=(root, names, 1)), \
                 patch.object(uninstall, "_mutex_state") as mutex, \
                 patch.object(uninstall, "_stop_observer") as stop, \
                 patch.object(uninstall, "_launch_helper") as helper:
                self.assertEqual(uninstall.main(quiet=True), 2)
                mutex.assert_not_called()
                stop.assert_not_called()
                helper.assert_not_called()

    def test_running_desktop_refuses_before_stop_or_helper(self):
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), [], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall, "_mutex_state", return_value="held"), \
             patch.object(uninstall, "_claim_installation_mutex", return_value=1234), \
             patch.object(uninstall, "_close_handles"), \
             patch.object(uninstall, "_request_desktop_quit", return_value="held"), \
             patch.object(uninstall, "_stop_observer") as stop, \
             patch.object(uninstall, "_launch_helper") as helper:
            self.assertEqual(uninstall.main(quiet=True), 9)
            stop.assert_not_called()
            helper.assert_not_called()

    def test_confirmation_rechecks_desktop_mutex_before_quit(self):
        user32 = type("User32", (), {"MessageBoxW": lambda *args: 6})()
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), [], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall.os, "name", "nt"), \
             patch.object(uninstall, "_mutex_state", side_effect=["free", "held"]), \
             patch.object(uninstall, "_claim_installation_mutex", return_value=1234), \
             patch.object(uninstall, "_close_handles"), \
             patch.object(uninstall.ctypes, "windll", type("Windll", (), {"user32": user32})()), \
             patch.object(uninstall, "_request_desktop_quit", return_value="held") as quit_request, \
             patch.object(uninstall, "_stop_observer") as stop:
            self.assertEqual(uninstall.main(quiet=False), 9)
            quit_request.assert_called_once()
            stop.assert_not_called()

    def test_running_desktop_can_gracefully_quit_before_stop(self):
        calls = []
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), ["Sandglass.exe"], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall, "_mutex_state", side_effect=["held", "held", "free"]), \
             patch.object(uninstall, "_claim_installation_mutex",
                          side_effect=lambda: calls.append("claim") or 1234), \
             patch.object(uninstall, "_close_handles"), \
             patch.object(uninstall, "_request_desktop_quit",
                          side_effect=lambda: calls.append("quit") or "free") as quit_request, \
             patch.object(uninstall, "_stop_observer",
                          side_effect=lambda: calls.append("stop") or True), \
             patch.object(uninstall, "_launch_helper",
                          side_effect=lambda *args, **kwargs: calls.append("helper")) as helper:
            self.assertEqual(uninstall.main(quiet=True), 0)
            quit_request.assert_called_once()
            helper.assert_called_once_with(
                Path("C:/install"), ["Sandglass.exe"], 1,
                installation_handle=1234,
            )
        self.assertEqual(calls, ["claim", "quit", "stop", "helper"])

    def test_mutation_dropping_desktop_quit_fails_called_assertion(self):
        """A mutant that skips the authenticated quit cannot satisfy the gate."""
        source = inspect.getsource(uninstall.main)
        mutant_source = source.replace(
            "state = _request_desktop_quit()",
            "state = \"free\"",
            1,
        )
        self.assertNotEqual(mutant_source, source)
        namespace = dict(vars(uninstall))
        exec(compile(mutant_source, "<uninstall-main-mutant>", "exec"), namespace)
        mutant_main = namespace["main"]
        calls = []
        namespace.update({
            "_context": lambda: (Path("C:/install"), ["Sandglass.exe"], 1),
            "_state_home_for_uninstall": lambda *args: None,
            "_mutex_state": lambda name: "held" if name == uninstall.DESKTOP_MUTEX else "free",
            "_claim_installation_mutex": lambda: calls.append("claim") or 0,
            "_request_desktop_quit": lambda: calls.append("quit") or "free",
            "_stop_observer": lambda: calls.append("stop") or True,
            "_launch_helper": lambda *args, **kwargs: calls.append("helper"),
        })
        self.assertEqual(mutant_main(quiet=True), 0)
        with self.assertRaises(AssertionError):
            self.assertIn("quit", calls)

    def test_unknown_desktop_mutex_is_fail_closed(self):
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), [], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall, "_mutex_state", return_value="unknown"), \
             patch.object(uninstall, "_stop_observer") as stop:
            self.assertEqual(uninstall.main(quiet=True), 10)
            stop.assert_not_called()

    def test_observer_stop_failure_is_before_helper(self):
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), [], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall, "_mutex_state", side_effect=["free", "free", "free"]), \
             patch.object(uninstall, "_claim_installation_mutex", return_value=1234), \
             patch.object(uninstall, "_close_handles"), \
             patch.object(uninstall, "_stop_observer", return_value=False), \
             patch.object(uninstall, "_launch_helper") as helper:
            self.assertEqual(uninstall.main(quiet=True), 6)
            helper.assert_not_called()

    def test_success_starts_helper_only_after_both_mutexes_are_free(self):
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), ["Sandglass.exe"], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall, "_mutex_state", side_effect=["free", "free", "free"]) as state, \
             patch.object(uninstall, "_claim_installation_mutex", return_value=1234), \
             patch.object(uninstall, "_close_handles"), \
             patch.object(uninstall, "_stop_observer", return_value=True), \
             patch.object(uninstall, "_launch_helper") as helper:
            self.assertEqual(uninstall.main(quiet=True), 0)
            self.assertEqual(state.call_args_list[0].args, (uninstall.DESKTOP_MUTEX,))
            self.assertEqual(state.call_args_list[1].args, (uninstall.DESKTOP_MUTEX,))
            self.assertEqual(state.call_args_list[2].args, (uninstall.OBSERVER_MUTEX,))
            helper.assert_called_once_with(
                Path("C:/install"), ["Sandglass.exe"], 1,
                installation_handle=1234,
            )

    def test_confirmation_cancel_has_no_stop_or_helper_side_effect(self):
        user32 = type("User32", (), {"MessageBoxW": lambda *args: 7})()
        with patch.object(uninstall, "_context", return_value=(Path("C:/install"), [], 1)), \
             patch.object(uninstall, "_state_home_for_uninstall"), \
             patch.object(uninstall.os, "name", "nt"), \
             patch.object(uninstall, "_mutex_state", return_value="free"), \
             patch.object(uninstall.ctypes, "windll", type("Windll", (), {"user32": user32})()), \
             patch.object(uninstall, "_stop_observer") as stop, \
             patch.object(uninstall, "_launch_helper") as helper:
            self.assertEqual(uninstall.main(quiet=False), 0)
            stop.assert_not_called()
            helper.assert_not_called()

    def test_helper_isolation_guard_rejects_hardcoded_hive_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "mutated.cmd"
            script.write_text(
                uninstall._cmd_script([uninstall.PRODUCT_MANIFEST, "Sandglass.exe"])
                .replace("%SANDGLASS_PRODUCT_KEY%", r"HKCU\Software\Sandglass"),
                encoding="ascii",
            )
            with self.assertRaises(AssertionError):
                self._assert_helper_isolated(script, (Path(r"D:\Sandglass"),))

    @WINDOWS
    def test_claim_uninstall_mutexes_rejects_existing_runtime(self):
        token = uuid.uuid4().hex
        names = (
            rf"Local\SandglassTests.Installation.{token}",
            rf"Local\SandglassTests.Desktop.{token}",
            rf"Local\SandglassTests.Observer.{token}",
        )
        with patch.object(uninstall, "INSTALLATION_MUTEX", names[0]), \
             patch.object(uninstall, "DESKTOP_MUTEX", names[1]), \
             patch.object(uninstall, "OBSERVER_MUTEX", names[2]):
            handles = uninstall._claim_uninstall_mutexes()
            try:
                self.assertEqual(len(handles), 3)
                with self.assertRaises(uninstall.UninstallError):
                    uninstall._claim_uninstall_mutexes()
            finally:
                uninstall._close_handles(handles)

    def test_claim_uninstall_mutexes_orders_installation_before_runtime(self):
        with patch.object(uninstall, "_claim_mutexes", return_value=(1, 2, 3)) as claim:
            self.assertEqual(uninstall._claim_uninstall_mutexes(), (1, 2, 3))
        claim.assert_called_once_with(
            (uninstall.INSTALLATION_MUTEX, uninstall.DESKTOP_MUTEX, uninstall.OBSERVER_MUTEX)
        )

    @WINDOWS
    def test_cmd_helper_removes_owned_paths_and_preserves_state_registry_and_shortcut_extras(self):
        with tempfile.TemporaryDirectory(prefix="sandglass-uninstall-&(%!) ") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root, product_extra=True)
            try:
                result = self._run_helper(root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"],
                                          base, desktop, programs, keys,
                                          state=state,
                                          delete_product_key="0")
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((state / "uninstall-result.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "success")
                self.assertEqual(receipt["phase"], "complete")
                self.assertFalse((root / "Sandglass.exe").exists())
                self.assertFalse((root / uninstall.PRODUCT_MANIFEST).exists())
                self.assertTrue((root / "owner-extra.txt").exists())
                self.assertTrue((state / "state-marker.json").exists())
                self.assertTrue(root.exists())
                self.assertFalse((desktop / "Sandglass.lnk").exists())
                self.assertTrue((programs / "Sandglass" / "owner-shortcut.txt").exists())
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["product"][5:]) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "owner_value")[0], "keep")
                    with self.assertRaises(FileNotFoundError):
                        winreg.QueryValueEx(key, "InstallDir")
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["run"][5:]) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "other-startup")[0], "keep")
                    with self.assertRaises(FileNotFoundError):
                        winreg.QueryValueEx(key, "sandglass")
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["unrelated"][5:]) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "marker")[0], "keep")
            finally:
                cleanup_registry()

    @WINDOWS
    def test_mutated_exit_b_returns_nonzero_even_after_successful_cleanup(self):
        """The regression mutant must not turn a failed cmd frame into PASS."""
        with tempfile.TemporaryDirectory(prefix="sandglass-uninstall-exit-mutant-") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root)
            original = uninstall._cmd_script([uninstall.PRODUCT_MANIFEST, "Sandglass.exe"])
            mutant = original.replace("exit %sg_rc%", "exit /b %sg_rc%")
            self.assertEqual(original.count("exit %sg_rc%"), 1)
            self.assertEqual(mutant.count("exit /b %sg_rc%"), 1)
            try:
                result = self._run_helper(
                    root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"], base,
                    desktop, programs, keys, state=state,
                    script_text=mutant, inspect=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertTrue((state / "uninstall-result.json").exists())
                self.assertFalse((root / "Sandglass.exe").exists())
            finally:
                cleanup_registry()

    @WINDOWS
    def test_cmd_helper_manifest_change_has_nonzero_exit_and_no_deletion(self):
        with tempfile.TemporaryDirectory(prefix="sandglass-uninstall-manifest-") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root)
            try:
                snapshot = state / "manifest.snapshot"
                snapshot.write_bytes((root / uninstall.PRODUCT_MANIFEST).read_bytes())
                (root / uninstall.PRODUCT_MANIFEST).write_text(
                    json.dumps({
                        "schema": 1,
                        "paths": [uninstall.PRODUCT_MANIFEST, "Sandglass.exe", "LICENSE"],
                    }),
                    encoding="utf-8",
                )
                result = self._run_helper(
                    root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"], base,
                    desktop, programs, keys, snapshot=snapshot, state=state,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((state / "uninstall-result.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["phase"], "manifest-changed")
                self.assertTrue((root / "Sandglass.exe").exists())
                self.assertTrue((root / uninstall.PRODUCT_MANIFEST).exists())
            finally:
                cleanup_registry()

    @WINDOWS
    def test_mutated_recursive_root_rmdir_removes_owner_extra(self):
        """The recursive-root mutant is observable on a disposable fixture."""
        with tempfile.TemporaryDirectory(prefix="sandglass-uninstall-rmdir-mutant-") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root)
            original = uninstall._cmd_script([uninstall.PRODUCT_MANIFEST, "Sandglass.exe"])
            mutant = original.replace(
                'rmdir /q "%SANDGLASS_ROOT%"',
                'rmdir /s /q "%SANDGLASS_ROOT%"',
            )
            self.assertNotEqual(mutant, original)
            try:
                result = self._run_helper(
                    root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"], base,
                    desktop, programs, keys, state=state,
                    script_text=mutant, inspect=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                # This is the invariant the real helper protects: changing
                # the final rmdir to recursive destroys owner content.
                self.assertFalse((root / "owner-extra.txt").exists())
                self.assertFalse(root.exists())
                with self.assertRaises(AssertionError):
                    self.assertTrue((root / "owner-extra.txt").exists(),
                                    "recursive-root mutation bypassed owner preservation")
            finally:
                cleanup_registry()

    @staticmethod
    def _open_without_delete_sharing(path: Path) -> int:
        """Hold a fixture image open so cmd's delete gate must wait."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateFileW(
            str(path), 0x80000000, 0x00000003, None, 3, 0x00000080, None
        )
        if not handle or int(handle) == -1:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    @staticmethod
    def _close_native_handle(handle: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(ctypes.c_void_p(handle))

    @WINDOWS
    def test_launch_helper_runs_production_launcher_with_isolated_registry(self):
        with tempfile.TemporaryDirectory(prefix="sandglass-launcher-(&%) ") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root)
            token = uuid.uuid4().hex
            mutex_names = (
                rf"Local\SandglassTests.Launch.Installation.{token}",
                rf"Local\SandglassTests.Launch.Desktop.{token}",
                rf"Local\SandglassTests.Launch.Observer.{token}",
            )
            captured: dict[str, object] = {}
            real_popen = uninstall.subprocess.Popen

            def guarded_popen(command, *args, **kwargs):
                environment = dict(kwargs["env"])
                script = Path(environment["SANDGLASS_HELPER"])
                self._assert_helper_isolated(
                    script,
                    (Path(r"D:\Sandglass"), Path(r"D:\Sandglass-codex-uninstall-main-mode")),
                )
                system32 = Path(os.environ["SystemRoot"]) / "System32"
                self.assertEqual(
                    os.path.normcase(environment["SANDGLASS_REG"]),
                    os.path.normcase(str(system32 / "reg.exe")),
                )
                self.assertEqual(environment["SANDGLASS_UNINSTALL_KEY"], keys["uninstall"])
                self.assertEqual(environment["SANDGLASS_PRODUCT_KEY"], keys["product"])
                self.assertEqual(environment["SANDGLASS_RUN_KEY"], keys["run"])
                expected_paths = {
                    "SANDGLASS_ROOT": root,
                    "SANDGLASS_EXE": root / "Sandglass.exe",
                    "SANDGLASS_RECEIPT": state / "uninstall-result.json",
                    "SANDGLASS_DESKTOP": desktop,
                    "SANDGLASS_PROGRAMS": programs,
                    "SANDGLASS_MANIFEST": root / uninstall.PRODUCT_MANIFEST,
                }
                for variable, expected in expected_paths.items():
                    self.assertEqual(Path(environment[variable]), expected)
                    self.assertTrue(Path(environment[variable]).resolve(strict=False).is_relative_to(
                        base.resolve(strict=False)
                    ))
                for variable in (
                    "SANDGLASS_RECEIPT_TMP", "SANDGLASS_MANIFEST_SNAPSHOT",
                    "SANDGLASS_HELPER",
                ):
                    self.assertTrue(Path(environment[variable]).resolve(strict=False).is_relative_to(
                        base.resolve(strict=False)
                    ))
                captured.update(command=command, startupinfo=kwargs["startupinfo"], env=environment)
                return real_popen(command, *args, **kwargs)

            patches = {
                "INSTALLATION_MUTEX": mutex_names[0],
                "DESKTOP_MUTEX": mutex_names[1],
                "OBSERVER_MUTEX": mutex_names[2],
                "UNINSTALL_KEY": keys["uninstall"][5:],
                "PRODUCT_KEY": keys["product"][5:],
                "RUN_KEY": keys["run"][5:],
            }
            image_handle: int | None = None
            try:
                image_handle = self._open_without_delete_sharing(root / "Sandglass.exe")
                with patch.multiple(uninstall, **patches), \
                     patch("sandglass.paths.meter_home", return_value=state), \
                     patch.object(uninstall, "_known_folder_path",
                                  side_effect=lambda csidl, label: desktop if csidl == 0x10 else programs), \
                     patch.object(uninstall.subprocess, "Popen", side_effect=guarded_popen):
                    child = uninstall._launch_helper(
                        root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"], os.getpid()
                    )
                try:
                    # The child must remain alive while the no-delete-share
                    # handle is open, and its inherited mutex must be visible.
                    time.sleep(0.25)
                    self.assertIsNone(child.poll())
                    for mutex_name in mutex_names:
                        self.assertEqual(uninstall._mutex_state(mutex_name), "held")
                finally:
                    self._close_native_handle(image_handle)
                    image_handle = None
                self.assertEqual(child.wait(timeout=30), 0)
                self.assertIn("/d /q /v:off /c", captured["command"])
                startup = captured["startupinfo"]
                self.assertEqual(len(startup.lpAttributeList["handle_list"]), 3)
                self.assertTrue(all(isinstance(handle, int)
                                    for handle in startup.lpAttributeList["handle_list"]))
                for mutex_name in mutex_names:
                    self.assertEqual(uninstall._mutex_state(mutex_name), "free")
                receipt = json.loads((state / "uninstall-result.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "success")
                self.assertFalse((root / "Sandglass.exe").exists())
                self.assertFalse((root / uninstall.PRODUCT_MANIFEST).exists())
                self.assertTrue((root / "owner-extra.txt").exists())
                self.assertTrue((state / "state-marker.json").exists())
                self.assertFalse((desktop / "Sandglass.lnk").exists())
                self.assertTrue((programs / "Sandglass" / "owner-shortcut.txt").exists())
                import winreg

                with self.assertRaises(FileNotFoundError):
                    winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["product"][5:])
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["run"][5:]) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "other-startup")[0], "keep")
                    with self.assertRaises(FileNotFoundError):
                        winreg.QueryValueEx(key, "sandglass")
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keys["unrelated"][5:]) as key:
                    self.assertEqual(winreg.QueryValueEx(key, "marker")[0], "keep")
            finally:
                if image_handle is not None:
                    # Assertion failures before the release must not leave the
                    # helper spinning after this test returns.
                    try:
                        self._close_native_handle(image_handle)
                    except OSError:
                        pass
                    image_handle = None
                if 'child' in locals() and child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                cleanup_registry()

    @WINDOWS
    def test_mutation_dropping_inherited_handles_fails_held_assertion_and_reaps(self):
        """A child without all inherited guards must be observable as free."""
        with tempfile.TemporaryDirectory(prefix="sandglass-launcher-handle-mutant-") as tmp:
            base = Path(tmp)
            root, state, desktop, programs = self._make_install_fixture(base)
            keys, cleanup_registry = self._registry_fixture(root)
            token = uuid.uuid4().hex
            mutex_names = (
                rf"Local\SandglassTests.HandleMutant.Installation.{token}",
                rf"Local\SandglassTests.HandleMutant.Desktop.{token}",
                rf"Local\SandglassTests.HandleMutant.Observer.{token}",
            )
            real_popen = uninstall.subprocess.Popen
            child = None
            image_handle: int | None = None

            def drop_handles_popen(command, *args, **kwargs):
                environment = dict(kwargs["env"])
                script = Path(environment["SANDGLASS_HELPER"])
                self._assert_helper_isolated(
                    script,
                    (Path(r"D:\Sandglass"), Path(r"D:\Sandglass-codex-uninstall-main-mode")),
                )
                # Mutation: production passes a handle list, but this mutant
                # drops it before CreateProcess.  The runtime-held assertion
                # below must fail, while finally blocks still reap the child.
                kwargs["startupinfo"].lpAttributeList = {"handle_list": []}
                return real_popen(command, *args, **kwargs)

            patches = {
                "INSTALLATION_MUTEX": mutex_names[0],
                "DESKTOP_MUTEX": mutex_names[1],
                "OBSERVER_MUTEX": mutex_names[2],
                "UNINSTALL_KEY": keys["uninstall"][5:],
                "PRODUCT_KEY": keys["product"][5:],
                "RUN_KEY": keys["run"][5:],
            }
            try:
                image_handle = self._open_without_delete_sharing(root / "Sandglass.exe")
                with patch.multiple(uninstall, **patches), \
                     patch("sandglass.paths.meter_home", return_value=state), \
                     patch.object(uninstall, "_known_folder_path",
                                  side_effect=lambda csidl, label: desktop if csidl == 0x10 else programs), \
                     patch.object(uninstall.subprocess, "Popen", side_effect=drop_handles_popen):
                    child = uninstall._launch_helper(
                        root, [uninstall.PRODUCT_MANIFEST, "Sandglass.exe"], os.getpid()
                    )
                try:
                    time.sleep(0.25)
                    self.assertIsNone(child.poll())
                    with self.assertRaises(AssertionError):
                        self.assertEqual(uninstall._mutex_state(mutex_names[0]), "held")
                finally:
                    self._close_native_handle(image_handle)
                    image_handle = None
                self.assertEqual(child.wait(timeout=30), 0)
            finally:
                if image_handle is not None:
                    try:
                        self._close_native_handle(image_handle)
                    except OSError:
                        pass
                    image_handle = None
                if child is not None and child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                cleanup_registry()

    @WINDOWS
    def test_no_reparse_fails_before_helper_targets_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "install"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "keep").write_text("keep", encoding="utf-8")
            (root / "Sandglass.exe").write_bytes(b"MZ")
            (root / uninstall.PRODUCT_MANIFEST).write_text(
                json.dumps({
                    "schema": 1,
                    "paths": [uninstall.PRODUCT_MANIFEST, "Sandglass.exe", "_internal"],
                }),
                encoding="utf-8",
            )
            link = root / "_internal"
            cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
            create = subprocess.run(
                f'/d /q /c mklink /J "{link}" "{outside}"', executable=str(cmd),
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            with self.assertRaises(uninstall.UninstallError):
                uninstall._no_reparse(root)
            self.assertTrue((root / "Sandglass.exe").exists())
            self.assertTrue((outside / "keep").exists())


if __name__ == "__main__":
    unittest.main()
