from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from pathlib import Path


PROJECT_NAME = "sandglass"
LICENSE_BASENAMES = ("LICENSE", "COPYING", "NOTICE")
RUNTIME_LICENSES = {
    "bottle": "MIT",
    "cffi": "MIT-0",
    "clr-loader": "MIT",
    "opentelemetry-proto": "Apache-2.0",
    "pillow": "MIT-CMU",
    "protobuf": "BSD-3-Clause",
    "proxy-tools": "Upstream BSD license text; package metadata declares MIT",
    "pycparser": "BSD-3-Clause",
    "pystray": "LGPL-3.0-only",
    "pythonnet": "MIT",
    "pywebview": "BSD-3-Clause",
    "six": "MIT",
    "typing-extensions": "PSF-2.0",
}
EXPECTED_PYTHON_VERSION = "3.13.15"
PROXY_TOOLS_COMMIT = "db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6"
PROXY_TOOLS_LICENSE_SHA256 = "f96cf2b17c9b0cede77438165fdc6ea2f91bedb248d1779538727a2b93d71e12"
def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_exact_constraints(path: Path) -> dict[str, str]:
    constraints: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        if not match:
            raise ValueError(f"non-exact runtime constraint at line {line_number}: {line}")
        name = canonical_name(match.group(1))
        if name in constraints:
            raise ValueError(f"duplicate runtime constraint: {name}")
        constraints[name] = match.group(2)
    return constraints


def _license_files(distribution: metadata.Distribution) -> list[Path]:
    result: list[Path] = []
    for item in distribution.files or ():
        basename = Path(str(item)).name.upper()
        if not basename.startswith(LICENSE_BASENAMES):
            continue
        source = Path(distribution.locate_file(item))
        if source.is_file():
            result.append(source)
    return sorted(set(result), key=lambda path: path.as_posix().lower())


def _copy_component(
    license_root: Path,
    *,
    name: str,
    version: str,
    license_label: str,
    source: str,
    files: list[Path],
) -> dict[str, object]:
    if not files:
        raise ValueError(f"no license text found for {name} {version}")
    component_dir = license_root / canonical_name(name)
    component_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    used_names: set[str] = set()
    for index, original in enumerate(files, start=1):
        filename = original.name
        if filename.lower() in used_names:
            filename = f"{index}-{filename}"
        used_names.add(filename.lower())
        target = component_dir / filename
        shutil.copyfile(original, target)
        copied.append({
            "path": target.relative_to(license_root).as_posix(),
            "sha256": sha256(target),
        })
    return {
        "name": name,
        "version": version,
        "license": license_label,
        "source": source,
        "files": copied,
    }


