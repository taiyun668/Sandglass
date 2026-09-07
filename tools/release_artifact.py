from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from zipfile import BadZipFile, ZipFile


FORBIDDEN_TEXT = (
    b"GrokWorkerProvider",
    b"codex-auth-web",
    b"GROK_WORKER_HOMES",
    b"GROK_APP_ACCOUNTS",
    b"grok_app_accounts_dir",
)
TEXT_SUFFIXES = {".py", ".html", ".js", ".md", ".txt", ".toml", ".cfg", ".ini"}
RUNTIME_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".pyc", ".pyo"}
REQUIRED_PATHS = {
    "sandglass/capabilities.py",
    "sandglass/web/index.html",
    "sandglass/web/assets/logo-mark.png",
}


def inspect_wheel(path: Path) -> list[str]:
    """Return release-boundary violations found inside one built wheel."""

    errors: list[str] = []
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            for required in sorted(REQUIRED_PATHS - names):
                errors.append(f"{path.name}: missing {required}")
            if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
                errors.append(f"{path.name}: missing packaged LICENSE")
            for name in sorted(names):
                normalized = name.replace("\\", "/")
                basename = normalized.rsplit("/", 1)[-1].lower()
                suffix = Path(basename).suffix.lower()
                if "__pycache__" in normalized.split("/") or suffix in RUNTIME_SUFFIXES:
                    errors.append(f"{path.name}: runtime artifact {normalized}")
                if basename in {"auth.json", ".credentials.json"}:
                    errors.append(f"{path.name}: provider credential file {normalized}")
                if suffix not in TEXT_SUFFIXES:
                    continue
                payload = archive.read(name)
                for forbidden in FORBIDDEN_TEXT:
                    if forbidden in payload:
                        errors.append(
                            f"{path.name}: forbidden public-source marker "
                            f"{forbidden.decode('ascii')} in {normalized}"
                        )
    except (BadZipFile, OSError) as exc:
        errors.append(f"{path.name}: unreadable wheel: {exc}")
    return errors


def write_checksums(paths: list[Path], target: Path) -> None:
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in paths]
    target.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Sandglass wheels and write SHA-256 checksums")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args(argv)
    wheels = sorted(args.dist.glob("sandglass-*.whl"))
    if not wheels:
        parser.error(f"no Sandglass wheel found under {args.dist}")
    errors = [error for wheel in wheels for error in inspect_wheel(wheel)]
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    target = args.checksums or args.dist / "SHA256SUMS"
    write_checksums(wheels, target)
    for wheel in wheels:
        print(f"PASS {wheel.name}")
    print(f"PASS checksums {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
