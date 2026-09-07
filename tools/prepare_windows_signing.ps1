param(
    [string]$Bundle = "dist\Sandglass",
    [string]$OutputDirectory = "build\windows-signing-request",
    [string]$NsisCompiler
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$bundlePath = [System.IO.Path]::GetFullPath((Join-Path $root $Bundle))
$output = [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $bundlePath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Bundle must stay inside the Sandglass checkout: $bundlePath"
}
if (-not $output.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Signing output must stay inside the Sandglass checkout: $output"
}
if (-not (Test-Path -LiteralPath (Join-Path $bundlePath "Sandglass.exe"))) {
    throw "Windows bundle is missing: $bundlePath"
}
if (Test-Path -LiteralPath $output) {
    if (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1) {
        throw "Signing output must be absent or empty: $output"
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$candidates = @(@(
    $NsisCompiler,
    (Join-Path ${env:LOCALAPPDATA} "SandglassBuildTools\nsis-3.12\makensis.exe"),
    (Join-Path ${env:LOCALAPPDATA} "Programs\NSIS\makensis.exe"),
    (Join-Path ${env:ProgramFiles} "NSIS\makensis.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
if (-not $candidates) { throw "NSIS 3.12 compiler is required." }
$makensis = $candidates[0]
$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Could not read version." }

python -m tools.windows_release inspect $bundlePath
if ($LASTEXITCODE -ne 0) { throw "Unsigned bundle inspection failed." }

$uninstaller = Join-Path $output "Uninstall.exe"
& $makensis /V2 /WX `
    "/DAPPVERSION=$version" `
    "/DSOURCEDIR=$bundlePath" `
    "/DARTIFACTDIR=$output" `
    "/DARTIFACTNAME=uninstaller-export-pass" `
    "/DEXPORT_UNINST" `
    "/DUNINSTOUT=$uninstaller" `
    (Join-Path $root "packaging\sandglass.nsi")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $uninstaller)) {
    throw "NSIS uninstaller export failed."
}

$request = Join-Path $output "Sandglass-$version-inner-signing-request.zip"
$manifest = Join-Path $output "inner-signing-manifest.json"
python -m tools.prepare_signing_request --bundle $bundlePath `
    --uninstaller $uninstaller --output $request --manifest $manifest
if ($LASTEXITCODE -ne 0) { throw "Inner signing request creation failed." }
Write-Output "Unsigned inner signing request: $request"
Write-Output "Manifest: $manifest"