def _sbom_components(path: Path) -> dict[str, dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    components = list(document.get("components", []))
    root = document.get("metadata", {}).get("component")
    if isinstance(root, dict):
        components.append(root)
    return {
        canonical_name(str(component.get("name", ""))): component
        for component in components
        if isinstance(component, dict) and component.get("name")
    }


def collect_runtime_licenses(
    sbom: Path,
    output: Path,
    root: Path,
    webview_dir: Path,
    nsis_compiler: Path | None = None,
) -> dict[str, object]:
    components = _sbom_components(sbom)
    runtime_names = set(components) - {PROJECT_NAME}
    expected_names = set(RUNTIME_LICENSES)
    if runtime_names != expected_names:
        missing = sorted(runtime_names - expected_names)
        stale = sorted(expected_names - runtime_names)
        raise ValueError(
            f"runtime license map does not match SBOM; missing={missing}, stale={stale}"
        )
    constraints = read_exact_constraints(root / "tools" / "windows-runtime-constraints.txt")
    if set(constraints) != expected_names:
        missing = sorted(expected_names - set(constraints))
        extra = sorted(set(constraints) - expected_names)
        raise ValueError(
            f"runtime constraints do not match license map; missing={missing}, extra={extra}"
        )

    if output.exists() and any(output.iterdir()):
        raise ValueError(f"license output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    license_root = output / "THIRD_PARTY_LICENSES"
    license_root.mkdir()

    distributions = {
        canonical_name(distribution.metadata.get("Name", "")): distribution
        for distribution in metadata.distributions()
        if distribution.metadata.get("Name")
    }
    manifest_components: list[dict[str, object]] = []
    for name in sorted(runtime_names):
        distribution = distributions.get(name)
        if distribution is None:
            raise ValueError(f"SBOM component is not installed: {name}")
        sbom_version = str(components[name].get("version", ""))
        if distribution.version != sbom_version:
            raise ValueError(
                f"installed {name} version {distribution.version} != SBOM {sbom_version}"
            )
        expected_version = constraints[name]
        license_label = RUNTIME_LICENSES[name]
        if sbom_version != expected_version:
            raise ValueError(
                f"{name} version {sbom_version} has not passed license review; "
                f"expected {expected_version}"
            )
        files = _license_files(distribution)
        source = str(components[name].get("purl") or f"installed distribution {name}")
        if name == "proxy-tools":
            override = root / "packaging" / "licenses" / "proxy_tools-LICENSE.txt"
            if sha256(override) != PROXY_TOOLS_LICENSE_SHA256:
                raise ValueError("proxy_tools upstream license hash changed")
            files = [override]
            source = (
                "https://github.com/jtushman/proxy_tools/blob/"
                f"{PROXY_TOOLS_COMMIT}/LICENSE.txt"
            )
        manifest_components.append(_copy_component(
            license_root,
            name=str(components[name].get("name", name)),
            version=distribution.version,
            license_label=license_label,
            source=source,
            files=files,
        ))

    python_version = ".".join(str(part) for part in sys.version_info[:3])
    if python_version != EXPECTED_PYTHON_VERSION:
        raise ValueError(
            f"CPython {python_version} has not passed bundle review; "
            f"expected {EXPECTED_PYTHON_VERSION}"
        )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    manifest_components.append(_copy_component(
        license_root,
        name="CPython",
        version=python_version,
        license_label="PSF-2.0",
        source="https://github.com/python/cpython",
        files=[python_license],
    ))

    pyinstaller = distributions.get("pyinstaller")
    if pyinstaller is None:
        raise ValueError("PyInstaller must be installed before collecting bundle licenses")
    manifest_components.append(_copy_component(
        license_root,
        name="PyInstaller bootloader",
        version=pyinstaller.version,
        license_label="GPL-2.0-or-later with PyInstaller bootloader exception",
        source=f"https://github.com/pyinstaller/pyinstaller/tree/v{pyinstaller.version}",
        files=_license_files(pyinstaller),
    ))

    webview_nuspec = webview_dir / "Microsoft.Web.WebView2.nuspec"
    webview_version_match = re.search(
        r"<version>([^<]+)</version>", webview_nuspec.read_text(encoding="utf-8")
    )
    if not webview_version_match:
        raise ValueError("WebView2 package version is missing")
    manifest_components.append(_copy_component(
        license_root,
        name="Microsoft.Web.WebView2",
        version=webview_version_match.group(1),
        license_label="Microsoft.Web.WebView2 package license and third-party notices",
        source="https://www.nuget.org/packages/Microsoft.Web.WebView2",
        files=[webview_dir / "LICENSE.txt", webview_dir / "NOTICE.txt"],
    ))

    if nsis_compiler is not None:
        manifest_components.append(_copy_component(
            license_root,
            name="NSIS",
            version="3.12",
            license_label="NSIS bundled licenses (zlib/libpng, bzip2, CPL-1.0)",
            source="https://nsis.sourceforge.io/License",
            files=[nsis_compiler.parent / "COPYING"],
        ))

    manifest_components.sort(key=lambda item: str(item["name"]).lower())
    manifest = {"schema": 1, "components": manifest_components}
    (license_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Third-party notices",
        "",
        "Sandglass includes the following third-party components. Exact license and notice",
        "texts are stored under `THIRD_PARTY_LICENSES/` and verified by SHA-256.",
        "",
    ]
    for component in manifest_components:
        lines.extend([
            f"## {component['name']} {component['version']}",
            "",
            f"License: {component['license']}",
            "",
            f"Source: {component['source']}",
            "",
        ])
    (output / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect exact Windows bundle license texts")
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--webview-dir", type=Path, required=True)
    parser.add_argument("--nsis-compiler", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = collect_runtime_licenses(
            args.sbom,
            args.output,
            args.root,
            args.webview_dir,
            args.nsis_compiler,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL third-party licenses: {exc}")
        return 1
    print(f"PASS third-party licenses: {len(manifest['components'])} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
