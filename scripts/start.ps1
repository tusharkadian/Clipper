param([int]$Port = 8765)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { throw 'Run ./scripts/setup.ps1 first.' }
if (-not (Test-Path (Join-Path $root 'apps/web/dist/index.html'))) {
    throw 'Frontend build missing. Run ./scripts/setup.ps1 first.'
}
& $python -m clipper.cli serve --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Clipper failed; review the reported error.' }