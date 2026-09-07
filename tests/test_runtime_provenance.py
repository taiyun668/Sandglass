import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from sandglass import runtime_provenance
from sandglass.resources import SOURCE_ROOT, WEB_DIR, source_native_bridge_path
from sandglass import runtime_provenance as provenance


ROOT = Path(__file__).resolve().parents[1]


class RuntimeProvenanceTests(unittest.TestCase):
    @staticmethod
    def _runtime_fixture(root: Path) -> Path:
        runtime = root / "webview-runtime"
        runtime.mkdir()
        for name in provenance._NATIVE_RUNTIME_FILES:
            (runtime / name).write_bytes(name.encode("ascii"))
        return runtime

    def test_identity_describes_actual_resolved_assets_without_raw_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime_fixture(Path(tmp))
            bridge = source_native_bridge_path()
            self.assertIsNotNone(bridge)
            mode, content = "source", bridge.read_bytes()

            identity = provenance.build_runtime_identity(
                source_root=SOURCE_ROOT,
                web_index=WEB_DIR / "index.html",
                native_runtime=runtime,
                panel_bridge=bridge,
                bridge_content=content,
                bridge_mode=mode,
            )
        rendered = json.dumps(identity)

        self.assertEqual(identity["schema"], 2)
        self.assertEqual(identity["panel_bridge"]["mode"], "source")
        self.assertTrue(identity["panel_bridge"]["contract_valid"])
        self.assertEqual(
            set(identity["native_runtime"]["files"]),
            set(provenance._NATIVE_RUNTIME_FILES),
        )
        self.assertNotIn(str(ROOT), rendered)
        expected_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        self.assertEqual(identity["source_root"]["git_head"], expected_head)

    def test_first_successful_bridge_call_is_persisted_in_sandglass_home(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(provenance, "_ACTIVE", {}), \
                patch.dict(os.environ, {"SANDGLASS_HOME": tmp}, clear=False):
            identity = {"schema": 1, "source_root": {}, "web_index": {},
                        "native_runtime": {}, "panel_bridge": {}}
            provenance.record_runtime_identity(identity)
            provenance.record_api_bridge_result(200)

            stored = json.loads(provenance.provenance_path().read_text(encoding="utf-8"))

        self.assertEqual(stored["api_bridge"]["success_count"], 1)
        self.assertEqual(stored["api_bridge"]["last_status"], 200)

    def test_web_server_identity_does_not_borrow_desktop_runtime_evidence(self):
        identity = provenance.build_web_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
        )
        self.assertEqual(identity["runtime_role"], "web_server")
        self.assertNotIn("panel_bridge", identity)
        self.assertTrue(provenance.runtime_identity_matches(identity, identity))

    def test_packaged_build_identity_replaces_missing_git_directory(self):
        packaged = {
            "schema": 1,
            "git_head": "b" * 40,
            "git_dirty": False,
            "project_version": "1.2.3",
            "build_id": "c" * 64,
            "resources": {},
        }
        with patch.object(
            provenance, "_packaged_build_provenance", return_value=packaged
        ):
            identity = provenance.build_web_runtime_identity(
                source_root=Path("Z:/packaged/runtime"),
                web_index=WEB_DIR / "index.html",
            )

        self.assertEqual(identity["source_root"]["git_head"], "b" * 40)
        self.assertEqual(identity["source_root"]["build_id"], "c" * 64)
        self.assertEqual(identity["source_root"]["project_version"], "1.2.3")
        self.assertTrue(identity["source_root"]["packaged_provenance"])

    def test_native_runtime_content_change_breaks_identity_at_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self._runtime_fixture(root)
            bridge = root / "PanelShell.js"
            bridge.write_text("bridge", encoding="utf-8")
            kwargs = {
                "source_root": root,
                "web_index": root / "index.html",
                "native_runtime": runtime,
                "panel_bridge": bridge,
                "bridge_content": b"bridge",
                "bridge_mode": "packaged",
            }
            kwargs["web_index"].write_text("page", encoding="utf-8")
            expected = provenance.build_runtime_identity(**kwargs)
            original = (runtime / provenance._NATIVE_RUNTIME_FILES[0]).read_bytes()
            try:
                (runtime / provenance._NATIVE_RUNTIME_FILES[0]).write_bytes(original + b"changed")
                actual = provenance.build_runtime_identity(**kwargs)
            finally:
                (runtime / provenance._NATIVE_RUNTIME_FILES[0]).write_bytes(original)

        self.assertFalse(provenance.runtime_identity_matches(expected, actual))
        self.assertNotIn(str(ROOT), json.dumps(expected))

    def test_source_content_change_breaks_identity_at_same_path_and_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "sandglass" / "web"
            package.mkdir(parents=True)
            (root / "sandglass" / "meter.py").write_text("VALUE = 1\n", encoding="utf-8")
            (package / "index.html").write_text("page", encoding="utf-8")
            i18n = package / "i18n.js"
            i18n.write_text("const messages = {};\n", encoding="utf-8")
            with patch.object(provenance, "_git_head", return_value="a" * 40):
                expected = provenance.build_web_runtime_identity(
                    source_root=root, web_index=package / "index.html"
                )
                i18n.write_text(
                    "const messages = {changed: true};\n", encoding="utf-8"
                )
                actual = provenance.build_web_runtime_identity(
                    source_root=root, web_index=package / "index.html"
                )

        self.assertEqual(expected["source_root"]["git_head"], actual["source_root"]["git_head"])
        self.assertFalse(provenance.runtime_identity_matches(expected, actual))
        self.assertNotIn(str(root), json.dumps(expected))

    def test_schema_one_evidence_fails_closed(self):
        identity = provenance.build_web_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=WEB_DIR / "index.html",
        )
        legacy = {**identity, "schema": 1}
        self.assertFalse(provenance.runtime_identity_matches(legacy, identity))

    def test_tests_do_not_read_live_assets_through_old_hardcoded_paths(self):
        forbidden = (
            'ROOT / "native" / "SandglassShell" / "Panel' + 'Shell.js"',
            'ROOT / "sandglass" / "web" / "index' + '.html"',
        )
        offenders = []
        for test_file in (ROOT / "tests").glob("test_*.py"):
            text = test_file.read_text(encoding="utf-8")
            for pattern in forbidden:
                if pattern in text:
                    offenders.append(f"{test_file.name}: {pattern}")
        self.assertEqual(offenders, [])

    def test_current_test_process_is_detected_as_running(self):
        self.assertTrue(provenance.process_is_running(os.getpid()))
        self.assertFalse(provenance.process_is_running(0))


