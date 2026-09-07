"""Stage one synthetic adapter lab without exposing evaluator truth.

The staged agent sees only its frozen three-layer mechanism bundle and a virtual
user profile. Controller metadata, Sandglass state and evaluator truth remain
outside the agent permission profile used by
:mod:`tools.run_adapter_holdout_trial`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from tools.prepare_adapter_clean_room import BUNDLE_FILES


def _file_manifest(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return rows


def _require_empty(path: Path, label: str) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{label} must be absent or empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _installed_products(visible_profile: Path) -> list[str]:
    programs = visible_profile / "AppData" / "Local" / "Programs"
    products: list[str] = []
    for path in sorted(programs.glob("*/install.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        product = str(value.get("product") or "").strip()
        if product and product not in products:
            products.append(product)
    return products


def prepare_lab(skill_bundle: Path, visible_profile: Path, output: Path) -> dict:
    """Create a clean lab and return controller-visible provenance."""

    skill_bundle = skill_bundle.resolve()
    visible_profile = visible_profile.resolve()
    output = output.resolve()
    skill_files = sorted(
        path.relative_to(skill_bundle).as_posix()
        for path in skill_bundle.rglob("*")
        if path.is_file()
    )
    expected_skill_files = sorted(path.as_posix() for path in BUNDLE_FILES)
    if skill_files != expected_skill_files:
        raise ValueError(
            "frozen bundle must contain the exact mechanism files: "
            f"{skill_files}"
        )
    products = _installed_products(visible_profile)
    if not products:
        raise ValueError(f"visible profile has no synthetic installed tools: {visible_profile}")
    _require_empty(output, "lab output")

    work = output / "agent-work"
    virtual_user = output / "virtual-user"
    state = output / "sandglass-home"
    scratch = output / "scratch"
    controller = output / "controller"
    for path in (work, state, scratch, controller):
        path.mkdir(parents=True, exist_ok=True)
    for relative in BUNDLE_FILES:
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_bundle / relative, target)
    shutil.copytree(visible_profile, virtual_user, dirs_exist_ok=True)

    owner_context = {
        "initial": "",
        "answer_only_after_agent_asks": {
            "tool_inventory": (
                "我平时一直使用" + "、".join(products) + "；"
                "这些是这个测试环境里使用的全部 AI 工具。"
            ),
            "account_shape": (
                "这里同时存在单账号与多账号使用，也发生过账号切换；"
                "具体归属仍须以本机证据为准。"
            ),
            "other_tools": "除此之外没有使用其他 AI 工具。",
            "switch_effect": (
                "切换工具记录的成功切换会在下一次请求前使目标账号真正生效；"
                "具体边界仍须由本机执行与用量记录证明。"
            ),
            "shared_auth_state": (
                "同一厂商的桌面端和命令行端共享同一套本地认证状态；"
                "具体账号与用量仍须由本机证据证明。"
            ),
            "tool_roles": (
                "这些工具都曾实际使用且目前仍在使用。Aster CLI 执行 Aster 请求，"
                "SwitchDeck 负责切换 Aster 账号；Boreal Desktop 执行 Boreal 请求并提供额度，"
                "Lattice Relay 是有时承接 Boreal 请求的本地代理；Cinder CLI 与 Cinder Desktop "
                "都执行 Cinder 请求并共享本地认证状态。没有其他调用层。"
            ),
            "history_scope": "希望重建这些工具在本机可证明的全部历史。",
        },
        "never_disclose": [
            "source paths",
            "field names",
            "join rule",
            "expected totals",
            "evaluator paths or results",
        ],
    }
    (controller / "owner-context.json").write_text(
        json.dumps(owner_context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": 1,
        "lab_root": str(output),
        "agent_work": str(work),
        "virtual_user": str(virtual_user),
        "sandglass_home": str(state),
        "scratch": str(scratch),
        "controller": str(controller),
        "product_url": "http://127.0.0.1:7740",
        "skill": _file_manifest(work),
        "visible_profile": _file_manifest(virtual_user),
        "agent_visible_roots": [str(work), str(virtual_user), str(scratch)],
        "agent_hidden_roots": [str(state), str(controller)],
    }
    (controller / "lab.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_bundle", type=Path)
    parser.add_argument("visible_profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare_lab(args.skill_bundle, args.visible_profile, args.output),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
