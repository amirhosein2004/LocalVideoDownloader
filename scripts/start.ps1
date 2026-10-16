param([switch]$ChooseFolder, [string]$DownloadDirectory, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install and start Docker Desktop first.' }
$downloads = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue).'{374DE290-123F-4565-9164-39C4925E467B}'
if ($downloads) { $downloads = [Environment]::ExpandEnvironmentVariables($downloads) }
else { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
if ($ChooseFolder) {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Choose the download folder'
    $picker.SelectedPath = $downloads
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit }
    $DownloadDirectory = $picker.SelectedPath
}
if (-not $DownloadDirectory) { $DownloadDirectory = $downloads }
if (-not [IO.Path]::IsPathRooted($DownloadDirectory)) { throw 'Use a full absolute download folder path.' }
$downloadPath = [IO.Path]::GetFullPath($DownloadDirectory).Replace('\', '/')
if ($downloadPath.Contains("`n") -or $downloadPath.Contains("`r")) { throw 'Folder paths cannot contain line breaks.' }
New-Item -ItemType Directory -Force -Path $downloadPath | Out-Null
$previousDownloadDir = $env:DOWNLOAD_DIR
try {
    $env:DOWNLOAD_DIR = $downloadPath
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed. Check that Docker Desktop is running.' }
} finally { $env:DOWNLOAD_DIR = $previousDownloadDir }
$appUrl = 'http://127.0.0.1:8765'
Write-Host "Open $appUrl"
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { $null = Invoke-WebRequest -UseBasicParsing "$appUrl/api/health" -TimeoutSec 2; $ready = $true; break }
    catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw 'The app did not become ready. Run docker compose logs.' }
if (-not $NoBrowser) { Start-Process $appUrl }
