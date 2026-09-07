$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "native\SandglassShell"
$output = Join-Path $source "bin"
$version = "1.0.4129.50"
$tools = Join-Path $env:LOCALAPPDATA "SandglassBuildTools"
$package = Join-Path $tools "microsoft.web.webview2.$version.nupkg"
$expanded = Join-Path $tools "webview2-$version"

function Copy-IfChanged([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($sourceHash -eq $destinationHash) {
            return
        }
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}
New-Item -ItemType Directory -Force -Path $tools, $output | Out-Null
# Older development builds produced an unsigned helper executable. The WPF
# panel now runs inside pythonw, so retaining that file only invites Smart App
# Control to block a path Sandglass no longer needs.
foreach ($legacyName in @("SandglassShell.exe", "SandglassShell.pdb")) {
    $legacyPath = Join-Path $output $legacyName
    if (Test-Path -LiteralPath $legacyPath) {
        Remove-Item -LiteralPath $legacyPath -Force
    }
}
if (-not (Test-Path $package)) {
    & curl.exe -L --fail --silent --show-error `
        "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/$version" `
        -o $package
}
if (-not (Test-Path $expanded)) {
    Expand-Archive -LiteralPath $package -DestinationPath $expanded
}

$webview = Join-Path $expanded "lib\net462"
Copy-IfChanged (Join-Path $webview "Microsoft.Web.WebView2.Core.dll") `
    (Join-Path $output "Microsoft.Web.WebView2.Core.dll")
Copy-IfChanged (Join-Path $webview "Microsoft.Web.WebView2.Wpf.dll") `
    (Join-Path $output "Microsoft.Web.WebView2.Wpf.dll")
Copy-IfChanged (Join-Path $expanded "runtimes\win-x64\native\WebView2Loader.dll") `
    (Join-Path $output "WebView2Loader.dll")
Copy-IfChanged (Join-Path $source "PanelShell.js") (Join-Path $output "PanelShell.js")
Write-Output $output
