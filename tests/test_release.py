import hashlib
import json
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path

SECMAIN = 'Section "Sandglass" SecMain'

from sandglass.resources import WEB_DIR
from zipfile import ZipFile

from tools.build_provenance import generate as generate_build_provenance
from tools.prepare_signing_request import create_request as create_signing_request
from tools.release_artifact import inspect_wheel, write_checksums
from tools.runtime_licenses import (
    PROXY_TOOLS_COMMIT,
    PROXY_TOOLS_LICENSE_SHA256,
    RUNTIME_LICENSES,
    read_exact_constraints,
)
from tools.windows_release import (
    REQUIRED_LICENSE_COMPONENTS,
    create_portable_zip,
    inspect_bundle,
    inspect_runtime_sbom,
)


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_project_declares_its_build_backend(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(metadata["build-system"]["requires"], ["setuptools==84.0.0"])

    def test_project_declares_owner_selected_mit_license(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["license"], "MIT")
        self.assertEqual(metadata["project"]["license-files"], ["LICENSE"])
        self.assertNotIn(
            "License :: OSI Approved :: MIT License",
            metadata["project"].get("classifiers", []),
        )
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_installed_package_exposes_cli_and_desktop_launchers(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["scripts"]["sandglass"], "sandglass.cli:main")
        self.assertEqual(
            metadata["project"]["gui-scripts"]["sandglass-desktop"],
            "sandglass.desktop:main",
        )

    def test_audited_bytes_are_not_rewritten_by_a_checkout(self):
        """The digests above are of what git stores, not of what checkout gives.

        This repository sets core.autocrlf=true, which is also git-for-windows'
        default, and until 2026-09-06 nothing declared these files exempt. A
        fresh clone therefore rewrote every LF to CRLF in them: measured with
        `git worktree add`, LICENSE-Geist.txt arrived 4475 bytes against the
        audited 4383, and both licence tests failed. The working tree here
        passed the whole time because its copies predate the conversion -- so
        the machine that would have shipped unreviewed licence bytes is a clean
        one, and this suite would have said nothing about it.

        Two assertions, because either alone can be satisfied while the thing
        is broken: the guard has to exist, and the bytes have to be right here
        and now.
        """
        audited = (
            Path("sandglass/web/fonts/LICENSE-Geist.txt"),
            Path("packaging/licenses/proxy_tools-LICENSE.txt"),
        )
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for relative in audited:
            with self.subTest(path=str(relative)):
                declared = f"{relative.as_posix()} -text"
                self.assertIn(
                    declared, attributes,
                    "审计过字节的文件必须声明 -text,否则新克隆出来的就不是被审计的那份",
                )
                data = (ROOT / relative).read_bytes()
                self.assertNotIn(
                    bytes((13, 10)), data,
                    "工作区里的这份已经被换行转换改过了,和 provenance 记录的字节不是同一份",
                )

    def test_geist_font_keeps_its_license_and_audited_binary(self):
        font = WEB_DIR / "fonts" / "Geist-Variable.woff2"
        license_path = font.parent / "LICENSE-Geist.txt"
        license_text = license_path.read_text(encoding="utf-8")
        provenance = (ROOT / "docs" / "release-provenance-audit.md").read_text(encoding="utf-8")

        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", license_text)
        self.assertEqual(
            hashlib.sha256(font.read_bytes()).hexdigest().upper(),
            "A369FCF5628EA2AA4E1B9E2EC6A5B3624E365BDA588E1F0F2F12B564F728FBB8",
        )
        self.assertEqual(
            hashlib.sha256(license_path.read_bytes()).hexdigest().upper(),
            "C683BFBCC7E087F5D37A54EF628F10387C451A83DDC459B151403A164AC46C90",
        )
        for evidence in (
            "v1.7.2",
            "a73329da8fc62afc917f796555202e4997f79b7c",
            "7FC800D2AC6B92844895196E5041ACA55D814C15DB70C44F79B3B83AB82B04E2",
            "geist-font/Geist/webfonts/Geist[wght].woff2",
        ):
                self.assertIn(evidence, provenance)

    def test_proxy_tools_keeps_the_exact_reviewed_upstream_license(self):
        license_path = ROOT / "packaging" / "licenses" / "proxy_tools-LICENSE.txt"
        provenance = (ROOT / "docs" / "release-provenance-audit.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(hashlib.sha256(license_path.read_bytes()).hexdigest(),
                         PROXY_TOOLS_LICENSE_SHA256)
        self.assertIn(PROXY_TOOLS_COMMIT, provenance)
        self.assertIn(PROXY_TOOLS_LICENSE_SHA256.upper(), provenance)

    def test_windows_release_gate_builds_and_smokes_the_installed_wheel(self):
        workflow = ROOT / ".github" / "workflows" / "windows-release-gate.yml"
        smoke = ROOT / "tests" / "smoke_installed.py"
        text = workflow.read_text(encoding="utf-8")

        self.assertTrue(smoke.is_file())
        self.assertIn("python -m build --wheel", text)
        self.assertIn('python-version: "3.13.15"', text)
        self.assertNotIn('python-version: "3.13"', text)
        self.assertIn("tools/wheel-build-requirements.txt", text)
        self.assertIn("tests\\smoke_installed.py", text)
        self.assertIn("python -m unittest discover -s tests", text)
        self.assertIn("contents: read", text)
        self.assertIn("python -m tools.release_artifact --dist dist", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("dist/SHA256SUMS", text)
        self.assertIn("tools/windows-release-requirements.txt", text)
        self.assertIn("build_windows_release.ps1", text)
        self.assertIn("smoke_windows_bundle.ps1", text)
        self.assertIn("dist/SHA256SUMS.windows", text)

    def test_windows_bundle_smoke_requires_orb_and_loaded_native_panel(self):
        script = (ROOT / "tools" / "smoke_windows_bundle.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('ClassName -ceq "SandglassOrb"', script)
        self.assertIn("$WM_LBUTTONUP = 0x0202", script)
        self.assertIn('Title -ceq "sandglass"', script)
        self.assertIn('ClassName.StartsWith("HwndWrapper["', script)
        self.assertIn("after its first fit message", script)
        self.assertIn("$env:SANDGLASS_HOME = $sandglassHome", script)
        self.assertIn("Stop-Process -Id $processId", script)
        self.assertNotIn("Stop-Process -Name", script)

    def test_windows_bundle_pipeline_is_pinned_and_keeps_install_per_user(self):
        requirements = (ROOT / "tools" / "windows-release-requirements.txt").read_text(
            encoding="utf-8"
        )
        wheel_requirements = (ROOT / "tools" / "wheel-build-requirements.txt").read_text(
            encoding="utf-8"
        )
        runtime_constraints = read_exact_constraints(
            ROOT / "tools" / "windows-runtime-constraints.txt"
        )
        build_script = (ROOT / "tools" / "build_windows_release.ps1").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "packaging" / "sandglass.nsi").read_text(encoding="utf-8")
        installer_smoke = (ROOT / "tools" / "smoke_windows_installer.ps1").read_text(
            encoding="utf-8"
        )
        prepare_signing = (ROOT / "tools" / "prepare_windows_signing.ps1").read_text(
            encoding="utf-8"
        )
        build_signed = (ROOT / "tools" / "build_signed_nsis.ps1").read_text(
            encoding="utf-8"
        )
        spec = (ROOT / "packaging" / "Sandglass.spec").read_text(encoding="utf-8")
        manifest_path = ROOT / "packaging" / "sandglass.manifest"
        manifest = manifest_path.read_text(encoding="utf-8")

        self.assertEqual(
            requirements.splitlines(),
            ["PyInstaller==6.22.2", "cyclonedx-bom==7.3.1"],
        )
        self.assertEqual(
            wheel_requirements.splitlines(),
            ["build==1.6.0", "setuptools==84.0.0"],
        )
        self.assertEqual(set(runtime_constraints), set(RUNTIME_LICENSES))
        self.assertIn("windows-runtime-constraints.txt", build_script)
        self.assertIn('$pythonVersion -ne "3.13.15"', build_script)
        self.assertIn("--self-test", build_script)
        self.assertIn("python -m tools.windows_release inspect", build_script)
        self.assertIn("tools.runtime_licenses", build_script)
        self.assertIn("THIRD_PARTY_NOTICES.md", build_script)
        self.assertIn("tools.build_provenance", build_script)
        self.assertIn("SandglassBuildTools\\nsis-3.12\\makensis.exe", build_script)
        self.assertIn("RequestExecutionLevel user", installer)
        self.assertIn(r'InstallDir "$LOCALAPPDATA\Programs\Sandglass"', installer)
        self.assertIn("${RunningX64}", installer)
        self.assertIn(
            'OpenMutexW(i 0x00100000, i 0, w "Local\\Sandglass.Desktop.SingleInstance")',
            installer,
        )
        # Every mutex probe must take the error from the call that produced it.
        # This used to require `kernel32::GetLastError()`, pinning the mechanism
        # that turned out to be the bug: the System plugin makes its own Win32
        # calls between two System::Call lines, so by the second the thread's
        # last error is gone. The branch had never run, so nothing caught it
        # until the observer mutex was genuinely free and the probe answered
        # "Windows could not verify" for a mutex nobody held.
        probes = [line for line in installer.splitlines() if "OpenMutexW" in line]
        self.assertEqual(len(probes), 3, probes)
        for probe in probes:
            self.assertTrue(probe.rstrip().endswith("? e'"), probe.strip())
        self.assertNotIn("kernel32::GetLastError()", installer)
        self.assertIn("SetErrorLevel 3", installer)
        self.assertNotIn(
            'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run"',
            installer,
        )
        self.assertNotIn(
            'WriteRegDWORD HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run"',
            installer,
        )
        self.assertIn(r'%LOCALAPPDATA%\sandglass is also deliberately preserved', installer)
        self.assertIn(
            'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "sandglass"',
            installer,
        )
        self.assertNotIn('RMDir /r "$INSTDIR"', installer)
        self.assertIn(r'RMDir /r "$INSTDIR\THIRD_PARTY_LICENSES"', installer)
        self.assertIn('"/S"', installer_smoke)
        self.assertIn("--self-test", installer_smoke)
        self.assertIn("Get-TreeSnapshot $providerRoot", installer_smoke)
        self.assertIn("RequireSigned", installer_smoke)
        self.assertIn("Get-AuthenticodeSignature", installer_smoke)
        self.assertIn("EXPORT_UNINST", installer)
        self.assertIn("IMPORT_UNINST", installer)
        self.assertIn("SIGNEDUNINST", installer)
        self.assertIn("tools.prepare_signing_request", prepare_signing)
        self.assertIn("Get-AuthenticodeSignature", build_signed)
        self.assertIn("/DIMPORT_UNINST", build_signed)
        self.assertIn('manifest=str(ROOT / "packaging" / "sandglass.manifest")', spec)
        self.assertIn("Sandglass-build-provenance.json", spec)
        ET.parse(manifest_path)
        self.assertIn("{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}", manifest)
        self.assertIn("<longPathAware", manifest)
        self.assertIn(">true</longPathAware>", manifest)
        self.assertIn('requestedExecutionLevel level="asInvoker"', manifest)
        self.assertIn('assets" / "orb.ico"', spec)
        self.assertNotIn('assets" / "app.ico"', spec)
        self.assertIn('Icon "..\\sandglass\\web\\assets\\orb.ico"', installer)
        self.assertIn('UninstallIcon "..\\sandglass\\web\\assets\\orb.ico"', installer)

    def test_runtime_sbom_gate_requires_runtime_and_rejects_build_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.cdx.json"
            components = [
                {"name": name}
                for name in (
                    "opentelemetry-proto", "pillow", "pythonnet", "pystray", "pywebview",
                )
            ]
            path.write_text(
                json.dumps({
                    "bomFormat": "CycloneDX",
                    "metadata": {"component": {"name": "sandglass"}},
                    "components": components,
                }),
                encoding="utf-8",
            )
            self.assertEqual(inspect_runtime_sbom(path), [])

            components.append({"name": "pip"})
            path.write_text(
                json.dumps({
                    "bomFormat": "CycloneDX",
                    "metadata": {"component": {"name": "sandglass"}},
                    "components": components,
                }),
                encoding="utf-8",
            )
            self.assertTrue(any("build-only" in error for error in inspect_runtime_sbom(path)))

    def test_windows_bundle_gate_and_portable_zip_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Sandglass"
            required = {
                "Sandglass.exe": b"MZfixture",
                "LICENSE": b"license",
                "PRIVACY.md": b"privacy",
                "SUPPORT.md": b"support",
                "THIRD_PARTY_NOTICES.md": b"notices",
                "_internal/sandglass/web/index.html": b"html",
                "_internal/sandglass/web/i18n.js": b"i18n",
                "_internal/sandglass/web/assets/logo-mark.png": b"png",
                "_internal/sandglass/native/Microsoft.Web.WebView2.Core.dll": b"dll",
                "_internal/sandglass/native/Microsoft.Web.WebView2.Wpf.dll": b"dll",
                "_internal/sandglass/native/WebView2Loader.dll": b"dll",
                "_internal/sandglass/native/PanelShell.js": b"js",
            }
            for relative, payload in required.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            license_root = root / "THIRD_PARTY_LICENSES"
            manifest_components = []
            for index, name in enumerate(sorted(REQUIRED_LICENSE_COMPONENTS)):
                relative = f"component-{index}/LICENSE.txt"
                payload = f"license for {name}".encode("utf-8")
                path = license_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                manifest_components.append({
                    "name": name,
                    "version": "fixture",
                    "license": "fixture",
                    "source": "fixture",
                    "files": [{
                        "path": relative,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }],
                })
            (license_root / "manifest.json").write_text(
                json.dumps({"schema": 1, "components": manifest_components}),
                encoding="utf-8",
            )
            provenance = {
                "schema": 1,
                "git_head": "a" * 40,
                "git_dirty": False,
                "project_version": "0.1.0",
                "resources": {
                    name: {
                        "path": source,
                        "bytes": len(required[target]),
                        "sha256": hashlib.sha256(required[target]).hexdigest(),
                    }
                    for name, source, target in (
                        (
                            "web_index",
                            "sandglass/web/index.html",
                            "_internal/sandglass/web/index.html",
                        ),
                        (
                            "web_i18n",
                            "sandglass/web/i18n.js",
                            "_internal/sandglass/web/i18n.js",
                        ),
                        (
                            "panel_bridge",
                            "native/SandglassShell/bin/PanelShell.js",
                            "_internal/sandglass/native/PanelShell.js",
                        ),
                    )
                },
            }
            provenance["build_id"] = hashlib.sha256(
                json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            provenance_path = (
                root / "_internal" / "sandglass" / "Sandglass-build-provenance.json"
            )
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

            self.assertEqual(inspect_bundle(root), [])
            first = Path(tmp) / "first.zip"
            second = Path(tmp) / "second.zip"
            create_portable_zip(root, first)
            create_portable_zip(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())

            first_license = license_root / "component-0" / "LICENSE.txt"
            first_license.write_bytes(b"tampered")
            self.assertTrue(any("license hash mismatch" in error for error in inspect_bundle(root)))
            first_license.write_bytes(b"license for " + sorted(REQUIRED_LICENSE_COMPONENTS)[0].encode())

            credential = root / "_internal" / "auth.json"
            credential.write_bytes(b"secret")
            self.assertTrue(any("credential" in error for error in inspect_bundle(root)))

    def test_build_provenance_uses_clean_commit_and_exact_resource_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            files = {
                "pyproject.toml": '[project]\nname="sandglass"\nversion="9.8.7"\n',
                "sandglass/web/index.html": "index",
                "sandglass/web/i18n.js": "i18n",
                "native/SandglassShell/bin/PanelShell.js": "bridge",
            }
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"], cwd=root, check=True
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            output = Path(tmp) / "provenance.json"
            result = generate_build_provenance(root, output)

            self.assertEqual(result["project_version"], "9.8.7")
            self.assertEqual(len(result["git_head"]), 40)
            self.assertFalse(result["git_dirty"])
            self.assertEqual(
                result["resources"]["web_index"]["sha256"],
                hashlib.sha256(b"index").hexdigest(),
            )
            self.assertEqual(json.loads(output.read_text()), result)

    def test_inner_signing_request_is_deterministic_and_contains_uninstaller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "Sandglass"
            (bundle / "_internal").mkdir(parents=True)
            (bundle / "Sandglass.exe").write_bytes(b"MZapp")
            (bundle / "_internal" / "runtime.dll").write_bytes(b"MZruntime")
            (bundle / "readme.txt").write_text("readme", encoding="utf-8")
            uninstaller = root / "Uninstall.exe"
            uninstaller.write_bytes(b"MZuninstaller")
            first = root / "first.zip"
            second = root / "second.zip"

            one = create_signing_request(bundle, uninstaller, first)
            two = create_signing_request(bundle, uninstaller, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one["pe_count"], 3)
            self.assertEqual(one["pe_files"], two["pe_files"])
            with ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read("Uninstall.exe"), b"MZuninstaller")
                self.assertEqual(archive.read("Sandglass/Sandglass.exe"), b"MZapp")

    def test_release_artifact_gate_rejects_private_and_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "sandglass-0.1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                for required in (
                    "sandglass/capabilities.py",
                    "sandglass/web/index.html",
                    "sandglass/web/assets/logo-mark.png",
                    "sandglass-0.1.0.dist-info/licenses/LICENSE",
                ):
                    archive.writestr(required, b"ok")
                archive.writestr("sandglass/private.py", b"GrokWorkerProvider")
                archive.writestr("sandglass/cache.sqlite", b"runtime")

            errors = inspect_wheel(wheel)

        self.assertTrue(any("GrokWorkerProvider" in error for error in errors))
        self.assertTrue(any("cache.sqlite" in error for error in errors))

    def test_release_artifact_gate_writes_reproducible_sha256sums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "sandglass-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel bytes")
            target = root / "SHA256SUMS"

            write_checksums([wheel], target)

            expected = hashlib.sha256(b"wheel bytes").hexdigest()
            self.assertEqual(target.read_text(encoding="ascii"), f"{expected}  {wheel.name}\n")

    def test_public_policy_documents_state_the_product_boundaries(self):
        required = {
            "CONTRIBUTING.md": ("provider source map", "byte-for-byte invariance"),
            "SECURITY.md": ("Report a vulnerability", "dashboard/API exposure outside IPv4 loopback"),
            "PRIVACY.md": ("%LOCALAPPDATA%\\sandglass", "does not upload local usage history"),
            "SUPPORT.md": ("Signed-out, cloud-only", "does not switch accounts"),
        }
        for filename, phrases in required.items():
            with self.subTest(filename=filename):
                path = ROOT / filename
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                for phrase in phrases:
                    self.assertIn(phrase, text)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for filename in required:
            self.assertIn(f"]({filename})", readme)
        self.assertIn("不代表与对应厂商存在隶属或背书关系", readme)
        self.assertIn("not stable public integration contracts", (ROOT / "SUPPORT.md").read_text(encoding="utf-8"))


