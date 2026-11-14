param()

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = if ($env:CLIPPER_TEST_PYTHON) { $env:CLIPPER_TEST_PYTHON } else { Join-Path $root '.venv/Scripts/python.exe' }
$dataRoot = if ($env:CLIPPER_DATA) { $env:CLIPPER_DATA } else { Join-Path $root '.data' }
$fixtures = Join-Path $dataRoot 'fixtures'
New-Item -ItemType Directory -Force $fixtures | Out-Null
$video = Join-Path $fixtures 'generated-speech.mp4'
if (Test-Path $video) { Write-Output 'Existing generated fixture retained.'; return }
$audio = Join-Path $fixtures ("fixture-$([guid]::NewGuid().ToString('N')).wav")
$voice = New-Object -ComObject SAPI.SpVoice
$stream = New-Object -ComObject SAPI.SpFileStream
$opened = $false
try {
    $voices = $voice.GetVoices('Language=409')
    if ($voices.Count -eq 0) { throw 'An English US Windows speech voice is required for this synthetic fixture.' }
    $voice.Voice = $voices.Item(0)
    $stream.Open($audio, 3, $false)
    $opened = $true
    $voice.AudioOutputStream = $stream
    $voice.Speak('We are waiting for the next match to begin. The clock is running down. One second left. I take the last shot and we win the match.') | Out-Null
    $stream.Close()
    $opened = $false
    $ffmpeg = & $python -c "from clipper.config import configure_tools; from clipper.preflight import executable; configure_tools(); print(executable('ffmpeg') or '')"
    if (-not $ffmpeg) { throw 'Run setup before generating the fixture.' }
    & $ffmpeg -hide_banner -nostdin -v error -n -f lavfi -i 'testsrc2=size=320x180:rate=15:duration=20' -i $audio -af apad -t 20 -c:v libx264 -c:a aac $video
    if ($LASTEXITCODE -ne 0) { Remove-Item $video -ErrorAction SilentlyContinue; throw 'Synthetic fixture encoding failed.' }
} finally {
    if ($opened) { $stream.Close() }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($stream) | Out-Null
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($voice) | Out-Null
    if (Test-Path $audio) { Remove-Item $audio }
}
Write-Output 'Synthetic speech/video fixture generated; not a clip-quality evaluation.'