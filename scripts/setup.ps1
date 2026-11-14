param([switch]$InstallModel, [switch]$InstallReranker)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'This test setup supports Windows x64 only.'
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.12 x64 with python available on PATH, then rerun setup.'
}
& python -c "import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Locked test setup requires Python 3.12 x64. Install that version and rerun setup.' }
if (Test-Path (Join-Path $root 'transfer-manifest.json')) {
    & python (Join-Path $root 'backend/clipper/transfer.py') --verify $root
    if ($LASTEXITCODE -ne 0) { throw 'Transfer verification failed. Extract a clean bundle before setup.' }
}
$tools = Join-Path $root '.tools'
New-Item -ItemType Directory -Force $tools | Out-Null

function Install-VerifiedArchive {
    param([string]$Url, [string]$Name, [string]$Hash, [string]$ExpectedDirectory, [string]$ExpectedFile)
    if (Test-Path (Join-Path $tools "$ExpectedDirectory/$ExpectedFile")) { return }
    $archive = Join-Path $tools $Name
    Invoke-WebRequest $Url -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash -ine $Hash) {
        Remove-Item $archive
        throw "Checksum mismatch for $Name. Tool updates require explicit verification."
    }
    Expand-Archive $archive -DestinationPath $tools -Force
}

Install-VerifiedArchive `
    -Url 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-win-x64.zip' `
    -Name 'node-v24.22.2-win-x64.zip' `
    -Hash 'CA1BFF5D8EAD13C9F2EA404A176DB46BD430704D5B5113734049FA365E7799E2' `
    -ExpectedDirectory 'node-v24.22.2-win-x64' -ExpectedFile 'node.exe'
Install-VerifiedArchive `
    -Url 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip' `
    -Name 'ffmpeg.zip' `
    -Hash '60F467265B1E312373DBCD92200C2618A74850F98D3D078E94296BB3FA2047BA' `
    -ExpectedDirectory 'ffmpeg-9.0.2-essentials_build' -ExpectedFile 'bin/ffmpeg.exe'

$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    python -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $python -m pip install -r (Join-Path $root 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& $python -m pip install --no-deps -e $root
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
$nodeDirectory = Join-Path $tools 'node-v24.22.2-win-x64'
$env:PATH = "$nodeDirectory;$env:PATH"
$node = Join-Path $nodeDirectory 'node.exe'
$npm = Join-Path $nodeDirectory 'node_modules/npm/bin/npm-cli.js'
& $node $npm --prefix (Join-Path $root 'apps/web') ci --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& $node $npm --prefix (Join-Path $root 'apps/web') run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
if ($InstallModel -and -not (Test-Path (Join-Path $root '.data/models/small.en/model.bin'))) {
    & $python -m clipper.cli model-install
    if ($LASTEXITCODE -ne 0) { throw 'English model installation failed.' }
}
if ($InstallReranker) {
    & $python -m clipper.cli reranker-install
    if ($LASTEXITCODE -ne 0) { throw 'Local Qwen reranker installation failed.' }
}
& $python -m clipper.cli doctor
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed. Resolve the reported media/disk prerequisites before starting.' }
Write-Output 'Setup complete. Run ./scripts/start.ps1.'