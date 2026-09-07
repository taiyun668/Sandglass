<#
.SYNOPSIS
Where a copy of the release key can be put, and whether that leaves this machine.

.DESCRIPTION
Dot-source this. It writes one object per candidate, best first:
  Path            a directory to hold the copy
  LeavesTheMachine whether it belongs to a signed-in sync account

A cloud folder existing proves nothing. Windows ships the OneDrive client and
creates %USERPROFILE%\OneDrive whether or not anyone signed in -- measured on the
machine this was written for: folder present, every account entry blank,
syncing nowhere. So each candidate is admitted on its account, not its folder,
because the alternative is telling the Owner their key is safe off the disk
while both copies sit on the same one.
#>

function Get-ReleaseKeyLocations {
    $found = @()

    $oneDriveAccounts = "HKCU:\Software\Microsoft\OneDrive\Accounts"
    if (Test-Path -LiteralPath $oneDriveAccounts) {
        foreach ($account in Get-ChildItem -LiteralPath $oneDriveAccounts -ErrorAction SilentlyContinue) {
            $settings = Get-ItemProperty -LiteralPath $account.PSPath -ErrorAction SilentlyContinue
            if ($settings.UserEmail -and $settings.UserFolder -and (Test-Path -LiteralPath $settings.UserFolder)) {
                $found += [PSCustomObject]@{ Path = $settings.UserFolder; LeavesTheMachine = $true }
            }
        }
    }

    $driveFs = Get-ItemProperty -LiteralPath "HKCU:\Software\Google\DriveFS" -ErrorAction SilentlyContinue
    if ($driveFs.CurrentAccountToken) {
        foreach ($drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
            if ($drive.Description -ne "Google Drive") { continue }
            $mine = Join-Path $drive.Root "My Drive"
            $root = if (Test-Path -LiteralPath $mine) { $mine } else { $drive.Root }
            if (Test-Path -LiteralPath $root) {
                $found += [PSCustomObject]@{ Path = $root; LeavesTheMachine = $true }
            }
        }
    }

    # Last, and honestly labelled: the same disk as the key it is copying.
    $found += [PSCustomObject]@{
        Path = (Join-Path $env:USERPROFILE "Documents"); LeavesTheMachine = $false
    }
    return $found
}