class RunningInstallLifecycleTests(unittest.TestCase):
    """Installing and uninstalling while Sandglass runs.

    The observer is a second detached copy of Sandglass.exe, so it holds the
    files an upgrade replaces and an uninstall deletes. File /r over a locked
    target writes whichever pieces it can and leaves the rest -- an install the
    user believes happened -- and an uninstall that only deletes files leaves
    the observer running out of a half-deleted directory, still writing to the
    state directory the uninstaller deliberately preserves.

    The installer smoke test runs --self-test, which exits, and then uninstalls,
    so it never exercises any of this.
    """

    def _nsi(self):
        return (ROOT / "packaging" / "sandglass.nsi").read_text(encoding="utf-8")

    def _section(self, name):
        body = self._nsi().split(name, 1)[1]
        return body.split("SectionEnd", 1)[0]

    def test_stop_reports_whether_the_observer_actually_let_go(self):
        """The installer is about to overwrite the files that process maps.

        This asserted the source text of `main()` and named two functions. It
        went red the moment they moved behind `stop_and_wait`, while never
        having checked the thing that matters: what the installer learns. What
        it learned was nothing -- `--stop` signalled and returned 0 regardless,
        so the installer waited a fixed two seconds for an exit bounded by a
        fifteen-second vendor request per provider, then queried the desktop
        mutex, which says nothing about a detached observer.
        """
        from sandglass import desktop, observer

        for released, expected in ((True, 0), (False, 1)):
            with self.subTest(released=released):
                with mock.patch.object(observer, "stop_supervising_observer"),                      mock.patch.object(observer, "request_observer_stop",
                                       return_value=True),                      mock.patch.object(observer, "_wait_for_mutex_release",
                                       return_value=released),                      mock.patch.object(desktop, "require_canonical_state_home",
                                       lambda: None),                      mock.patch.object(desktop.sys, "argv", ["sandglass", "--stop"]):
                    self.assertEqual(desktop.main(), expected)

    def test_the_stop_deadline_comes_from_the_code_not_a_guess(self):
        """Long enough to cover what actually holds the exit up.

        The wait only decides when to report "I could not confirm it stopped";
        the answer always comes from the mutex. But a bound shorter than one
        vendor request would report that on every ordinary stop.
        """
        from sandglass import observer
        from sandglass.quota import CACHE_TTL_SECONDS_BY_PROVIDER, HTTP_TIMEOUT_SECONDS

        self.assertGreaterEqual(
            observer.stop_deadline_seconds(),
            HTTP_TIMEOUT_SECONDS * len(CACHE_TTL_SECONDS_BY_PROVIDER),
        )

    def test_install_and_uninstall_read_what_stop_reported(self):
        for name in ('Section "Sandglass" SecMain', 'Section "Uninstall"'):
            with self.subTest(section=name):
                section = self._section(name)
                self.assertNotIn("Sleep 2000", section,
                                 "固定等待看不见一个可能十几秒才结束的退出")
                # The directive, not the phrase: the comment beside it says
                # --stop too, and matching that would pass either way.
                calls = [line for line in section.splitlines()
                         if "ExecWait" in line and "--stop" in line]
                self.assertEqual(len(calls), 1, section)
                self.assertTrue(calls[0].rstrip().endswith("$0"),
                                f"ExecWait 必须收下退出码: {calls[0].strip()}")
                self.assertIn("Sandglass.Observer.SingleInstance", self._nsi(),
                              "持有文件的是 observer,门必须查它")

    def test_install_stops_the_previous_copy_before_writing(self):
        section = self._section('Section "Sandglass" SecMain')
        # The directive, not the phrase: the comment beside it says "File /r"
        # too, and matching that would pass no matter where the guard sat.
        write = section.index('File /r "${SOURCEDIR}')
        self.assertIn("--stop", section)
        self.assertLess(section.index("--stop"), write, "必须先停掉旧进程再覆盖文件")

    def test_install_refuses_rather_than_writing_over_a_locked_program(self):
        """Half an upgrade is worse than none: it looks like it worked."""
        section = self._section('Section "Sandglass" SecMain')
        write = section.index('File /r "${SOURCEDIR}')
        self.assertIn("Rename", section)
        self.assertIn("${Errors}", section)
        self.assertLess(section.index("Rename"), write)
        self.assertLess(section.index("Abort"), write,
                        "锁住时必须在写入之前中止，而不是写一半")

    def _exec_lines(self, section, target):
        """The Exec directives in this section naming target, in order."""
        return [n for n, line in enumerate(section.splitlines())
                if line.strip().startswith("Exec ") and target in line]

    def test_a_silent_install_starts_the_program_again(self):
        """The in-app update ends with the app running, or it ends with nothing.

        MUI_FINISHPAGE_RUN is a checkbox on a page /S never draws, so it cannot
        be what brings the program back after an update installs itself.
        """
        section = self._section(SECMAIN)
        lines = section.splitlines()
        write = next(n for n, line in enumerate(lines) if "File /r" in line and "SOURCEDIR" in line)
        relaunch = self._exec_lines(section, "$INSTDIR")
        self.assertTrue(relaunch, "静默安装装完必须把程序拉起来")
        self.assertGreater(relaunch[-1], write, "必须装完才拉起，不是装之前")
        after = section[section.index("File /r"):]
        self.assertIn("${Silent}", after, "拉起必须条件在静默模式上，交互安装走结束页")

    def test_a_silent_install_never_waits_on_a_message_box(self):
        """/S does not suppress MessageBox.

        A detached installer stopping on a modal during a self-update leaves the
        user with no panel, no new version, and a dialog they never asked for.
        The abort happens before File /r, so the old copy is intact and is what
        should come back.
        """
        section = self._section(SECMAIN)
        guard_start = section.index("${Errors}")
        guard = section[guard_start:section.index("Abort", guard_start)]
        self.assertLess(guard.index("${Silent}"), guard.index("MessageBox"),
                        "静默分支必须在弹框之前分出去")
        self.assertTrue(self._exec_lines(guard, "$R0"),
                        "锁住而中止时必须把旧版本拉回来，不能让用户手上什么都没有")

    def test_the_updater_and_the_installer_agree_on_the_silent_flag(self):
        """The two halves live in different files and must not drift apart.

        Asserted on the argv the updater actually builds, not on the source
        text: FEED_URL contains "/Sandglass/", so searching the file for "/S"
        matches the URL and passes with the flag removed.
        """
        from unittest.mock import patch

        from sandglass import update

        seen = []
        with patch.object(update, "download_verified", lambda offer, into: into),                 patch("subprocess.Popen", lambda args, **kw: seen.append(args)):
            update.apply_update({"asset": "s-setup.exe", "version": "9.9.9"})
        self.assertTrue(seen, "更新器必须启动安装器")
        self.assertIn("/S", seen[0], "更新器必须以静默方式启动安装器")
        self.assertIn("${Silent}", self._section(SECMAIN),
                      "安装器必须处理更新器实际传的那个模式")

    def test_uninstall_stops_observing_before_deleting(self):
        section = self._section('Section "Uninstall"')
        self.assertIn("--stop", section)
        self.assertLess(section.index("--stop"), section.index("RMDir /r"),
                        "必须先停止观测再删除程序目录")

    def test_uninstall_also_refuses_while_the_panel_is_running(self):
        """--stop alone is not enough: the panel supervises the observer.

        A running panel puts a stopped observer back within the minute, which
        would land in the middle of the uninstall. The panel is Sandglass.exe,
        so the same rename proves nothing is left holding it.
        """
        section = self._section('Section "Uninstall"')
        remove = section.index("RMDir /r")
        self.assertIn("Rename", section)
        self.assertIn("${Errors}", section)
        self.assertLess(section.index("Abort"), remove,
                        "面板还在跑时必须中止，而不是删一半")

    def test_the_state_directory_is_still_preserved(self):
        """The counterpart: stopping is not licence to delete the books."""
        section = self._section('Section "Uninstall"')
        self.assertNotIn("$LOCALAPPDATA\sandglass", section.replace("; ", ""))


if __name__ == "__main__":
    unittest.main()
