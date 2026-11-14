# Testing on Another Windows Machine

## Prerequisites

- Windows x64 and Python3.12 x64 available as `python`. Install Python separately; Clipper does not require elevation or install drivers.
- At least20GiB free drive headroom after extraction, plus space for dependencies, source media and exports. Model/tool assets are optional and can add severalGiB.
- Internet for initial Python/frontend dependencies and any assets not included. This bundle is not a fully offline installer.
- CPU processing works without CUDA. Optional GPU speech requires compatible CUDA12/cuDNN9; hardware decoding/encoding also require working driver/runtime support. Auto tests actual paths and falls back visibly. Shared GPU memory is not dedicatedVRAM.

Use only authorized source recordings. Keep model/FFmpeg/Node license notices with copied assets and review redistribution obligations before sharing beyond your own test machines. This is a Windows test bundle, not a signed production installer.

## Create the Bundle on the Original Machine

From the project directory, with a successful frontend build:

```powershell
./.venv/Scripts/python.exe -m clipper.cli package --output ./releases/clipper-windows-test.zip --include-tools --include-models
```

Omit either asset flag for a smaller bundle. No recordings, imports, job database, reports, logs, `.venv`, `node_modules` or developer absolute paths are intentionally included. Source/tests/docs, locked dependencies and the built UI are copied by an allowlist. Models include only the selected small.en and Qwen directories; tools include only pinned Node/FFmpeg directories, not arbitrary tools or installers.

Existing output files are never overwritten. The ZIP contains a `clipper` folder and transfer-manifest.json with per-file sizes/SHA256 hashes. The manifest checks transfer integrity, not publisher identity or code signing. With --include-tools, npm's vendor files under Node's own node_modules are necessary and included; the application's apps/web/node_modules directory is not.

Do not copy the existing project folder wholesale or reuse its virtual environment. Python environments and queue source paths are machine-specific.

## Extract and Set Up

Extract to a writable local folder, then open PowerShell in its `clipper` directory. Avoid cloud-sync folders while testing. Do not copy the original jobs database or imported media directory. For a GitHub clone/Download ZIP, skip manual transfer verification because GitHub does not create transfer-manifest.json; setup still works without it.

```powershell
python ./backend/clipper/transfer.py --verify .
./scripts/setup.ps1 -InstallModel -InstallReranker
./scripts/start.ps1
```

Setup verifies a CLI-created extracted bundle before installing, creates a new `.venv`, installs locked dependencies, builds the UI and runs doctor. Public GitHub source exports do not include models/tools. Missing optional models download only when explicitly requested. CUDA/cuDNN/driver installation remains manual; see [gpu-setup.md](gpu-setup.md). Do not publish asset-containing test ZIPs without reviewing third-party redistribution obligations.

If local script policy blocks execution, use an approved way to run the scripts on that machine; do not lower machine-wide policy. Setup changes only project files and its current-process PATH, not registry/system power settings.

Open the printed loopback address, normally http://127.0.0.1:8765. Auto uses the available hardware; a CPU-only machine is a valid test target. Do not expose this service on the network.

## Comparable CLI Benchmarks

Stop the web launcher first. Finish or cancel pending/paused work. CLI benchmarking refuses an active worker/non-idle queue and processes only its own job.

Use the same source file, sample length, profile, captions, zoom and software bundle across machines. Start with a short sample, then5minutes, then representative30minute and sustained workloads only after output inspection. Benchmark mode makes two fixed exports; it does not evaluate highlight selection or run Qwen.

```powershell
./.venv/Scripts/python.exe -m clipper.cli benchmark 'D:/Recordings/sample.mp4' --seconds 60 --device cpu --encoder cpu --analysis-decoder cpu --keep-awake --timeout 600 --output 'D:/ClipperResults/cpu-60s.json'
./.venv/Scripts/python.exe -m clipper.cli benchmark 'D:/Recordings/sample.mp4' --seconds 60 --device auto --encoder auto --analysis-decoder auto --keep-awake --timeout 600 --output 'D:/ClipperResults/auto-60s.json'
```

Choose new report filenames; output never overwrites existing results. The source original remains external and is not deleted by Clipper. Saved output reports outside managed storage survive normal seven-day cleanup.

Use a deliberate --timeout appropriate to the sample and machine. It is an elapsed-time deadline, including sleep, enforced when execution is able to resume; no user process can enforce wall-time cancellation while Windows is hibernating. Failed/cancelled benchmarks retain incomplete reports, available hardware/timing details and finished exports. Do not compare incomplete or interrupted runs as throughput measurements. Test while plugged into power and leave the machine awake; keep-awake cannot override critical-battery sleep.

Reports include sourceSHA256, requested settings, hardware/driver/tool/package/model fingerprints and backend/lock hashes. Compare actual GPU/CPU paths and fallback warnings, not just requested settings. Environment metadata excludes usernames, hostnames and local tool/workspace paths; inspect source content and report warnings before sharing.

Per-export timings separate encode, inspect and full decode validation, including failedNVENC attempts beforeCPUretry. `render_seconds` remains total elapsed time, not pure GPU encode time. Timing snapshots use a best-effort Windows unbiased-clock check for standby gaps. Some ModernStandby states cannot be observed; no clock gap is not proof of uninterrupted execution. Keep-awake is opt-in, scoped to active CLI work and restored on exit/failure; the display may turn off. It does not prevent explicit sleep, lid-close policy, shutdown or all battery-saving transitions, and is not a thermal guarantee. No persistent power settings are changed.

## Acceptance Checklist

1. Doctor passes media/disk prerequisites; missingGPU is reported and CPU still works.
2. Both fixed benchmark exports play, have correct duration/portrait geometry, higher captions and no added black bars.
3. Reports identify actual paths and any CPU fallback, standby suspicion or keep-awake failure. Inspect warnings before comparing timings.
4. A normal Extract clips run completes with visible transcript health and honest AI/activity ranking status. Count remains a maximum, not a target.
5. Pause/resume/cancel and manual removal work; imported copies and exports disappear while external originals/models remain.
6. Compare CPU/Auto on the same sample, then test longer recordings for resource accumulation. Short samples do not establish four-hour capacity or highlight quality.

The original workstation can validate fresh-directory setup and CPU fallbacks, but cannot certify behavior on a different physical CPU/GPU/driver. Record failures and reports from the second machine before calling that target supported.