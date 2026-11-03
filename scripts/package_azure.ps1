<#
Builds the frontend and creates deploy\app.zip for Azure App Service.

The zip contains backend\, frontend\dist\ (even though Git ignores dist) and requirements.txt
(a copy of requirements.lock.txt, so the cloud installs the exact versions tested locally).

Usage (from the project root):
    .\scripts\package_azure.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "1/3 Building frontend..."
Push-Location frontend
try {
    if (-not (Test-Path node_modules)) { npm ci }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
} finally { Pop-Location }
if (-not (Test-Path "frontend\dist\index.html")) { throw "frontend\dist\index.html missing after build" }

Write-Host "2/3 Collecting files..."
$deployDir = Join-Path $root "deploy"
New-Item -ItemType Directory -Force $deployDir | Out-Null
$zipPath = Join-Path $deployDir "app.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$files = @()
$files += Get-ChildItem -Recurse -File backend | Where-Object { $_.FullName -notmatch '\\__pycache__\\' }
$files += Get-ChildItem -Recurse -File frontend\dist

Write-Host "3/3 Writing $zipPath ..."
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in $files) {
        # Forward slashes so the archive extracts correctly on Linux.
        $entry = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entry) | Out-Null
    }
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, (Join-Path $root "requirements.lock.txt"), "requirements.txt") | Out-Null
} finally { $zip.Dispose() }

$sizeKb = [math]::Round((Get-Item $zipPath).Length / 1KB)
Write-Host "Done: deploy\app.zip ($sizeKb KB, $($files.Count + 1) files)"
