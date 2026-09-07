<#
.SYNOPSIS
Generate the Owner's release key, or sign a release manifest with it.

.DESCRIPTION
The updater accepts a build when Windows trusts its Authenticode signature OR
when the checksum manifest carries this project's own signature. This produces
the second one, so the update channel does not wait on a certificate.

The private key is written where you tell it to and nowhere else. It does not
belong in the repository, in the build tree, or on a build runner: signing is a
step the Owner performs, which is also what makes every release a deliberate
act rather than something CI can do unattended.

.EXAMPLE
  ./tools/sign_release_manifest.ps1 -NewKey -PrivateKey D:\keys\sandglass-release.xml
  # prints the public key; paste it into RELEASE_PUBLIC_KEY in sandglass/update.py

.EXAMPLE
  ./tools/sign_release_manifest.ps1 -PrivateKey D:\keys\sandglass-release.xml `
      -Manifest dist\SHA256SUMS.windows
  # writes dist\SHA256SUMS.windows.sig next to it
#>
[CmdletBinding()]
param(
    [switch]$NewKey,
    [switch]$NoBackup,
    [string]$PrivateKey,
    [string]$Manifest
)

$ErrorActionPreference = "Stop"

# Kept outside the repository and outside the build tree by default, so it is
# never committed and never uploaded by CI. Back this file up: losing it means
# every already-installed copy stops being able to update itself.
if (-not $PrivateKey) {
    $PrivateKey = Join-Path $env:USERPROFILE ".sandglass\release-key.txt"
}
$keyParent = Split-Path -Parent $PrivateKey
if ($keyParent -and -not (Test-Path -LiteralPath $keyParent)) {
    New-Item -ItemType Directory -Path $keyParent -Force | Out-Null
}
$updatePy = Join-Path (Split-Path -Parent $PSScriptRoot) "sandglass\update.py"

if ($NewKey) {
    if (Test-Path -LiteralPath $PrivateKey) {
        throw "$PrivateKey already exists; refusing to overwrite a release key."
    }
    $key = [System.Security.Cryptography.ECDsa]::Create(
        [System.Security.Cryptography.ECCurve]::CreateFromFriendlyName('nistP256'))
    # ExportECPrivateKey needs .NET Core; Windows PowerShell 5.1 is .NET
    # Framework, so the key is stored as its raw parameters instead: D, X, Y,
    # one hex line each.
    $full = $key.ExportParameters($true)
    $lines = @(
        [System.BitConverter]::ToString($full.D).Replace('-', ''),
        [System.BitConverter]::ToString($full.Q.X).Replace('-', ''),
        [System.BitConverter]::ToString($full.Q.Y).Replace('-', '')
    )
    Set-Content -LiteralPath $PrivateKey -Value $lines -Encoding ascii
    $q = $key.ExportParameters($false).Q
    $public = ([System.BitConverter]::ToString($q.X) + [System.BitConverter]::ToString($q.Y)).Replace('-', '')
    $source = Get-Content -LiteralPath $updatePy -Raw
    if ($source -notmatch 'RELEASE_PUBLIC_KEY = ""') {
        Remove-Item -LiteralPath $PrivateKey -Force
        throw "sandglass/update.py already carries a release key. Rotating one is deliberate: clear RELEASE_PUBLIC_KEY by hand first, and know that copies installed under the old key stop updating."
    }
    $source = $source -replace 'RELEASE_PUBLIC_KEY = ""', ('RELEASE_PUBLIC_KEY = "' + $public + '"')
    [System.IO.File]::WriteAllText($updatePy, $source)
    # "Back this up yourself" is a step that gets skipped, and the person it
    # fails is the one who cannot tell it failed. So the backup is made here,
    # into a folder that already leaves this machine. The trade is explicit:
    # the key reaches the sync provider's servers. The alternative is a single
    # copy on one disk whose loss stops every installed copy from ever updating
    # again, which is the worse outcome for a key of this size.
    $backup = ""
    $backupLeavesTheMachine = $false
    if (-not $NoBackup) {
        . (Join-Path $PSScriptRoot "release_key_locations.ps1")
        $location = @(Get-ReleaseKeyLocations)[0]
        $backupLeavesTheMachine = $location.LeavesTheMachine
        $backupDir = Join-Path $location.Path "Sandglass"
        if (-not (Test-Path -LiteralPath $backupDir)) {
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        }
        $backup = Join-Path $backupDir "release-key-backup.txt"
        Copy-Item -LiteralPath $PrivateKey -Destination $backup -Force
    }
    Write-Output ""
    Write-Output "Done. Nothing else is needed from you."
    Write-Output "  1. Release key:  $PrivateKey"
    if ($backup -and $backupLeavesTheMachine) {
        Write-Output "     Backed up to: $backup"
        Write-Output "     That folder belongs to a signed-in sync account, so the key leaves this machine."
    } elseif ($backup) {
        Write-Output "     Second copy:  $backup"
        Write-Output ""
        Write-Output "     THIS IS NOT A BACKUP YET. No cloud account is signed in on this"
        Write-Output "     machine, so both copies are on the same disk. Copy either file to"
        Write-Output "     a phone, a USB stick, another computer, or a password manager --"
        Write-Output "     it is three short lines of text. If this disk dies and you have"
        Write-Output "     not, every installed copy of Sandglass stops being able to update,"
        Write-Output "     and there is no way to undo that."
    } else {
        Write-Output "     No backup was made (-NoBackup). One disk failure ends this key."
    }
    Write-Output "  2. sandglass/update.py now carries the matching public key."
    Write-Output ""
    Write-Output "The release build signs each release by itself from here."
    Write-Output "You do not have to run this again."
    exit 0
}

if (-not $Manifest) { throw "-Manifest is required when not creating a key." }
function ConvertFrom-HexString([string]$text) {
    $bytes = New-Object byte[] ($text.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($text.Substring($i * 2, 2), 16)
    }
    return $bytes
}

$lines = @(Get-Content -LiteralPath $PrivateKey | Where-Object { $_.Trim() })
if ($lines.Count -ne 3) { throw "$PrivateKey is not a release key (expected D, X, Y)." }
$parameters = New-Object System.Security.Cryptography.ECParameters
$parameters.Curve = [System.Security.Cryptography.ECCurve]::CreateFromFriendlyName('nistP256')
$parameters.D = ConvertFrom-HexString $lines[0].Trim()
$point = New-Object System.Security.Cryptography.ECPoint
$point.X = ConvertFrom-HexString $lines[1].Trim()
$point.Y = ConvertFrom-HexString $lines[2].Trim()
$parameters.Q = $point
$key = [System.Security.Cryptography.ECDsa]::Create($parameters)
$payload = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Manifest))
$signature = $key.SignData($payload, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
$out = "$((Resolve-Path -LiteralPath $Manifest).Path).sig"
Set-Content -LiteralPath $out -Value ([System.BitConverter]::ToString($signature).Replace('-', '')) -Encoding ascii
Write-Output "signed $Manifest -> $out"
