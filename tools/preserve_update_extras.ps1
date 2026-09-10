param(
    [Parameter(Mandatory = $true)]
    [string]$Old,
    [Parameter(Mandatory = $true)]
    [string]$New
)

$ErrorActionPreference = "Stop"

$oldFull = [IO.Path]::GetFullPath($Old).TrimEnd([IO.Path]::DirectorySeparatorChar)
$newFull = [IO.Path]::GetFullPath($New).TrimEnd([IO.Path]::DirectorySeparatorChar)
if (-not (Test-Path -LiteralPath $oldFull -PathType Container)) {
    throw "Old installation directory does not exist: $oldFull"
}
if (-not (Test-Path -LiteralPath $newFull -PathType Container)) {
    throw "Staged installation directory does not exist: $newFull"
}
if ($oldFull.Equals($newFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Old and staged installation directories must be distinct."
}

# This is only the compatibility table for installations made before the
# product-owned manifest was introduced.  Once a valid manifest exists, its
# list is authoritative (including paths that a newer release removed).
$legacyProductPaths = @(
    "Sandglass.exe",
    "_internal",
    "LICENSE",
    "PRIVACY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES",
    "Uninstall.exe",
    ".sandglass-owner"
)
$productPathsManifest = "Sandglass-owned-paths.json"

function Get-ProductPaths([string]$Root) {
    $manifestPath = Join-Path $Root $productPathsManifest
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        return $legacyProductPaths
    }
    try {
        $raw = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8)
        $manifest = $raw | ConvertFrom-Json
        if ($null -eq $manifest -or $manifest.schema -ne 1) {
            throw "unsupported product paths manifest schema"
        }
        $paths = @($manifest.paths)
        if ($paths.Count -eq 0) {
            throw "product paths manifest is empty"
        }
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $previous = $null
        foreach ($path in $paths) {
            if ($path -isnot [string] -or [String]::IsNullOrWhiteSpace($path) -or
                $path -in @('.', '..') -or $path.Contains('/') -or $path.Contains('\')) {
                throw "unsafe product path in manifest"
            }
            if (-not $seen.Add($path) -or ($null -ne $previous -and [StringComparer]::Ordinal.Compare($previous, $path) -gt 0)) {
                throw "product paths manifest must be sorted and unique"
            }
            $previous = $path
        }
        if (-not $seen.Contains($productPathsManifest)) {
            throw "product paths manifest does not own itself"
        }
        return $paths
    } catch {
        throw "Invalid product paths manifest '$manifestPath': $($_.Exception.Message)"
    }
}

$productPaths = Get-ProductPaths $oldFull

function Assert-NoReparse([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to cross a reparse point while preserving update data: $Path"
    }
    if (-not $item.PSIsContainer) { return }
    foreach ($child in (Get-ChildItem -LiteralPath $Path -Force)) {
        Assert-NoReparse $child.FullName
    }
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "")
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Merge-OwnerPath([string]$Source, [string]$Destination) {
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to copy a reparse point: $Source"
    }
    if (Test-Path -LiteralPath $Destination) {
        $destinationItem = Get-Item -LiteralPath $Destination -Force
        if (($destinationItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to merge into a reparse point: $Destination"
        }
        if ($sourceItem.PSIsContainer -ne $destinationItem.PSIsContainer) {
            throw "Owner path type conflicts with staged content: $Destination"
        }
        if (-not $sourceItem.PSIsContainer) {
            $sourceHash = Get-Sha256 $Source
            $destinationHash = Get-Sha256 $Destination
            if ($sourceHash -cne $destinationHash) {
                throw "Owner file conflicts with staged content: $Destination"
            }
            return
        }
        foreach ($child in (Get-ChildItem -LiteralPath $Source -Force)) {
            Merge-OwnerPath $child.FullName (Join-Path $Destination $child.Name)
        }
        return
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

# Validate both trees before copying a byte. A junction discovered halfway
# through must not leave a partially preserved staging tree that looks valid.
Assert-NoReparse $oldFull
Assert-NoReparse $newFull

foreach ($item in (Get-ChildItem -LiteralPath $oldFull -Force)) {
    if ($productPaths -contains $item.Name -or $item.Name -ieq $productPathsManifest) { continue }
    Merge-OwnerPath $item.FullName (Join-Path $newFull $item.Name)
}
