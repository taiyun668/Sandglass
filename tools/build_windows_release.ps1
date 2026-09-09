param(
    [switch]$SkipInstaller,
    [switch]$SkipRuntimeSmoke,
    [string]$NsisCompiler
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$dist = Join-Path $root "dist"
$bundle = Join-Path $dist "Sandglass"
$work = Join-Path $root "build\pyinstaller"
$releaseEnv = Join-Path $root "build\windows-release-env"
$licenseStage = Join-Path $root "build\runtime-licenses"
$webviewDir = Join-Path ${env:LOCALAPPDATA} "SandglassBuildTools\webview2-1.0.4129.50"

function Remove-ReleasePath([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside the Sandglass checkout: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

if (-not [Environment]::Is64BitProcess) {
    throw "The x64 Windows bundle must be built by 64-bit Python."
}

$pythonVersion = python -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.13.15") {
    throw "The reviewed Windows release interpreter is CPython 3.13.15; found $pythonVersion."
}

$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $version) {
    throw "Could not read the project version."
}
$cycloneVersion = python -c "import importlib.metadata as m; print(m.version('cyclonedx-bom'))" 2>$null
if ($LASTEXITCODE -ne 0 -or $cycloneVersion -ne "7.3.1") {
    throw "Install the pinned release tools first: python -m pip install -r tools/windows-release-requirements.txt"
}

$makensis = $null
if (-not $SkipInstaller) {
    $nsisCandidates = @(@(
        $NsisCompiler,
        (Join-Path ${env:LOCALAPPDATA} "SandglassBuildTools\nsis-3.12\makensis.exe"),
        (Join-Path ${env:LOCALAPPDATA} "Programs\NSIS\makensis.exe"),
        (Join-Path ${env:ProgramFiles} "NSIS\makensis.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if (-not $nsisCandidates) {
        throw "NSIS 3.12 is required for the installer candidate. Install NSIS.NSIS or pass -SkipInstaller."
    }
    $makensis = $nsisCandidates[0]
}

& (Join-Path $PSScriptRoot "build_native_shell.ps1") | Out-Host
$buildProvenance = Join-Path $root "build\Sandglass-build-provenance.json"
python -m tools.build_provenance --root $root --output $buildProvenance
if ($LASTEXITCODE -ne 0) { throw "Build provenance generation failed." }
Remove-ReleasePath $bundle
Remove-ReleasePath $work
Remove-ReleasePath $releaseEnv
Remove-ReleasePath $licenseStage
New-Item -ItemType Directory -Force -Path $dist | Out-Null

python -m venv $releaseEnv
if ($LASTEXITCODE -ne 0) { throw "Release environment creation failed." }
$releasePython = Join-Path $releaseEnv "Scripts\python.exe"
& $releasePython -m pip install --disable-pip-version-check `
    -c (Join-Path $root "tools\windows-runtime-constraints.txt") `
    "${root}[desktop]"
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency installation failed." }
& $releasePython -m pip uninstall -y pip
if ($LASTEXITCODE -ne 0) { throw "Release-environment bootstrap cleanup failed." }

$sbom = Join-Path $dist "Sandglass-$version-windows-x64-runtime.cdx.json"
python -m cyclonedx_py environment $releasePython `
    --pyproject (Join-Path $root "pyproject.toml") --mc-type application `
    --spec-version 1.6 --output-format JSON --output-reproducible `
    --output-file $sbom
if ($LASTEXITCODE -ne 0) { throw "CycloneDX runtime SBOM generation failed." }
python -m tools.windows_release inspect-sbom $sbom
if ($LASTEXITCODE -ne 0) { throw "Runtime SBOM inspection failed." }

& $releasePython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw "Release-environment bootstrap restore failed." }
& $releasePython -m pip install --disable-pip-version-check `
    -r (Join-Path $root "tools\windows-release-requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Pinned bundle tool installation failed." }

$licenseArgs = @(
    "-m", "tools.runtime_licenses",
    "--sbom", $sbom,
    "--output", $licenseStage,
    "--root", $root,
    "--webview-dir", $webviewDir
)
if ($makensis) { $licenseArgs += @("--nsis-compiler", $makensis) }
& $releasePython @licenseArgs
if ($LASTEXITCODE -ne 0) { throw "Third-party license collection failed." }

& $releasePython -m PyInstaller `
    (Join-Path $root "packaging\Sandglass.spec") `
    --noconfirm --clean --distpath $dist --workpath $work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

Copy-Item (Join-Path $root "LICENSE") $bundle -Force
Copy-Item (Join-Path $root "PRIVACY.md") $bundle -Force
Copy-Item (Join-Path $root "SUPPORT.md") $bundle -Force
Copy-Item (Join-Path $licenseStage "THIRD_PARTY_NOTICES.md") $bundle -Force
Copy-Item (Join-Path $licenseStage "THIRD_PARTY_LICENSES") $bundle -Recurse -Force

# The installer uses this bundle-owned inventory to avoid restoring a product
# path removed by a later release.  Generate it only after every release-owned
# top-level path has been copied into the bundle.
python -m tools.windows_release write-owned-manifest $bundle
if ($LASTEXITCODE -ne 0) { throw "Product paths manifest generation failed." }

if (-not $SkipRuntimeSmoke) {
    # Sandglass.exe is a GUI-subsystem binary, and PowerShell does not wait for
    # those when they are invoked directly. Measured on this machine: the call
    # below returned in 0.01s with $LASTEXITCODE still holding the previous
    # command's 0, and the process printed its refusal afterwards, while
    # Start-Process -Wait reported the real exit code 2. So this gate could not
    # fail, and was hiding a packaged build that would not run at all.
    $selfTest = Start-Process -FilePath (Join-Path $bundle "Sandglass.exe") `
        -ArgumentList "--self-test" -Wait -PassThru -WindowStyle Hidden
    if ($selfTest.ExitCode -ne 0) {
        throw "Packaged desktop self-test failed with exit code $($selfTest.ExitCode)."
    }
}
python -m tools.windows_release inspect $bundle
if ($LASTEXITCODE -ne 0) { throw "Windows bundle inspection failed." }

$portableName = "Sandglass-$version-windows-x64-unsigned-portable.zip"
$portable = Join-Path $dist $portableName
if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Force }
python -m tools.windows_release zip $bundle $portable
if ($LASTEXITCODE -ne 0) { throw "Portable archive creation failed." }

$artifacts = @($portable, $sbom)
if (-not $SkipInstaller) {
    $setupBase = "Sandglass-$version-windows-x64-unsigned-setup"
    & $makensis /V2 /WX `
        "/DAPPVERSION=$version" `
        "/DSOURCEDIR=$bundle" `
        "/DARTIFACTDIR=$dist" `
        "/DARTIFACTNAME=$setupBase" `
        (Join-Path $root "packaging\sandglass.nsi")
    if ($LASTEXITCODE -ne 0) { throw "NSIS compilation failed." }
    $setup = Join-Path $dist "$setupBase.exe"
    if (-not (Test-Path -LiteralPath $setup)) { throw "Installer output is missing: $setup" }
    if (-not $SkipRuntimeSmoke) {
        & (Join-Path $PSScriptRoot "smoke_windows_installer.ps1") -Installer $setup
        if ($LASTEXITCODE -ne 0) { throw "Installer smoke test failed." }
    }
    $artifacts += $setup
}

$checksums = Join-Path $dist "SHA256SUMS.windows"
python -m tools.windows_release checksums $checksums @artifacts
if ($LASTEXITCODE -ne 0) { throw "Windows checksum generation failed." }
# Signing the manifest is what lets an already-installed copy accept this build
# as ours without a code-signing certificate. It happens here rather than as a
# step the Owner has to remember, and it stays out of CI because the key is not
# there -- which is also what keeps a release a deliberate act.
$releaseKey = Join-Path $env:USERPROFILE ".sandglass\release-key.txt"
if (Test-Path -LiteralPath $releaseKey) {
    & (Join-Path $PSScriptRoot "sign_release_manifest.ps1") -PrivateKey $releaseKey -Manifest $checksums -Version $version
    Write-Output "Manifest signature: $checksums.sig"
    $keyBackup = ""
    . (Join-Path $PSScriptRoot "release_key_locations.ps1")
    foreach ($location in Get-ReleaseKeyLocations) {
        $probe = Join-Path (Join-Path $location.Path "Sandglass") "release-key-backup.txt"
        if (Test-Path -LiteralPath $probe) { $keyBackup = $probe; break }
    }
    if (-not $keyBackup) {
        Write-Warning "The release key has no backup copy. If this disk dies, every installed copy stops being able to update. Run tools/sign_release_manifest.ps1 -NewKey on a fresh key, or copy $releaseKey somewhere that leaves this machine."
    }
} else {
    Write-Output "No release key at $releaseKey, so this candidate carries no manifest signature."
    Write-Output "Run tools/sign_release_manifest.ps1 -NewKey once to create one."
}

Write-Output "Windows release candidate: $($artifacts -join ', ')"
Write-Output "Checksums: $checksums"
