import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tools.adapter_holdout_acl import _is_dangerously_broad, deny_targets
from tools.generate_adapter_holdout import generate
from tools.prepare_adapter_clean_room import BUNDLE_FILES
from tools.prepare_adapter_holdout_lab import prepare_lab
from tools.run_adapter_holdout_trial import (
    IsolatedSandglass,
    agent_environment,
    build_resume_command,
    initial_prompt,
    load_lab,
    permission_overrides,
    verify_mechanism_bundle,
)


class AdapterHoldoutLabTests(unittest.TestCase):
    @staticmethod
    def _bundle(root: Path) -> Path:
        bundle = root / "bundle"
        for relative in BUNDLE_FILES:
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# frozen {relative.as_posix()}\n", encoding="utf-8")
        return bundle

    def test_lab_keeps_controller_and_state_outside_agent_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            visible = root / "visible"
            generate(visible, root / "truth", seed=101, events=60)
            lab_root = root / "lab"
            prepared = prepare_lab(bundle, visible, lab_root)

            self.assertCountEqual(
                [row["path"] for row in prepared["skill"]],
                [path.as_posix() for path in BUNDLE_FILES],
            )
            self.assertNotIn(prepared["sandglass_home"], prepared["agent_visible_roots"])
            self.assertNotIn(prepared["controller"], prepared["agent_visible_roots"])
            self.assertIn(prepared["sandglass_home"], prepared["agent_hidden_roots"])
            self.assertEqual(load_lab(lab_root)["lab_root"], str(lab_root.resolve()))
            self.assertEqual(
                load_lab(lab_root)["product_url"], "http://127.0.0.1:7740"
            )

    def test_permission_profile_grants_only_work_profile_and_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            visible = root / "visible"
            generate(visible, root / "truth", seed=202, events=60)
            lab = prepare_lab(bundle, visible, root / "lab")

            overrides = "\n".join(permission_overrides(lab))
            self.assertIn('windows.sandbox="elevated"', overrides)
            self.assertIn('":root"="deny"', overrides)
            self.assertIn(json.dumps(lab["agent_work"]), overrides)
            self.assertIn(json.dumps(lab["virtual_user"]), overrides)
            self.assertIn(json.dumps(lab["scratch"]), overrides)
            self.assertNotIn(json.dumps(lab["sandglass_home"]), overrides)
            self.assertNotIn(json.dumps(lab["controller"]), overrides)
            self.assertIn('"127.0.0.1"="allow"', overrides)

            environment = agent_environment(lab)
            self.assertEqual(environment["USERPROFILE"], lab["virtual_user"])
            self.assertNotIn("SANDGLASS_HOME", environment)

            resumed = build_resume_command(lab, "session-123", "owner answer")
            self.assertEqual(resumed[:3], ["codex", "exec", "resume"])
            self.assertIn("session-123", resumed)
            self.assertEqual(resumed[-1], "owner answer")

    def test_owner_context_is_discovery_only_and_withholds_answers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            visible = root / "visible"
            generate(visible, root / "truth", seed=303, events=60)
            lab = prepare_lab(bundle, visible, root / "lab")

            prompt = initial_prompt(lab)
            self.assertEqual(prompt, "执行 Sandglass Adapter Skill。")
            verify_mechanism_bundle(lab)
            owner = json.loads(
                (Path(lab["controller"]) / "owner-context.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(owner["initial"], "")
            self.assertIn("tool_inventory", owner["answer_only_after_agent_asks"])
            self.assertIn("Lattice Relay", owner["answer_only_after_agent_asks"]["tool_inventory"])
            self.assertIn("account_shape", owner["answer_only_after_agent_asks"])
            self.assertIn("tool_roles", owner["answer_only_after_agent_asks"])
            self.assertIn("history_scope", owner["answer_only_after_agent_asks"])

            (Path(lab["agent_work"]) / "SKILL.md").write_text(
                "changed\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "mechanism bundle"):
                verify_mechanism_bundle(lab)

    def test_real_isolated_product_is_visible_only_over_loopback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            visible = root / "visible"
            generate(visible, root / "truth", seed=505, events=60)
            lab = prepare_lab(bundle, visible, root / "lab")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
                server_socket.bind(("127.0.0.1", 0))
                port = server_socket.getsockname()[1]
            lab["product_url"] = f"http://127.0.0.1:{port}"

            with IsolatedSandglass(lab):
                with urllib.request.urlopen(
                    lab["product_url"] + "/api/runtime-diagnostics", timeout=3
                ) as response:
                    payload = json.load(response)
                self.assertEqual(payload["runtime"]["runtime_role"], "web_server")

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                self.assertNotEqual(client.connect_ex(("127.0.0.1", port)), 0)

    def test_acl_plan_hides_state_controller_repo_and_sibling_labs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            visible = root / "visible"
            generate(visible, root / "truth", seed=404, events=60)
            labs = root / "labs"
            selected = prepare_lab(bundle, visible, labs / "selected")
            (labs / "sibling").mkdir()

            with patch("tools.adapter_holdout_acl.Path.home", return_value=root / "home"):
                targets = deny_targets(selected)
            self.assertIn(Path(selected["sandglass_home"]).resolve(), targets)
            self.assertIn(Path(selected["controller"]).resolve(), targets)
            self.assertIn((labs / "sibling").resolve(), targets)
            self.assertNotIn(Path(selected["agent_work"]).resolve(), targets)
            self.assertNotIn(Path(selected["virtual_user"]).resolve(), targets)
            self.assertNotIn(Path(selected["lab_root"]).resolve(), targets)

    def test_acl_guard_rejects_broad_targets(self):
        self.assertTrue(_is_dangerously_broad(Path.home()))
        self.assertTrue(_is_dangerously_broad(Path("D:/")))


if __name__ == "__main__":
    unittest.main()
