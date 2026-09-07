param(
    [Parameter(Mandatory = $true)][string]$SignedInnerArchive,
    [string]$OutputDirectory = "build\windows-signed-installer",
    [string]$NsisCompiler
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$archive = [System.IO.Path]::GetFullPath($SignedInnerArchive)
$output = [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $output.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Signing output must stay inside the Sandglass checkout: $output"
}
if (-not (Test-Path -LiteralPath $archive)) { throw "Signed archive is missing: $archive" }
if (Test-Path -LiteralPath $output) {
    if (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1) {
        throw "Signing output must be absent or empty: $output"
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}
$expanded = Join-Path $output "inner"
Expand-Archive -LiteralPath $archive -DestinationPath $expanded
$bundle = Join-Path $expanded "Sandglass"
$uninstaller = Join-Path $expanded "Uninstall.exe"
if (-not (Test-Path -LiteralPath (Join-Path $bundle "Sandglass.exe")) -or
    -not (Test-Path -LiteralPath $uninstaller)) {
    throw "Signed inner archive has the wrong structure."
}
$peFiles = @(Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object {
    $_.Extension.ToLowerInvariant() -in @(".exe", ".dll", ".pyd")
})
$peFiles += Get-Item -LiteralPath $uninstaller
$invalid = @($peFiles | Where-Object {
    (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne "Valid"
})
if ($invalid) {
    throw "Signed inner archive contains $($invalid.Count) PE files without a valid signature."
}
python -m tools.windows_release inspect $bundle
if ($LASTEXITCODE -ne 0) { throw "Signed bundle inspection failed." }

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
$installerName = "Sandglass-$version-windows-x64-unsigned-outer-setup"
& $makensis /V2 /WX `
    "/DAPPVERSION=$version" `
    "/DSOURCEDIR=$bundle" `
    "/DARTIFACTDIR=$output" `
    "/DARTIFACTNAME=$installerName" `
    "/DIMPORT_UNINST" `
    "/DSIGNEDUNINST=$uninstaller" `
    (Join-Path $root "packaging\sandglass.nsi")
if ($LASTEXITCODE -ne 0) { throw "Final NSIS compilation failed." }
$installer = Join-Path $output "$installerName.exe"
if (-not (Test-Path -LiteralPath $installer)) { throw "Outer installer is missing." }
Write-Output "Unsigned outer installer signing request: $installer"
