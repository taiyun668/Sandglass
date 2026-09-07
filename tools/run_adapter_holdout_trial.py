"""Launch a holdout agent under an explicit least-privilege Codex profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager
from pathlib import Path

from tools.adapter_holdout_acl import SandboxAclGuard


PROFILE_NAME = "sandglass_holdout"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _inline_table(values: dict[str, str]) -> str:
    return "{" + ",".join(
        f"{_toml_string(key)}={_toml_string(value)}"
        for key, value in values.items()
    ) + "}"


def load_lab(lab_root: Path) -> dict:
    path = lab_root.resolve() / "controller" / "lab.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if Path(data["lab_root"]).resolve() != lab_root.resolve():
        raise ValueError("lab manifest root does not match requested lab")
    data.setdefault("product_url", "http://127.0.0.1:7740")
    return data


def permission_overrides(lab: dict) -> list[str]:
    filesystem = _inline_table(
        {
            ":root": "deny",
            ":minimal": "read",
            lab["agent_work"]: "write",
            lab["virtual_user"]: "read",
            lab["scratch"]: "write",
        }
    )
    return [
        'windows.sandbox="elevated"',
        f'default_permissions="{PROFILE_NAME}"',
        f'permissions.{PROFILE_NAME}.description="Synthetic adapter clean room"',
        f"permissions.{PROFILE_NAME}.filesystem={filesystem}",
        f"permissions.{PROFILE_NAME}.network.enabled=true",
        f'permissions.{PROFILE_NAME}.network.domains='
        + _inline_table({"127.0.0.1": "allow", "localhost": "allow"}),
        "features.network_proxy=true",
    ]


def agent_environment(lab: dict) -> dict[str, str]:
    environment = dict(os.environ)
    virtual_user = Path(lab["virtual_user"])
    scratch = Path(lab["scratch"])
    environment.update(
        {
            "USERPROFILE": str(virtual_user),
            "HOME": str(virtual_user),
            "LOCALAPPDATA": str(virtual_user / "AppData" / "Local"),
            "APPDATA": str(virtual_user / "AppData" / "Roaming"),
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "PYTHONUTF8": "1",
        }
    )
    environment.pop("SANDGLASS_HOME", None)
    return environment


def initial_prompt(lab: dict) -> str:
    return "执行 Sandglass Adapter Skill。"


def verify_mechanism_bundle(lab: dict) -> None:
    root = Path(lab["agent_work"])
    actual = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        actual.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    if actual != lab["skill"]:
        raise RuntimeError("agent-visible mechanism bundle no longer matches its manifest")


def product_environment(lab: dict) -> dict[str, str]:
    environment = agent_environment(lab)
    environment["SANDGLASS_HOME"] = lab["sandglass_home"]
    return environment


def product_command(lab: dict) -> list[str]:
    url = urllib.parse.urlsplit(lab["product_url"])
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
        raise ValueError(
            f"product URL must be explicit loopback HTTP: {lab['product_url']}"
        )
    return [
        sys.executable,
        "-m",
        "sandglass",
        "--offline",
        "serve",
        "--host",
        url.hostname,
        "--port",
        str(url.port),
        "--no-browser",
    ]


class IsolatedSandglass(AbstractContextManager["IsolatedSandglass"]):
    """Run the real product against the lab-owned hidden state."""

    def __init__(self, lab: dict, timeout: float = 15.0):
        self.lab = lab
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self.stdout_handle = None
        self.stderr_handle = None

    def __enter__(self) -> "IsolatedSandglass":
        controller = Path(self.lab["controller"])
        self.stdout_handle = (controller / "product-stdout.txt").open(
            "w", encoding="utf-8"
        )
        self.stderr_handle = (controller / "product-stderr.txt").open(
            "w", encoding="utf-8"
        )
        command = product_command(self.lab)
        parsed = urllib.parse.urlsplit(self.lab["product_url"])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            if probe_socket.connect_ex((parsed.hostname, parsed.port)) == 0:
                self._stop()
                raise RuntimeError(
                    "refusing to replace an existing loopback service: "
                    f"{self.lab['product_url']}"
                )
        try:
            self.process = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env=product_environment(self.lab),
                stdout=self.stdout_handle,
                stderr=self.stderr_handle,
                text=True,
                encoding="utf-8",
            )
            deadline = time.monotonic() + self.timeout
            probe = self.lab["product_url"] + "/api/runtime-diagnostics"
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        "isolated Sandglass exited before readiness: "
                        f"{self.process.returncode}"
                    )
                try:
                    with urllib.request.urlopen(probe, timeout=1.0) as response:
                        if response.status == 200:
                            return self
                except OSError:
                    time.sleep(0.1)
            raise TimeoutError(f"isolated Sandglass did not become ready: {probe}")
        except Exception:
            self._stop()
            raise

    def _stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for handle_name in ("stdout_handle", "stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.close()
                setattr(self, handle_name, None)

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._stop()
        return False


def build_command(lab: dict, prompt: str) -> list[str]:
    command = [
        "codex",
        "exec",
        "--ignore-user-config",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "-C",
        lab["agent_work"],
    ]
    for override in permission_overrides(lab):
        command.extend(["-c", override])
    for feature in (
        "hooks",
        "apps",
        "browser_use",
        "computer_use",
        "in_app_browser",
        "memories",
        "multi_agent",
        "plugins",
        "skill_search",
    ):
        command.extend(["--disable", feature])
    command.append(prompt)
    return command


def build_resume_command(lab: dict, session_id: str, prompt: str) -> list[str]:
    command = [
        "codex",
        "exec",
        "resume",
        "--ignore-user-config",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
    ]
    for override in permission_overrides(lab):
        command.extend(["-c", override])
    for feature in (
        "hooks",
        "apps",
        "browser_use",
        "computer_use",
        "in_app_browser",
        "memories",
        "multi_agent",
        "plugins",
        "skill_search",
    ):
        command.extend(["--disable", feature])
    command.extend([session_id, prompt])
    return command


def _artifact_suffix(controller: Path, resume_session: str | None) -> str:
    if not resume_session:
        return ""
    index = 1
    while (controller / f"events-resume-{index:02d}.jsonl").exists():
        index += 1
    return f"-resume-{index:02d}"


def run_trial(
    lab_root: Path,
    *,
    prompt: str | None,
    timeout: int,
    resume_session: str | None = None,
) -> int:
    lab = load_lab(lab_root)
    verify_mechanism_bundle(lab)
    controller = Path(lab["controller"])
    if resume_session:
        if not prompt:
            raise ValueError("a resumed trial requires an explicit owner response")
        command = build_resume_command(lab, resume_session, prompt)
    else:
        command = build_command(lab, prompt or initial_prompt(lab))
    suffix = _artifact_suffix(controller, resume_session)
    with IsolatedSandglass(lab), SandboxAclGuard(lab):
        completed = subprocess.run(
            command,
            cwd=lab["agent_work"],
            env=agent_environment(lab),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    (controller / f"events{suffix}.jsonl").write_text(
        completed.stdout, encoding="utf-8"
    )
    (controller / f"stderr{suffix}.txt").write_text(
        completed.stderr, encoding="utf-8"
    )
    result = {
        "exit_code": completed.returncode,
        "resume_session": resume_session or "",
        "command_argv": command,
        "permission_overrides": permission_overrides(lab),
    }
    (controller / f"launch-result{suffix}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sys.stdout.buffer.write(completed.stdout.encode("utf-8", errors="replace"))
    sys.stderr.buffer.write(completed.stderr.encode("utf-8", errors="replace"))
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lab_root", type=Path)
    parser.add_argument("--prompt")
    parser.add_argument("--resume-session")
    parser.add_argument("--timeout", type=int, default=35 * 60)
    args = parser.parse_args()
    return run_trial(
        args.lab_root,
        prompt=args.prompt,
        timeout=args.timeout,
        resume_session=args.resume_session,
    )


if __name__ == "__main__":
    raise SystemExit(main())
