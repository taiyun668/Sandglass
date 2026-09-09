import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
import uuid
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path

SECMAIN = 'Section "Sandglass" SecMain'

from sandglass.resources import WEB_DIR
from zipfile import ZipFile

from tools.build_provenance import generate as generate_build_provenance
from tools.release_artifact import inspect_wheel, write_checksums
from tools.runtime_licenses import (
    PROXY_TOOLS_COMMIT,
    PROXY_TOOLS_LICENSE_SHA256,
    RUNTIME_LICENSES,
    read_exact_constraints,
)
from tools.windows_release import (
    PRODUCT_PATHS_MANIFEST,
    REQUIRED_LICENSE_COMPONENTS,
    create_portable_zip,
    inspect_bundle,
    inspect_runtime_sbom,
    write_owned_paths_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_sources_agree(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package_source = (ROOT / "sandglass" / "__init__.py").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "sandglass.nsi").read_text(encoding="utf-8")
        version = metadata["project"]["version"]

        self.assertIn(f'__version__ = "{version}"', package_source)
        self.assertIn(f'!define APPVERSION "{version}"', installer)

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
        self.assertNotIn("smoke_windows_bundle.ps1", text)
        self.assertIn('python-version: ["3.12", "3.13.15"]', text)
        self.assertIn("dist/SHA256SUMS.windows", text)
        self.assertNotIn("SIGNPATH", text)
        self.assertNotIn("signpath/", text)

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
        update_smoke = (ROOT / "tools" / "smoke_windows_update.ps1").read_text(
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
        self.assertIn("python -m tools.windows_release write-owned-manifest", build_script)
        self.assertLess(
            build_script.index("write-owned-manifest"),
            build_script.index("python -m tools.windows_release inspect $bundle"),
        )
        self.assertIn("tools.runtime_licenses", build_script)
        self.assertIn("THIRD_PARTY_NOTICES.md", build_script)
        self.assertIn("tools.build_provenance", build_script)
        self.assertIn("SandglassBuildTools\\nsis-3.12\\makensis.exe", build_script)
        self.assertIn("RequestExecutionLevel user", installer)
        self.assertIn(r'InstallDir "$LOCALAPPDATA\Programs\Sandglass"', installer)
        for retired_macro in ("EXPORT_UNINST", "IMPORT_UNINST", "SIGNEDUNINST"):
            self.assertNotIn(retired_macro, installer)
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
        # Ordinary installation must not opt the user into login startup. The
        # updater may preserve an already opted-in portable Run value, so this
        # is measured by the Windows smoke rather than forbidden as source text.
        self.assertIn("Ordinary install changed the Sandglass Run value", update_smoke)
        self.assertNotIn(
            'WriteRegDWORD HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run"',
            installer,
        )
        self.assertIn(r'%LOCALAPPDATA%\sandglass is also deliberately preserved', installer)
        self.assertIn(
            'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "sandglass"',
            installer,
        )
        # The update rollback is allowed to remove the newly activated tree;
        # the uninstaller must still avoid recursively deleting a user-chosen
        # install directory.
        uninstall = installer.split('Section "Uninstall"', 1)[1]
        self.assertNotIn('RMDir /r "$INSTDIR"', uninstall)
        self.assertIn(r'RMDir /r "$INSTDIR\THIRD_PARTY_LICENSES"', installer)
        self.assertIn('"/S"', installer_smoke)
        self.assertIn("--self-test", installer_smoke)
        self.assertIn("Get-TreeSnapshot $providerRoot", installer_smoke)
        self.assertNotIn("RequireSigned", installer_smoke)
        self.assertNotIn("Get-AuthenticodeSignature", installer_smoke)
        self.assertFalse((ROOT / "tools" / "prepare_signing_request.py").exists())
        self.assertFalse((ROOT / "tools" / "prepare_windows_signing.ps1").exists())
        self.assertFalse((ROOT / "tools" / "build_signed_nsis.ps1").exists())
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

    def test_windows_update_smoke_is_bounded_and_exercises_the_real_protocol(self):
        script_path = ROOT / "tools" / "smoke_windows_update.ps1"
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")
        # The smoke is allowed to mutate only its generated temp tree, the
        # exact HKCU keys it snapshots/restores, and the product-owned Start
        # Menu link which it snapshots/restores byte-for-byte. These are guards,
        # not comments: each phrase is part of an executable operation below.
        for parameter in ("$OldInstaller", "$NewInstaller", "$ExpectedGitCommit"):
            self.assertIn(parameter, script)
        for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME", "SANDGLASS_HOME"):
            self.assertIn("$env:" + name, script)
        self.assertIn("$oldInstallArgs = '/S /D=' + $activeDir", script)
        self.assertIn("$newArgs = ('/UPDATE /PARENTPID=' + $oldPanelPid", script)
        self.assertIn("/UPDATE", script)
        self.assertIn("-match '(?i)(^|\\s)/S(?:\\s|$)'", script)
        self.assertIn('Invoke-InstalledStop', script)
        self.assertIn('Stop-Process -Id $oldPanelPid -Force', script)
        handoff = script.index('Stop-Process -Id $oldPanelPid -Force')
        installer_wait = script.index('Wait-ProcessExitBounded $updateProcess', handoff)
        self.assertNotIn('Invoke-InstalledStop', script[handoff:installer_wait],
                         "成功路径必须让安装器自己停止 observer")
        self.assertIn('if ($updateProcess.ExitCode -ne 0)', script)
        self.assertIn('phase=$phase', script)
        self.assertIn('"--self-test"', script)
        self.assertIn('Sandglass-build-provenance.json', script)
        self.assertIn('git_head', script)
        self.assertIn('"$installDir.update-backup"', script)
        self.assertIn('"Sandglass-update-$oldPanelPid"', script)
        self.assertIn('$updateReadyToken = [Guid]::NewGuid().ToString("N")', script)
        self.assertIn("' /RESTARTEXE=\"' + $oldExe + '\" /UPDATE_TOKEN='", script)
        self.assertIn("RequestedSmokeRoot", script)
        self.assertIn("CandidateBundle", script)
        self.assertIn("NsisCompiler", script)
        self.assertIn('"/DSANDGLASS_TEST_UPDATE_TARGET=$installDir"', script)
        self.assertIn("CandidateBundle provenance does not equal ExpectedGitCommit", script)
        self.assertIn("ExpectRollback requires CandidateBundle and NsisCompiler", script)
        self.assertIn('"/DSANDGLASS_TEST_FAULT_POST_ACTIVATION"', script)
        self.assertIn("Portable-to-installed update did not activate the expected new build provenance", script)
        self.assertIn('Join-Path $smokeRoot "installed target"', script)
        self.assertIn('EventWaitHandle]::OpenExisting($updateReadyEventName)', script)
        self.assertIn('The new desktop did not emit its direct UI-ready event', script)
        self.assertIn('Save-RegistryKey $oldInstallKey', script)
        self.assertIn('Restore-RegistryKey $oldInstallKey', script)
        self.assertIn('Restore-RegistryKey $oldUninstallKey', script)
        self.assertIn('"HKCU\\Software\\Sandglass"', script)
        self.assertIn('"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Sandglass"', script)
        self.assertIn('Get-TreeSnapshot $providerRoot', script)
        self.assertIn('Provider fixtures changed during update', script)
        self.assertIn('SANDGLASS_HOME was removed by uninstall', script)
        self.assertIn('Remove-SmokeRoot', script)
        self.assertNotIn('Stop-Process -Name', script)
        self.assertNotIn('Invoke-WebRequest', script)
        self.assertTrue(script.rstrip().endswith("exit 0"),
                        "成功烟测必须显式返回自己的退出码")
        self.assertNotIn('Start-BitsTransfer', script)

    def test_windows_update_smoke_measures_and_restores_exact_state(self):
        """The smoke must prove the real handoff and leave the host untouched."""
        script = (ROOT / "tools" / "smoke_windows_update.ps1").read_text(encoding="utf-8")

        self.assertIn("[Parameter(Mandatory = $true)]\n    [string]$ExpectedGitCommit", script)
        self.assertIn("function Get-Sha256", script)
        self.assertIn("[Security.Cryptography.SHA256]::Create()", script)
        self.assertIn("$oldInstallerHash = Get-Sha256 $oldInstallerPath", script)
        self.assertNotIn("Get-FileHash", script)
        self.assertIn("$oldInstallerHash -eq $newInstallerHash", script)
        self.assertIn('$afterDelete = Invoke-Reg "query" $Key', script)
        self.assertIn("Could not prove originally absent registry key", script)
        self.assertIn("--observer", script)
        self.assertIn("Test-MutexHeld \"Local\\Sandglass.Observer.SingleInstance\"", script)
        self.assertIn("MainWindowHandle", script)
        self.assertIn("IsWindowVisible", script)
        self.assertIn("MainWindowTitle", script)
        self.assertIn("Wait-ProcessExitBounded", script)
        for process_name in ("$oldInstall", "$updateProcess", "$selfTest", "$uninstall"):
            self.assertIn(f"Wait-ProcessExitBounded {process_name}", script)
        self.assertIn("Save-RunValue", script)
        self.assertIn("Restore-RunValue", script)
        self.assertIn("Get-ShortcutState", script)
        self.assertIn("Assert-ShortcutState", script)
        self.assertIn("original-sandglass.lnk", script)
        self.assertIn("Fault-injected update did not restore the Sandglass Run value", script)
        self.assertIn("RegistryValueKind", script)
        self.assertIn("DoNotExpandEnvironmentNames", script)
        self.assertIn("$State.Value.Exported = $true", script)
        self.assertIn("-not $State.Exported", script)
        self.assertIn("try { Restore-RegistryKey $oldInstallKey", script)
        self.assertIn("try { Restore-RegistryKey $oldUninstallKey", script)
        self.assertIn("$provenanceValue.git_head -eq $oldProvenance.git_head", script)
        self.assertIn("$provenanceValue.build_id -eq $oldProvenance.build_id", script)
        self.assertIn("Provider snapshot refuses a reparse point", script)
        self.assertIn('Type = "Directory"', script)
        self.assertIn("$uninstallAttempted", script)
        self.assertIn('-ArgumentList "/S"', script)
        self.assertNotIn('"_?=$installDir"', script)
        self.assertIn('Wait-Until { -not (Test-Path -LiteralPath $installDir) }', script)
        self.assertIn("$updateStagePath", script)
        self.assertIn("$updateFailureLog", script)
        self.assertIn("if ($null -ne $failure) {", script)
        self.assertIn("    exit 1", script)
        self.assertIn("UPDATE SMOKE CLEANUP FAILED", script)
        self.assertIn("$ExpectRollback", script)
        self.assertIn('phase -notlike "fault-post-activation*"', script)
        self.assertIn("did not restore the old build provenance", script)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell is required")
    def test_windows_update_registry_helper_uses_process_exit_semantics(self):
        """The PS 5.1 registry boundary must not turn missing-key stderr into an exception."""
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        source = (ROOT / "tools" / "smoke_windows_update.ps1").read_text(encoding="utf-8")
        start = source.index("function Invoke-Reg")
        end = source.index("function Save-RegistryKey", start)
        helper = source[start:end]
        with tempfile.TemporaryDirectory(prefix="Sandglass reg smoke ") as tmp:
            root = Path(tmp)
            key = "HKCU\\Software\\SandglassSmokeProbe_" + uuid.uuid4().hex
            export_path = root / "path with spaces" / "missing.reg"
            probe = root / "registry-helper-probe.ps1"
            probe.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + helper
                + "$present = Invoke-Reg 'query' 'HKCU'\n"
                + "if ($present.ExitCode -ne 0) { throw \"present query exit $($present.ExitCode)\" }\n"
                + f"$query = Invoke-Reg 'query' '{key}'\n"
                + "if ($query.ExitCode -ne 1) { throw \"query exit $($query.ExitCode)\" }\n"
                + f"$delete = Invoke-Reg 'delete' '{key}'\n"
                + "if ($delete.ExitCode -ne 1) { throw \"delete exit $($delete.ExitCode)\" }\n"
                + f"$export = Invoke-Reg 'export' '{key}' '{export_path}'\n"
                + "if ($export.ExitCode -eq 0) { throw 'missing export unexpectedly succeeded' }\n"
                + f"$import = Invoke-Reg 'import' $null '{export_path}'\n"
                + "if ($import.ExitCode -eq 0) { throw 'missing import unexpectedly succeeded' }\n"
                + "Write-Output 'AFTER'\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(probe)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("AFTER", completed.stdout)

        # Mutation guard: putting the old native call back must fail before
        # AFTER on Windows PowerShell 5.1, proving the test exercises behavior
        # rather than merely checking source text.
        mutated = helper.replace(
            '        $process = [System.Diagnostics.Process]::Start($startInfo)\n',
            '        & reg.exe query $Key 2>$null | Out-Null\n'
            '        $process = $null\n',
        )
        self.assertNotEqual(mutated, helper)
        with tempfile.TemporaryDirectory(prefix="Sandglass reg mutation ") as tmp:
            probe = Path(tmp) / "registry-helper-mutation.ps1"
            key = "HKCU\\Software\\SandglassSmokeMutation_" + uuid.uuid4().hex
            probe.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + mutated
                + f"$query = Invoke-Reg 'query' '{key}'\n"
                + "Write-Output 'AFTER'\n",
                encoding="utf-8",
            )
            mutated_run = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(probe)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertNotEqual(mutated_run.returncode, 0)
        self.assertNotIn("AFTER", mutated_run.stdout)
        self.assertIn("NativeCommandError", mutated_run.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell is required")
    def test_update_extra_preserver_merges_owner_paths_without_old_product_bytes(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        helper = ROOT / "tools" / "preserve_update_extras.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old", root / "new"
            for path in (old / "_internal", new / "_internal",
                         old / "owner-custom" / "empty", new / "owner-custom"):
                path.mkdir(parents=True, exist_ok=True)
            (old / "Sandglass.exe").write_bytes(b"old-product")
            (new / "Sandglass.exe").write_bytes(b"new-product")
            (old / "_internal" / "obsolete.dll").write_bytes(b"obsolete")
            (new / "_internal" / "current.dll").write_bytes(b"current")
            (old / "owner-custom" / "shared.txt").write_bytes(b"same")
            (new / "owner-custom" / "shared.txt").write_bytes(b"same")
            (old / "owner-custom" / "old-only.txt").write_bytes(b"owner")
            (new / "owner-custom" / "new-only.txt").write_bytes(b"new-owner")
            (old / PRODUCT_PATHS_MANIFEST).write_text(
                json.dumps({
                    "schema": 1,
                    "paths": [
                        "LICENSE", "PRIVACY.md", "SUPPORT.md",
                        PRODUCT_PATHS_MANIFEST, "Sandglass.exe",
                        "THIRD_PARTY_LICENSES", "THIRD_PARTY_NOTICES.md",
                        "Uninstall.exe", "_internal", "legacy-helper.dll",
                    ],
                }), encoding="utf-8",
            )
            (old / "legacy-helper.dll").write_bytes(b"old-product")

            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(helper),
                 "-Old", str(old), "-New", str(new)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((new / "Sandglass.exe").read_bytes(), b"new-product")
            self.assertTrue((new / "_internal" / "current.dll").is_file())
            self.assertFalse((new / "_internal" / "obsolete.dll").exists())
            self.assertFalse((new / "legacy-helper.dll").exists())
            self.assertEqual(
                (new / "owner-custom" / "old-only.txt").read_bytes(), b"owner"
            )
            self.assertEqual(
                (new / "owner-custom" / "new-only.txt").read_bytes(), b"new-owner"
            )
            self.assertTrue((new / "owner-custom" / "empty").is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell is required")
    def test_update_extra_preserver_fails_closed_on_malformed_product_manifest(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        helper = ROOT / "tools" / "preserve_update_extras.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old", root / "new"
            old.mkdir(); new.mkdir()
            (old / PRODUCT_PATHS_MANIFEST).write_text("{not-json", encoding="utf-8")
            (old / "owner-secret.txt").write_text("must-not-copy", encoding="utf-8")
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(helper),
                 "-Old", str(old), "-New", str(new)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((new / "owner-secret.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior is required")
    def test_update_extra_preserver_refuses_reparse_points_before_copy(self):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell is required")
        helper = ROOT / "tools" / "preserve_update_extras.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new, target = root / "old", root / "new", root / "target"
            old.mkdir(); new.mkdir(); target.mkdir()
            (old / "owner-before.txt").write_bytes(b"must-not-partially-copy")
            junction = old / "owner-link"
            made = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True, text=True,
            )
            if made.returncode != 0:
                self.skipTest("could not create a test junction")
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(helper),
                 "-Old", str(old), "-New", str(new)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((new / "owner-before.txt").exists())

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

            write_owned_paths_manifest(root)

            self.assertEqual(inspect_bundle(root), [])
            owned_manifest = root / PRODUCT_PATHS_MANIFEST
            owned_manifest.write_text('{"schema":1,"paths":["Sandglass.exe"]}\n', encoding="utf-8")
            self.assertTrue(any("product paths" in error for error in inspect_bundle(root)))
            actual_paths = sorted(path.name for path in root.iterdir())
            owned_manifest.write_bytes(
                (json.dumps(
                    {"schema": 1, "paths": actual_paths}, separators=(",", ":")
                ) + "\n").encode("utf-8")
            )
            self.assertTrue(any(
                "missing installer-created paths: Uninstall.exe" in error
                for error in inspect_bundle(root)
            ))
            write_owned_paths_manifest(root)
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

    def test_sign_manifest_script_output_is_accepted_by_python_verifier(self):
        """Exercise the actual PowerShell signer-to-Python verification path."""
        if os.name != "nt":
            self.skipTest("the release verifier uses Windows CNG")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is not installed")
        from sandglass.release_signature import verify_release_signature

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / "release-key.txt"
            manifest = root / "SHA256SUMS.windows"
            manifest.write_text(
                f"{'a' * 64}  Sandglass-9.9.9-windows-x64-unsigned-setup.exe\n",
                encoding="ascii",
            )
            script = ROOT / "tools" / "sign_release_manifest.ps1"
            quote = lambda path: "'" + str(path).replace("'", "''") + "'"
            command = """
$key = [System.Security.Cryptography.ECDsa]::Create(
    [System.Security.Cryptography.ECCurve]::CreateFromFriendlyName('nistP256'))
$full = $key.ExportParameters($true)
@(
    [System.BitConverter]::ToString($full.D).Replace('-', ''),
    [System.BitConverter]::ToString($full.Q.X).Replace('-', ''),
    [System.BitConverter]::ToString($full.Q.Y).Replace('-', '')
) | Set-Content -LiteralPath __KEY__ -Encoding ascii
& __SCRIPT__ -PrivateKey __KEY__ -Manifest __MANIFEST__ -Version 9.9.9
$q = $key.ExportParameters($false).Q
Write-Output ('PUBLIC=' + ([System.BitConverter]::ToString($q.X) + [System.BitConverter]::ToString($q.Y)).Replace('-', ''))
""".replace("__KEY__", quote(key)).replace("__SCRIPT__", quote(script)).replace(
                "__MANIFEST__", quote(manifest))
            result = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            public = next((line[7:].strip() for line in result.stdout.splitlines()
                           if line.startswith("PUBLIC=")), "")
            self.assertEqual(len(public), 128, result.stdout)
            signature = (manifest.with_name(manifest.name + ".sig")
                         ).read_text(encoding="ascii").strip()
            self.assertTrue(verify_release_signature(
                manifest.read_bytes(), bytes.fromhex(signature), public))
            self.assertTrue(manifest.read_text(encoding="ascii").startswith(
                "# Sandglass-Version: 9.9.9\n"))

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


class SigningClaimsAgreeTests(unittest.TestCase):
    """A sponsor's name on the front page is a claim, and it was once false.

    The public README said "Free code signing provided by SignPath.io,
    certificate by SignPath Foundation" while there was no account, no
    application and no certificate -- the attribution a sponsor asks for once
    it sponsors you, published before it did. Nothing caught it because no two
    files had to agree.

    They do now. Whichever way the project's state moves, these two say the
    same thing about it.
    """

    def test_no_sponsor_is_named_while_there_is_no_account(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        integrity = (ROOT / "docs" / "release-integrity.md").read_text(encoding="utf-8")
        no_account = "No certificate, signing-service account" in integrity

        named = [
            sponsor for sponsor in ("SignPath", "Certum", "DigiCert", "Sectigo",
                                    "Trusted Signing")
            if sponsor in readme
        ]
        if no_account:
            self.assertEqual(
                named, [],
                "签名工作流说没有账号,README 却已经把赞助方的名字挂出去了",
            )
            self.assertIn(
                "not Authenticode-signed.", readme,
                "没有证书时,README 必须自己说清楚,而不是留给用户去猜",
            )
        else:
            self.assertTrue(
                named,
                "已经有签名账号了,README 应当按对方要求给出署名",
            )


class RunningInstallLifecycleTests(unittest.TestCase):
    """Installing and uninstalling while Sandglass runs.

    The observer is a second detached copy of Sandglass.exe, so it holds the
    files an upgrade replaces and an uninstall deletes. File /r over a locked
    target writes whichever pieces it can and leaves the rest -- an install the
    user believes happened -- and an uninstall that only deletes files leaves
    the observer running out of a half-deleted directory, still writing to the
    state directory the uninstaller deliberately preserves.

    The Windows smoke exercises the same stop-before-copy and stop-before-delete
    lifecycle in a temporary install; these tests keep the source-level ordering
    guard independent of that machine-dependent run.
    """

    def _nsi(self):
        return (ROOT / "packaging" / "sandglass.nsi").read_text(encoding="utf-8")

    def _section(self, name):
        body = self._nsi().split(name, 1)[1]
        return body.split("SectionEnd", 1)[0]

    @staticmethod
    def _real_directives(section):
        """Return (line number, source) for non-comment NSIS lines.

        Installer comments deliberately discuss the protocol (including
        ``--stop`` and ``File /r``), so searching the raw section can prove a
        comment's order instead of the executable directive's order.
        """
        return [
            (number, line)
            for number, line in enumerate(section.splitlines())
            if line.strip() and not line.lstrip().startswith(";")
        ]

    def _directive_line(self, section, predicate):
        matches = [
            number for number, line in self._real_directives(section)
            if predicate(line)
        ]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]

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
        stop = self._directive_line(
            section, lambda line: line.strip().startswith("ExecWait ")
            and "--stop" in line
        )
        write = self._directive_line(
            section, lambda line: line.strip().startswith("File /r ")
            and "${SOURCEDIR}" in line
        )
        self.assertLess(stop, write, "必须先停掉旧进程再覆盖文件")

    def test_portable_update_gates_restart_source_without_touching_its_directory(self):
        """A portable source still owns the detached observer and mutexes."""
        installer = self._nsi()
        section = self._section(SECMAIN)
        stop = self._directive_line(
            section, lambda line: line.strip().startswith("ExecWait ")
            and "--stop" in line
        )
        write = self._directive_line(
            section, lambda line: line.strip().startswith("File /r ")
            and "${SOURCEDIR}" in line
        )
        lines = dict(self._real_directives(section))
        self.assertIn('$UpdateSourceExe', lines[stop])
        self.assertLess(stop, write)
        self.assertIn('StrCpy $UpdateSourceExe "$UpdateRestartExe"', installer)
        self.assertIn('StrCpy $INSTDIR "$LOCALAPPDATA\\Programs\\Sandglass"', installer)
        self.assertIn('SANDGLASS_TEST_UPDATE_TARGET', installer)
        self.assertNotIn('Rename "$UpdateRestartExe"', installer)
        self.assertNotIn('RMDir /r "$UpdateRestartExe"', installer)
        self.assertNotIn('FileExists} "$UpdateRestartExe"', installer)
        copy_directive = section.index('File /r "${SOURCEDIR}')
        self.assertIn('Call CheckSandglassMutex', section[:copy_directive])
        self.assertIn('Sandglass.Desktop.SingleInstance', section[:copy_directive])

    def test_stop_order_test_rejects_execwait_moved_after_file_copy(self):
        """Mutation of the real ExecWait directive must make the invariant red."""
        section = self._section(SECMAIN)
        source_lines = section.splitlines()
        stop_index = next(
            i for i, line in enumerate(source_lines)
            if line.strip().startswith("ExecWait ") and "--stop" in line
        )
        write_index = next(
            i for i, line in enumerate(source_lines)
            if line.strip().startswith("File /r ") and "${SOURCEDIR}" in line
        )
        moved = list(source_lines)
        stop_line = moved.pop(stop_index)
        moved.insert(write_index, stop_line)
        mutated = "\n".join(moved)
        with self.assertRaises(AssertionError):
            self.assertLess(
                self._directive_line(
                    mutated, lambda line: line.strip().startswith("ExecWait ")
                    and "--stop" in line
                ),
                self._directive_line(
                    mutated, lambda line: line.strip().startswith("File /r ")
                    and "${SOURCEDIR}" in line
                ),
            )

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
        locked = section.index('StrCpy $UpdatePhase "executable-locked"')
        guard_start = section.rfind("ClearErrors", 0, locked)
        guard = section[guard_start:section.index("Abort", locked)]
        self.assertLess(guard.index("${Silent}"), guard.index("MessageBox"),
                        "静默分支必须在弹框之前分出去")
        self.assertTrue(self._exec_lines(guard, "$R0"),
                        "锁住而中止时必须把旧版本拉回来，不能让用户手上什么都没有")

    def test_the_updater_and_the_installer_agree_on_the_update_protocol(self):
        """The two halves live in different files and must not drift apart.

        Asserted on the argv the updater actually builds, not on the source
        text: FEED_URL contains "/Sandglass/", so searching the file for "/S"
        matches the URL and passes with the flag removed.
        """
        from sandglass import update

        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with mock.patch.dict(os.environ, {"SANDGLASS_HOME": tmp}), \
                        mock.patch.object(update, "download_verified", lambda offer, into: into), \
                        mock.patch("subprocess.Popen", lambda args, **kw: seen.append(args)):
                    update.apply_update({"asset": "s-setup.exe", "version": "9.9.9"})
            finally:
                # Production exits here; the test substitutes Popen and must
                # model that exit before its temporary SANDGLASS_HOME is removed.
                update._release_update_gate()
        self.assertTrue(seen, "更新器必须启动安装器")
        self.assertIn("/UPDATE", seen[0], "更新器必须使用专用更新模式")
        self.assertTrue(any(arg.startswith("/PARENTPID=") for arg in seen[0]))
        self.assertTrue(any(arg.startswith("/RESTARTEXE=") for arg in seen[0]))
        section = self._section(SECMAIN)
        self.assertIn("/UPDATE", self._nsi())
        self.assertIn("WaitForSingleObject", self._nsi())
        self.assertIn("UpdateInstFilesShow", self._nsi())
        self.assertIn("SetAutoClose true", self._nsi())
        self.assertIn('FileWrite $4 "$UpdatePhase$\\r$\\n"', self._nsi())
        failure = self._nsi().split("Function UpdateFailure", 1)[1].split("FunctionEnd", 1)[0]
        self.assertIn("Quit", failure)
        self.assertIn("${Silent}", section,
                      "普通 /S 安装仍必须保留现有静默路径")

    def test_update_mode_has_a_complete_rollback_before_copying_files(self):
        section = self._section(SECMAIN)
        write = section.index('File /r "${SOURCEDIR}')
        backup = section.index('Rename "$INSTDIR" "$UpdateBackup"')
        leave_stage = section.index('SetOutPath "$TEMP"', backup)
        activate = section.index('Rename "$UpdateStage" "$INSTDIR"')
        self.assertLess(write, backup, "新版本必须先完整解到 staging")
        self.assertLess(leave_stage, activate, "激活前必须离开 staging 当前目录")
        self.assertLess(backup, activate, "旧安装备份后才能激活新版本")
        self.assertIn('Rename "$UpdateBackup" "$INSTDIR"', self._nsi())
        self.assertNotIn('RMDir /r "$R0"', self._nsi())

    def test_post_activation_operations_are_error_checked_and_rollback_is_testable(self):
        installer = self._nsi()
        section = self._section(SECMAIN)
        activation = section.index('Rename "$UpdateStage" "$INSTDIR"')
        after_activation = section[activation:]
        for directive in (
            'CreateDirectory "$SMPROGRAMS\\Sandglass"',
            'CreateShortcut "$SMPROGRAMS\\Sandglass\\Sandglass.lnk"',
            'WriteUninstaller "$INSTDIR\\Uninstall.exe"',
            'WriteRegStr HKCU "Software\\Sandglass" "InstallDir"',
            'WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Sandglass" "DisplayVersion"',
            'WriteRegDWORD HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Sandglass" "NoRepair"',
        ):
            self.assertIn(directive, after_activation, directive)
        self.assertIn('SANDGLASS_TEST_FAULT_POST_ACTIVATION', installer)
        failure = installer.split("Function UpdateFailure", 1)[1].split("FunctionEnd", 1)[0]
        self.assertIn('$UpdateActivated == 1', failure)
        self.assertLess(
            failure.index('RMDir /r "$INSTDIR"'),
            failure.index('Rename "$UpdateBackup" "$INSTDIR"'),
        )
        self.assertIn('Call RestoreUpdateRegistry', failure)
        self.assertNotIn('DeleteRegKey HKCU "Software\\Sandglass"', failure)
        self.assertIn('Delete "$SMPROGRAMS\\Sandglass\\Sandglass.lnk"', failure)
        self.assertIn('$UpdateShortcutCaptured == 1', failure)
        self.assertIn('$UpdateShortcutChanged == 1', failure)
        self.assertIn('Rename "$UpdateShortcutBackup" "$SMPROGRAMS\\Sandglass\\Sandglass.lnk"', failure)
        self.assertIn('$UpdateShortcutDirectoryExisted != 1', failure)
        self.assertIn('RMDir "$SMPROGRAMS\\Sandglass"', failure)
        restore = installer.split("Function RestoreUpdateRegistry", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        self.assertNotIn('DeleteRegKey HKCU "Software\\Sandglass"', restore)
        self.assertIn('DeleteRegKey /ifempty HKCU "Software\\Sandglass"', restore)
        self.assertIn('DeleteRegValue HKCU "Software\\Sandglass" "InstallDir"', restore)

    def test_update_validates_new_self_test_before_deleting_backup(self):
        installer = self._nsi()
        section = self._section(SECMAIN)
        selftest = section.index('ExecWait \'"$INSTDIR\\Sandglass.exe" --self-test\'')
        delete_backup = section.index('RMDir /r "$UpdateBackup"')
        self.assertLess(selftest, delete_backup)
        self.assertIn('self-test-launch', section[selftest:delete_backup])
        self.assertIn('self-test', section[selftest:delete_backup])
        self.assertIn('/UPDATE_TOKEN=$UpdateReadySignal', installer)
        self.assertIn('SetErrorLevel 23', installer)
        self.assertIn('/UPDATE_TOKEN=', installer)
        self.assertIn('CreateEventW', installer)
        self.assertIn('WaitForMultipleObjects', installer)
        self.assertNotIn('FileExists} "$UpdateReadySignal"', installer)

    def test_update_preserves_owner_files_before_retiring_old_tree(self):
        installer = self._nsi()
        helper = (ROOT / "tools" / "preserve_update_extras.ps1").read_text(
            encoding="utf-8"
        )
        section = self._section(SECMAIN)
        self.assertIn('Function PreserveUpdateExtras', installer)
        self.assertIn('File /oname=preserve_update_extras.ps1', installer)
        for owned in ("Sandglass.exe", "_internal", "LICENSE", "PRIVACY.md",
                      "SUPPORT.md", "THIRD_PARTY_LICENSES", "Uninstall.exe",
                      PRODUCT_PATHS_MANIFEST):
            self.assertIn(f'"{owned}"', helper)
        self.assertIn("function Merge-OwnerPath", helper)
        self.assertIn("Assert-NoReparse $oldFull", helper)
        self.assertNotIn('FileOpen $4 "$INSTDIR\\.sandglass-owner" w', installer)
        preserve = section.index('Call PreserveUpdateExtras')
        backup = section.index('Rename "$INSTDIR" "$UpdateBackup"')
        selftest = section.index('ExecWait \'"$INSTDIR\\Sandglass.exe" --self-test\'')
        delete_backup = section.index('RMDir /r "$UpdateBackup"')
        self.assertLess(preserve, backup)
        self.assertLess(backup, selftest)
        self.assertLess(preserve, delete_backup)
        failure = installer.split('Function UpdateFailure', 1)[1].split('FunctionEnd', 1)[0]
        self.assertNotIn('Call PreserveUpdateExtras', failure)
        self.assertIn('$UpdatePreserveOk == 1', failure)

    def test_update_ready_signal_is_required_and_precedes_backup_retirement(self):
        installer = self._nsi()
        section = self._section(SECMAIN)
        self.assertIn('${GetOptions} "$R9" "/UPDATE_TOKEN=" $UpdateReadySignal', installer)
        self.assertIn('Function WaitForUpdateReady', installer)
        launch = section.index('Call LaunchUpdateDesktop')
        wait = section.index('Call WaitForUpdateReady', launch)
        delete_backup = section.index('RMDir /r "$UpdateBackup"')
        self.assertLess(launch, wait)
        self.assertLess(wait, delete_backup)
        self.assertIn('ready-timeout', installer)
        launcher = installer.split("Function LaunchUpdateDesktop", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        waiter = installer.split("Function WaitForUpdateReady", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        self.assertIn("t R0", launcher)
        self.assertIn("p R1, p R2", launcher)
        self.assertIn("p .r0, p .r1", launcher)
        self.assertIn("StrCpy $UpdateChildHandle $0", launcher)
        self.assertNotIn("StrCpy $UpdateChildHandle $R0", launcher)
        self.assertIn("p R0", waiter)
        self.assertNotIn("t r0", launcher)
        self.assertNotIn("p r1, p r2", launcher)
        parent_wait = installer.split("Function WaitForUpdateParent", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        self.assertIn("WaitForSingleObject(p r0, i 30000)", parent_wait)
        self.assertIn('StrCpy $UpdatePhase "parent-exit-timeout"', parent_wait)
        self.assertNotIn("0xFFFFFFFF", parent_wait)

    def test_owner_file_guard_mutation_is_caught(self):
        section = self._section(SECMAIN)
        preserve = section.index('Call PreserveUpdateExtras')
        backup = section.index('Rename "$INSTDIR" "$UpdateBackup"')
        self.assertLess(preserve, backup)
        # Reintroducing the old post-activation copy is caught by the same
        # ordering rule: owner preservation must precede the rollback rename.
        mutated = section.replace('Call PreserveUpdateExtras', '; moved', 1)
        mutated = mutated.replace('StrCpy $UpdateBackupCreated 1',
                                  'Call PreserveUpdateExtras\n    StrCpy $UpdateBackupCreated 1', 1)
        with self.assertRaises(AssertionError):
            self.assertLess(
                mutated.index('Call PreserveUpdateExtras'),
                mutated.index('Rename "$INSTDIR" "$UpdateBackup"'),
            )

    def test_portable_opt_in_run_migrates_and_preserves_value_kind(self):
        installer = self._nsi()
        self.assertIn('SnapshotUpdateRun', installer)
        self.assertIn('UpdateOldRunPresent', installer)
        self.assertIn('UpdateOldRunType', installer)
        self.assertIn('WriteRegExpandStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "sandglass"', installer)
        self.assertIn('WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "sandglass"', installer)
        self.assertIn('SANDGLASS_TEST_FAULT_POST_ACTIVATION', installer)
        snapshot = installer.split("Function SnapshotUpdateRun", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        self.assertIn("StrCpy $UpdateOldRunType 0", snapshot)
        self.assertIn('StrCpy $UpdateOldRunType 1', snapshot)
        self.assertIn('StrCpy $UpdateOldRunType 2', snapshot)
        self.assertIn('GetValueKind', snapshot)
        self.assertNotIn('reg.exe query', snapshot)
        section = self._section(SECMAIN)
        shortcut = section.index('CreateShortcut "$SMPROGRAMS\\Sandglass\\Sandglass.lnk"')
        migration = section.index('StrCpy $R2', shortcut)
        self.assertLess(shortcut, migration)
        self.assertIn('$UpdateHasInstalled != 1', section)
        self.assertIn('$UpdateOldRunPresent == 1', section)

    def test_portable_update_never_uses_an_empty_install_registry_path(self):
        section = self._section(SECMAIN)
        activation = section[section.index('File /r "${SOURCEDIR}') :]
        self.assertIn('${If} $UpdateHasInstalled == 1', activation)
        self.assertIn('Rename "$UpdateStage" "$INSTDIR"', activation)
        self.assertIn('Exec \'"$UpdateRestartExe"\'', self._nsi())
        self.assertIn('${If} $UpdateBackup != ""', activation)
        hide = activation.index("ShowWindow $HWNDPARENT 0")
        relaunch = activation.index("Call LaunchUpdateDesktop", hide)
        self.assertLess(hide, relaunch, "安装器窗口必须先消失，新版界面才能出现")
        launcher = self._nsi().split("Function LaunchUpdateDesktop", 1)[1].split(
            "FunctionEnd", 1
        )[0]
        self.assertIn('"$INSTDIR\\Sandglass.exe"', launcher)
        self.assertIn("CreateProcessW", launcher)

    def test_update_staging_path_uses_only_a_canonical_numeric_parent_pid(self):
        nsi = self._nsi()
        canonicalize = nsi.index('IntOp $R7 $UpdateParentPid + 0')
        stage = nsi.index('StrCpy $UpdateStage "$TEMP\\Sandglass-update-$UpdateParentPid"')
        self.assertLess(canonicalize, stage)
        self.assertIn('${If} $R7 <= 0', nsi[canonicalize:stage])

    def test_uninstall_stops_observing_before_deleting(self):
        section = self._section('Section "Uninstall"')
        stop = self._directive_line(
            section, lambda line: line.strip().startswith("ExecWait ")
            and "--stop" in line
        )
        remove = self._directive_line(
            section, lambda line: line.strip().startswith("RMDir /r ")
            and "\\_internal" in line
        )
        self.assertLess(stop, remove,
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
        self.assertNotIn(r"$LOCALAPPDATA\sandglass", section.replace("; ", ""))


if __name__ == "__main__":
    unittest.main()
