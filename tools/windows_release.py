from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


FORBIDDEN_NAMES = {"auth.json", ".credentials.json"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
FORBIDDEN_TEXT = (
    b"GrokWorkerProvider",
    b"codex-auth-web",
    b"GROK_WORKER_HOMES",
    b"GROK_APP_ACCOUNTS",
    b"grok_app_accounts_dir",
)
TEXT_SUFFIXES = {".cfg", ".html", ".ini", ".js", ".json", ".md", ".txt"}
REQUIRED_BUNDLE_PATHS = {
    "Sandglass.exe",
    "LICENSE",
    "PRIVACY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES/manifest.json",
    "_internal/sandglass/web/index.html",
    "_internal/sandglass/web/assets/logo-mark.png",
    "_internal/sandglass/native/Microsoft.Web.WebView2.Core.dll",
    "_internal/sandglass/native/Microsoft.Web.WebView2.Wpf.dll",
    "_internal/sandglass/native/WebView2Loader.dll",
    "_internal/sandglass/native/PanelShell.js",
    "_internal/sandglass/Sandglass-build-provenance.json",
}
REQUIRED_LICENSE_COMPONENTS = {
    "bottle",
    "cffi",
    "clr_loader",
    "CPython",
    "Microsoft.Web.WebView2",
    "opentelemetry-proto",
    "pillow",
    "protobuf",
    "proxy_tools",
    "pycparser",
    "PyInstaller bootloader",
    "pystray",
    "pythonnet",
    "pywebview",
    "six",
    "typing_extensions",
}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
REQUIRED_RUNTIME_COMPONENTS = {
    "sandglass",
    "opentelemetry-proto",
    "pillow",
    "pythonnet",
    "pystray",
    "pywebview",
}
BUILD_ONLY_COMPONENTS = {"cyclonedx-bom", "pip", "pyinstaller"}


def inspect_bundle(root: Path) -> list[str]:
    """Return public-boundary and completeness errors in an onedir bundle."""
    errors: list[str] = []
    files = {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}
    for required in sorted(REQUIRED_BUNDLE_PATHS - files.keys()):
        errors.append(f"missing {required}")
    exe = files.get("Sandglass.exe")
    if exe is not None and not exe.read_bytes()[:2] == b"MZ":
        errors.append("Sandglass.exe is not a Windows executable")
    provenance_path = files.get("_internal/sandglass/Sandglass-build-provenance.json")
    if provenance_path is not None:
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if provenance.get("schema") != 1:
                raise ValueError("unsupported build provenance schema")
            head = str(provenance.get("git_head") or "")
            build_id = str(provenance.get("build_id") or "")
            if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
                errors.append("build provenance has invalid Git HEAD")
            if len(build_id) != 64 or any(
                char not in "0123456789abcdef" for char in build_id
            ):
                errors.append("build provenance has invalid build id")
            unsigned = dict(provenance)
            unsigned.pop("build_id", None)
            expected_build_id = hashlib.sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            if build_id != expected_build_id:
                errors.append("build provenance build id mismatch")
            if provenance.get("git_dirty") is not False:
                errors.append("build provenance is not from a clean tracked worktree")
            packaged_resources = {
                "web_index": "_internal/sandglass/web/index.html",
                "web_i18n": "_internal/sandglass/web/i18n.js",
                "panel_bridge": "_internal/sandglass/native/PanelShell.js",
            }
            resources = provenance.get("resources")
            if not isinstance(resources, dict):
                raise ValueError("build provenance resources must be an object")
            for name, relative in packaged_resources.items():
                entry = resources.get(name)
                packaged = files.get(relative)
                if not isinstance(entry, dict) or packaged is None:
                    errors.append(f"build provenance missing resource {name}")
                    continue
                payload = packaged.read_bytes()
                if int(entry.get("bytes") or -1) != len(payload):
                    errors.append(f"build provenance byte mismatch {name}")
                if str(entry.get("sha256") or "").lower() != hashlib.sha256(
                    payload
                ).hexdigest():
                    errors.append(f"build provenance hash mismatch {name}")
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"unreadable build provenance: {exc}")
    manifest_path = files.get("THIRD_PARTY_LICENSES/manifest.json")
    if manifest_path is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema") != 1:
                raise ValueError("unsupported third-party license manifest schema")
            components = manifest.get("components", [])
            if not isinstance(components, list):
                raise ValueError("third-party license components must be a list")
            names = {
                str(component.get("name", ""))
                for component in components
                if isinstance(component, dict)
            }
            for missing in sorted(REQUIRED_LICENSE_COMPONENTS - names):
                errors.append(f"third-party license manifest missing {missing}")
            for component in components:
                if not isinstance(component, dict):
                    errors.append("invalid third-party license component")
                    continue
                entries = component.get("files", [])
                if not isinstance(entries, list) or not entries:
                    errors.append(f"third-party license component has no files: {component.get('name', '')}")
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        errors.append(f"invalid license file entry for {component.get('name', '')}")
                        continue
                    relative = str(entry.get("path", "")).replace("\\", "/")
                    if not relative or relative.startswith("/") or ".." in relative.split("/"):
                        errors.append(f"unsafe third-party license path {relative}")
                        continue
                    bundle_relative = f"THIRD_PARTY_LICENSES/{relative}"
                    license_file = files.get(bundle_relative)
                    if license_file is None:
                        errors.append(f"missing {bundle_relative}")
                        continue
                    actual = hashlib.sha256(license_file.read_bytes()).hexdigest()
                    if actual != str(entry.get("sha256", "")).lower():
                        errors.append(f"third-party license hash mismatch {bundle_relative}")
        except (OSError, ValueError) as exc:
            errors.append(f"unreadable third-party license manifest: {exc}")
    for relative, path in sorted(files.items()):
        lower_name = path.name.lower()
        if lower_name in FORBIDDEN_NAMES:
            errors.append(f"provider credential file {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"runtime data file {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            payload = path.read_bytes()
        except OSError as exc:
            errors.append(f"unreadable {relative}: {exc}")
            continue
        for marker in FORBIDDEN_TEXT:
            if marker in payload:
                errors.append(f"forbidden public-source marker {marker.decode('ascii')} in {relative}")
    return errors


def create_portable_zip(root: Path, target: Path, prefix: str = "Sandglass") -> None:
    """Create a sorted portable archive with stable ZIP metadata."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted((path for path in root.rglob("*") if path.is_file()),
                           key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            info = ZipInfo(f"{prefix}/{relative}", FIXED_ZIP_TIME)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o755 if path.suffix.lower() == ".exe" else 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED,
                             compresslevel=9)


def write_checksums(paths: list[Path], target: Path) -> None:
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(paths, key=lambda item: item.name.lower())
    ]
    target.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def inspect_runtime_sbom(path: Path) -> list[str]:
    """Ensure the SBOM describes the runtime environment, not build tools."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unreadable runtime SBOM: {exc}"]
    if document.get("bomFormat") != "CycloneDX":
        return ["runtime SBOM is not CycloneDX"]
    names = {
        str(component.get("name", "")).lower()
        for component in document.get("components", [])
        if isinstance(component, dict)
    }
    root_component = document.get("metadata", {}).get("component", {})
    if isinstance(root_component, dict):
        names.add(str(root_component.get("name", "")).lower())
    errors = [
        f"runtime SBOM missing {name}"
        for name in sorted(REQUIRED_RUNTIME_COMPONENTS - names)
    ]
    errors.extend(
        f"runtime SBOM contains build-only component {name}"
        for name in sorted(BUILD_ONLY_COMPONENTS & names)
    )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and package the Sandglass Windows bundle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("bundle", type=Path)

    zip_parser = subparsers.add_parser("zip")
    zip_parser.add_argument("bundle", type=Path)
    zip_parser.add_argument("target", type=Path)

    checksum_parser = subparsers.add_parser("checksums")
    checksum_parser.add_argument("target", type=Path)
    checksum_parser.add_argument("artifacts", type=Path, nargs="+")

    sbom_parser = subparsers.add_parser("inspect-sbom")
    sbom_parser.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "inspect":
        errors = inspect_bundle(args.bundle)
        for error in errors:
            print(f"FAIL {error}")
        if errors:
            return 1
        print(f"PASS Windows bundle {args.bundle}")
        return 0
    if args.command == "zip":
        create_portable_zip(args.bundle, args.target)
        print(f"PASS portable archive {args.target}")
        return 0
    if args.command == "inspect-sbom":
        errors = inspect_runtime_sbom(args.path)
        for error in errors:
            print(f"FAIL {error}")
        if errors:
            return 1
        print(f"PASS runtime SBOM {args.path}")
        return 0
    write_checksums(args.artifacts, args.target)
    print(f"PASS checksums {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