class ProvenanceWriteTests(unittest.TestCase):
    """The file the audit reads is this process identifying itself."""

    def test_a_provenance_write_it_could_not_do_is_reported_rather_than_dropped(self):
        """A missing runtime-provenance.json used to mean this process had not
        identified itself.

        `_write` caught OSError and returned. `record_runtime_identity` still
        updated `_ACTIVE`, so in-process reads looked healthy while the file
        a second launch and the audit use stayed missing or stale. A later
        launch then warned that a different version was already running.
        Still not raised: this is bookkeeping beside a process that must
        keep starting.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            provenance, "_ACTIVE", {}
        ), patch.dict(os.environ, {"SANDGLASS_HOME": tmp}, clear=False):
            identity = {
                "schema": 1,
                "source_root": {},
                "web_index": {},
                "native_runtime": {},
                "panel_bridge": {},
            }
            provenance.record_runtime_identity(identity)
            path = provenance.provenance_path()
            original = path.read_text(encoding="utf-8")
            recorded = []
            real_write = Path.write_text

            def write_text(self, *args, **kwargs):
                if self.suffix == ".tmp":
                    raise OSError("disk full")
                return real_write(self, *args, **kwargs)

            with patch.object(Path, "write_text", write_text), patch(
                "sandglass.diagnostics.record_component_failure",
                lambda name, exc: recorded.append(name),
            ):
                provenance.record_runtime_identity({**identity, "marker": "new"})

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(recorded, ["runtime_provenance_write"])
            self.assertNotIn("marker", provenance.runtime_provenance())


if __name__ == "__main__":
    unittest.main()


class PackagedManifestConsistencyTests(unittest.TestCase):
    """A build_id of the right length is not a build_id.

    tools/windows_release.py already requires it to be the digest of the rest of
    the manifest and refuses a bundle where it is not. The runtime measured only
    its length, so any 64 hex characters were accepted and a hand-edited
    git_head kept a build_id that no longer described it. This proves internal
    consistency, not authenticity -- only a signature does that, and the release
    is not signed yet.
    """

    def _manifest(self, **overrides):
        value = {
            "schema": 1,
            "git_head": "a" * 40,
            "git_dirty": False,
            "project_version": "0.1.0",
            "resources": {},
        }
        value.update(overrides)
        value["build_id"] = runtime_provenance._provenance_build_id(value)
        return value

    def _with_manifest(self, value):
        target = Path(runtime_provenance.__file__).resolve().parent
        return mock.patch.object(
            runtime_provenance.Path, "read_text",
            lambda self, **k: json.dumps(value) if self.parent == target else "",
        )

    def test_a_consistent_manifest_is_accepted(self):
        value = self._manifest()
        with self._with_manifest(value):
            self.assertEqual(
                runtime_provenance._packaged_build_provenance()["git_head"],
                "a" * 40,
            )

    def test_an_edited_field_no_longer_matches_its_own_build_id(self):
        value = self._manifest()
        value["git_head"] = "b" * 40          # same length, digest now stale
        with self._with_manifest(value):
            self.assertEqual(runtime_provenance._packaged_build_provenance(), {})

    def test_sixty_four_hex_characters_are_not_enough(self):
        value = self._manifest()
        value["build_id"] = "c" * 64
        with self._with_manifest(value):
            self.assertEqual(runtime_provenance._packaged_build_provenance(), {})

    def test_the_runtime_rule_is_the_build_rule(self):
        """Both sides must compute it the same way, or a bundle passes one gate
        and fails the other."""
        import hashlib

        value = self._manifest()
        unsigned = {k: v for k, v in value.items() if k != "build_id"}
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(value["build_id"], expected)

