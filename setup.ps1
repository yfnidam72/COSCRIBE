<#
  Coscribe setup (Windows).
  Run from the repo folder:   powershell -ExecutionPolicy Bypass -File .\setup.ps1
  Options:  -SkipModels   don't pre-download TranslateGemma (it downloads on first "Best" translation)
            -NoShortcut   don't create Desktop / Start menu shortcuts
#>
param([switch]$SkipModels, [switch]$NoShortcut)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Magenta }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Step "Python environment (uv + Python 3.11)"
if (-not (Have "uv")) {
    Write-Host "Installing uv (Python package manager)..."
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}
if (-not (Test-Path "$root\.venv")) { uv venv --python 3.11 "$root\.venv" }
uv pip install --python "$root\.venv\Scripts\python.exe" -r "$root\requirements.txt"

Step "FFmpeg (video decoding, caption rendering)"
if (-not (Have "ffmpeg")) {
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "FFmpeg installed. If Coscribe says it can't find it, sign out and back in once." -ForegroundColor Yellow
} else { Write-Host "FFmpeg found." }

Step "Ollama (runs the translation models locally)"
if (-not (Have "ollama")) {
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
} else { Write-Host "Ollama found." }
if (-not $SkipModels -and (Have "ollama")) {
    Write-Host "Downloading TranslateGemma 12B (~8 GB, one time)..."
    ollama pull translategemma:12b
}

if (-not $NoShortcut) {
    Step "Shortcuts"
    $ws = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath("Desktop"), "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")) {
        $lnk = $ws.CreateShortcut("$dir\Coscribe.lnk")
        $lnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
        $lnk.Arguments = "`"$root\Coscribe.pyw`""
        $lnk.WorkingDirectory = $root
        $lnk.IconLocation = "$root\app\static\coscribe.ico,0"
        $lnk.Description = "Coscribe - the local video translation app"
        $lnk.Save()
    }
    Write-Host "Created Coscribe shortcuts on the Desktop and in the Start menu."
}

Step "Done"
Write-Host "Open Coscribe from the Desktop. The first transcription downloads Whisper large-v3 (~3 GB);"
Write-Host "Amazigh and Darija models (~3.3 GB / ~5.8 GB) download the first time you use them."
