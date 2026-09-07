"""Create and verify the deterministic inner Windows signing request."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PE_SUFFIXES = {".exe", ".dll", ".pyd"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def create_request(bundle: Path, uninstaller: Path, target: Path) -> dict[str, object]:
    bundle = bundle.resolve()
    uninstaller = uninstaller.resolve()
    if not (bundle / "Sandglass.exe").is_file():
        raise ValueError("bundle is missing Sandglass.exe")
    if not uninstaller.is_file() or uninstaller.read_bytes()[:2] != b"MZ":
        raise ValueError("exported uninstaller is not a Windows executable")
    target.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    files = [
        (f"Sandglass/{path.relative_to(bundle).as_posix()}", path)
        for path in bundle.rglob("*")
        if path.is_file()
    ]
    files.append(("Uninstall.exe", uninstaller))
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, path in sorted(files):
            payload = path.read_bytes()
            info = ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (
                0o755 if path.suffix.lower() in PE_SUFFIXES else 0o644
            ) << 16
            archive.writestr(info, payload, compress_type=ZIP_DEFLATED, compresslevel=9)
            if path.suffix.lower() in PE_SUFFIXES:
                entries.append(
                    {
                        "path": relative,
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                )
    return {
        "schema": 1,
        "archive": target.name,
        "pe_files": entries,
        "pe_count": len(entries),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--uninstaller", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = create_request(args.bundle, args.uninstaller, args.output)
    if args.manifest:
        args.manifest.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print(f"PASS signing request {result['pe_count']} PE files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
