"""Stage the exact adapter mechanism without answer-bearing cases or v3 files."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL = ROOT / "skills" / "sandglass-adapter" / "SKILL.md"
BUNDLE_FILES = (
    Path("SKILL.md"),
    Path("references/reconstruction.md"),
    Path("references/v2-interface.md"),
    Path("scripts/v2_import.py"),
)


def prepare_bundle(output: Path) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("clean-room output must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    source_root = SOURCE_SKILL.parent
    source_bytes = {relative: (source_root / relative).read_bytes() for relative in BUNDLE_FILES}
    for relative, content in source_bytes.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    files = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    )
    expected_files = sorted(relative.as_posix() for relative in BUNDLE_FILES)
    if files != expected_files:
        raise RuntimeError(f"clean-room bundle contains unexpected files: {files}")
    digests = {
        relative.as_posix(): hashlib.sha256(content).hexdigest()
        for relative, content in source_bytes.items()
    }
    return {
        "files": files,
        "working_directory": str(output),
        "source_sha256": digests["SKILL.md"],
        "bundle_sha256": hashlib.sha256((output / "SKILL.md").read_bytes()).hexdigest(),
        "file_sha256": digests,
        "byte_identical": all(
            (output / relative).read_bytes() == content
            for relative, content in source_bytes.items()
        ),
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        result = prepare_bundle(Path(temporary) / "bundle")
        assert result["files"] == sorted(path.as_posix() for path in BUNDLE_FILES)
        assert Path(result["working_directory"]).resolve() == (
            Path(temporary) / "bundle"
        ).resolve()
        assert result["byte_identical"]
        assert result["source_sha256"] == result["bundle_sha256"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("adapter clean-room bundle self-test: PASS")
        return 0
    if args.output is None:
        parser.error("output is required unless --self-test is used")
    print(json.dumps(prepare_bundle(args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
