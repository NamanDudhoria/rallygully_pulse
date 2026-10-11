# Windows launcher (PowerShell). Uses the Python that's active in this terminal (e.g. your conda env).
#   .\run.ps1            first run: installs, builds, seeds demo data, serves http://localhost:8000
#   .\run.ps1 -Reset     rebuild demo data around today (do this on demo morning)
#   .\run.ps1 -Port 8001 use another port
param([switch]$Reset, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($label, [scriptblock]$cmd) {
    Write-Host "==> $label" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: $label (exit code $LASTEXITCODE)" }
}

$pyver = python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyver -lt [version]"3.10") { throw "Python 3.10+ needed (found $pyver). Activate a newer environment." }
$nodever = (node -v).TrimStart("v")
if ([version]$nodever -lt [version]"20.19") { throw "Node 20.19+ needed (found $nodever). Install Node 22 LTS from nodejs.org." }

Step "Installing backend packages" { python -m pip install -q -r backend\requirements.txt }
Push-Location frontend
Step "Installing frontend packages" { npm.cmd install --silent }
Step "Building the app" { npm.cmd run build --silent }
Pop-Location

Push-Location backend
if ($Reset -or -not (Test-Path pulse.db)) { Step "Creating demo data" { python -m app.seed --reset } }
Write-Host "==> Pulse is running at http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Green
python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
Pop-Location
