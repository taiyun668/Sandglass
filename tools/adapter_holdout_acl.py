"""Temporary NTFS read barriers for the synthetic adapter experiment.

Codex's elevated Windows sandbox executes commands as ``CodexSandboxOffline``.
The current CLI permission-profile path does not enforce denied reads on this
machine, so formal trials add explicit, inheritable deny ACEs for that sandbox
identity only.  The controller user remains unaffected and every ACE added by
this module is removed in ``finally``.
"""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import AbstractContextManager
from pathlib import Path


SANDBOX_ACCOUNT = "CodexSandboxOffline"
SYSTEM_POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SYSTEM_ICACLS = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "icacls.exe"


def _powershell(script: str) -> str:
    environment = dict(os.environ)
    system_root = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PSModulePath"] = os.pathsep.join(
        [
            str(Path.home() / "Documents" / "WindowsPowerShell" / "Modules"),
            "C:\\Program Files\\WindowsPowerShell\\Modules",
            str(system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
        ]
    )
    completed = subprocess.run(
        [str(SYSTEM_POWERSHELL), "-NoProfile", "-Command", script],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def resolve_sandbox_sid() -> str:
    script = (
        "$account = New-Object System.Security.Principal.NTAccount('")
    script += SANDBOX_ACCOUNT
    script += "'); $account.Translate([System.Security.Principal.SecurityIdentifier]).Value"
    sid = _powershell(script).splitlines()[-1].strip()
    if not sid.startswith("S-1-"):
        raise RuntimeError(f"unexpected sandbox SID: {sid!r}")
    return sid


def _is_dangerously_broad(path: Path) -> bool:
    resolved = path.resolve()
    if resolved.parent == resolved:
        return True
    home = Path.home().resolve()
    return resolved in {home, home.parent, Path(resolved.anchor)}


def deny_targets(lab: dict) -> list[Path]:
    """Return exact directories that could contaminate the selected trial."""

    current_lab = Path(lab["lab_root"]).resolve()
    lab_parent = current_lab.parent
    targets: set[Path] = {
        Path(lab["sandglass_home"]).resolve(),
        Path(lab["controller"]).resolve(),
        Path(__file__).resolve().parents[1],
    }
    for sibling in lab_parent.iterdir():
        if sibling.is_dir() and sibling.resolve() != current_lab:
            targets.add(sibling.resolve())
    # Machine-specific directories that must never be read during a trial. They
    # were hard-coded to this developer's own project folders, which put two
    # local project names into a public repository for a guard that is a no-op
    # anywhere else. Same guard, supplied by the machine that needs it:
    # SANDGLASS_HOLDOUT_DENY, os.pathsep-separated.
    for raw in os.environ.get("SANDGLASS_HOLDOUT_DENY", "").split(os.pathsep):
        candidate = Path(raw.strip())
        if raw.strip() and candidate.exists():
            targets.add(candidate.resolve())

    isolated = Path("D:/isolated")
    if isolated.is_dir():
        for candidate in isolated.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.resolve() in {lab_parent, current_lab}:
                continue
            else:
                targets.add(candidate.resolve())

    home = Path.home()
    for candidate in (
        home / ".claude",
        home / ".codex",
        home / ".grok",
        home / "Documents" / "codex-auth",
        home / "SandglassCleanRoomEvaluator",
    ):
        if candidate.exists():
            targets.add(candidate.resolve())
    for pattern in (
        "sandglass-codex-clean-room-*",
        "sandglass-env-first-labs-*",
        "sandglass-owner-question-labs-*",
        "sandglass-skill-labs-*",
    ):
        for candidate in home.glob(pattern):
            if candidate.is_dir():
                targets.add(candidate.resolve())

    ordered = sorted(targets, key=lambda value: str(value).lower())
    broad = [str(path) for path in ordered if _is_dangerously_broad(path)]
    if broad:
        raise RuntimeError(f"refusing broad ACL targets: {broad}")
    return ordered


def _sddl(path: Path) -> str:
    encoded = json.dumps(str(path))
    return _powershell(f"(Get-Acl -LiteralPath {encoded}).Sddl").splitlines()[-1]


def _has_explicit_deny(path: Path, sid: str) -> bool:
    encoded_path = json.dumps(str(path))
    encoded_sid = json.dumps(sid)
    script = (
        f"$sid = New-Object System.Security.Principal.SecurityIdentifier({encoded_sid}); "
        f"$acl = Get-Acl -LiteralPath {encoded_path}; "
        "$found = $acl.Access | Where-Object { "
        "$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value "
        "-eq $sid.Value -and $_.AccessControlType -eq 'Deny' -and -not $_.IsInherited }; "
        "if ($found) { 'true' } else { 'false' }"
    )
    return _powershell(script).splitlines()[-1].strip().lower() == "true"


class SandboxAclGuard(AbstractContextManager["SandboxAclGuard"]):
    def __init__(self, lab: dict):
        self.lab = lab
        self.sid = resolve_sandbox_sid()
        self.targets = deny_targets(lab)
        self.added: list[Path] = []

    def __enter__(self) -> "SandboxAclGuard":
        controller = Path(self.lab["controller"])
        snapshot = {
            "schema": 1,
            "sandbox_account": SANDBOX_ACCOUNT,
            "sandbox_sid": self.sid,
            "targets": [
                {
                    "path": str(path),
                    "sddl_before": _sddl(path),
                    "preexisting_explicit_deny": _has_explicit_deny(path, self.sid),
                }
                for path in self.targets
            ],
        }
        (controller / "acl-before.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        identity = f"*{self.sid}"
        try:
            for row, path in zip(snapshot["targets"], self.targets, strict=True):
                if row["preexisting_explicit_deny"]:
                    continue
                completed = subprocess.run(
                    [str(SYSTEM_ICACLS), str(path), "/deny", f"{identity}:(OI)(CI)(RX)"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"failed to deny sandbox read on {path}: "
                        + (completed.stderr.strip() or completed.stdout.strip())
                    )
                if not _has_explicit_deny(path, self.sid):
                    raise RuntimeError(f"deny ACE did not become effective: {path}")
                self.added.append(path)
        except Exception:
            self._restore()
            raise
        (controller / "acl-active.json").write_text(
            json.dumps(
                {"sandbox_sid": self.sid, "added": [str(path) for path in self.added]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return self

    def _restore(self) -> None:
        identity = f"*{self.sid}"
        errors: list[str] = []
        for path in reversed(self.added):
            completed = subprocess.run(
                [str(SYSTEM_ICACLS), str(path), "/remove:d", identity],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if completed.returncode:
                errors.append(
                    f"{path}: {completed.stderr.strip() or completed.stdout.strip()}"
                )
        self.added.clear()
        if errors:
            raise RuntimeError("failed to restore ACLs: " + "; ".join(errors))

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._restore()
        controller = Path(self.lab["controller"])
        (controller / "acl-restored.json").write_text(
            json.dumps(
                {
                    "sandbox_sid": self.sid,
                    "all_added_denies_removed": True,
                    "exception_during_trial": None if exc_value is None else repr(exc_value),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return False
