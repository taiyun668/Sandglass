"""Generate immutable, non-secret source identity for packaged artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path


RESOURCE_PATHS = {
    "web_index": Path("sandglass/web/index.html"),
    "web_i18n": Path("sandglass/web/i18n.js"),
    "panel_bridge": Path("native/SandglassShell/bin/PanelShell.js"),
}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, encoding="utf-8"
    ).strip()


def generate(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    head = _git(root, "rev-parse", "HEAD")
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ValueError("Git HEAD is not a full lowercase commit id")
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
    if dirty:
        raise ValueError("refusing to package a dirty tracked worktree")
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    resources: dict[str, dict[str, object]] = {}
    for name, relative in RESOURCE_PATHS.items():
        path = root / relative
        payload = path.read_bytes()
        resources[name] = {
            "path": relative.as_posix(),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    identity: dict[str, object] = {
        "schema": 1,
        "git_head": head,
        "git_dirty": False,
        "project_version": str(metadata["project"]["version"]),
        "resources": resources,
    }
    identity["build_id"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.root, args.output)
    print(
        f"PASS build provenance {str(result['git_head'])[:12]} "
        f"{str(result['build_id'])[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
