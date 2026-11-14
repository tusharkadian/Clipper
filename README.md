# Clipper

An experimental, local-first Windows developer alpha for finding and exporting captioned clips from authorized gaming recordings. No accounts, paid inference, cloud analysis or automatic publishing. The application source is Apache-2.0; third-party software and models retain separate licenses.

**Accuracy is not yet validated for posting.** Activity peaks and a small transcript model can select non-events, miss quiet plays or produce incorrect captions. Developers should inspect and correct every clip they post. Confidence and successful encoding do not establish transcription accuracy, publishing permission or virality.

## Run From Source

Requirements: Windows x64, Python3.12 x64 on PATH, internet for initial dependencies/assets, and sufficient disk space. Setup preserves20GiB drive headroom; the application workspace cap is60GiB. GPU is optional; CPU processing is supported. CUDA/cuDNN and driver installation remain manual.

```powershell
./scripts/setup.ps1 -InstallModel
./scripts/start.ps1
```

Open the printed loopback address, normally http://127.0.0.1:8765. Keep the launcher open; Ctrl+C stops it, while closing the browser does not stop jobs. Do not expose the service on the network. Setup uses a project-local virtual environment and pinned Node/FFmpeg archives. No administrator access or persistent power/driver changes are required.

For optional local AI reranking, use ./scripts/setup.ps1 -InstallReranker or the explicit install control. Model downloads are opt-in; inference is local. Auto prefers available GPU paths and reports CPU fallback. See [GPU setup](docs/gpu-setup.md) and [local reranker policy](docs/local-reranker.md).

GitHub clones/source downloads do not have a transfer manifest. Run setup directly. CLI-created source ZIPs have a manifest and can be checked before setup:

```powershell
python ./backend/clipper/transfer.py --verify .
```

## Current Behavior

- Local uploads, single completed YouTube videos/streams and Twitch VOD adapters; no live capture or access-control bypass.
- Durable single-worker queue with pause/resume, cancel/retry and completed-job removal.
- English-only speech recognition with word timings and visible transcript-health diagnostics; actual spoken language is not independently verified.
- Audio/visual activity selection, approximate scene hints, conservative shared-footage/long-dialogue suppression and optional bounded Qwen transcript reranking.
- Maximum clips is the sole output ceiling, not a guaranteed count. Threshold, spoken-boundary and duplicate checks can return fewer or no clips.
- Portrait1080x1920 H.264/AAC/30fps exports, partial center zoom and blurred backdrop, uniformly scaled captions and unstyled SRT sidecars.
- GPU-preferred speech/analysis/encoding when supported, actual encoder probing, validated outputs and visible CPU fallback.

The text model cannot verify silent gameplay or audience response. Unreliable transcripts and unhelpfully flat/saturated AI scores retain activity ranking. Caption text can still be wrong. Manual clip/caption editing, reliable language detection, game-event recognition and held-out usefulness evaluation remain pending.

## Storage

Uploads are copied into .data/imports and runs are stored in .data/runs. External source originals are not modified. Removing a completed job deletes its managed copy, exports and caches, while preserving model weights and external originals.

Managed media expires seven days after its first completion/failure/cancellation; retries do not extend that deadline. Cleanup runs at startup and approximately each minute, retries locked files and protects active dependencies. If the app is closed, overdue deletion waits for the next launch. Download clips you want to retain; copies saved outside app storage are not deleted. Recognizable old orphaned media is recovered conservatively with reference, link and age protection.

## Developer Testing

Stop the launcher and finish/cancel pending work before CLI benchmarking. Use identical source/settings/software for CPU/Auto comparisons; Benchmark makes two fixed exports and does not evaluate highlight selection.

```powershell
./.venv/Scripts/python.exe -m clipper.cli doctor
./.venv/Scripts/python.exe -m clipper.cli benchmark 'D:/Recordings/sample.mp4' --seconds 60 --device auto --encoder auto --analysis-decoder auto --keep-awake --timeout 600 --output 'D:/ClipperResults/auto-60s.json'
./.venv/Scripts/python.exe -m pytest backend/tests -q
```

Keep-awake is opt-in and job-scoped, not a global power change. It cannot prevent forced battery sleep, shutdown or every ModernStandby transition. Deadlines are checked when execution can resume; incomplete benchmarks are not throughput measurements. No thermal/lifespan or sustained-capacity guarantee is made.

See [CONTRIBUTING](CONTRIBUTING.md) for browser tests and [cross-machine testing](docs/testing-other-windows.md). Different physical hardware still requires validation. Short synthetic fixtures prove workflows, not clip accuracy.

For measured clip/caption accuracy, use the private [human-label evaluation workflow](docs/clip-quality-evaluation.md). Generated scores and confidence are not labels. It initializes review files from retained runs, computes JiWER against human references and reports insufficient evidence rather than passing an unlabeled or reused holdout set. Never publish the private manifests, transcript hypotheses or annotations with source paths.

## GitHub Sharing and Issues

Use the [source-first GitHub checklist](docs/github-sharing.md). Do not publish the old tools/models ZIPs or commit .data, .tools, .venv, node_modules, built UI, private recordings or internal notes. Build a reviewed source-only export:

```powershell
./.venv/Scripts/python.exe -m clipper.cli package --source-only --output ./releases/clipper-source-alpha.zip
```

Raw reports/logs can contain private paths, source URLs and transcript evidence. Generate a restricted summary, inspect it, and attach only deliberately reviewed data:

```powershell
./.venv/Scripts/python.exe -m clipper.cli diagnostics --report 'D:/ClipperResults/report.json' --output 'D:/ClipperResults/diagnostic-summary.json'
```

Use the GitHub issue forms for bugs or quality feedback. Report security problems privately as described in [SECURITY](SECURITY.md). This local app is not a hostile-media sandbox.

## License and Roadmap

Copyright2026 Tushar Kadian. Application source: [Apache-2.0](LICENSE), with [NOTICE](NOTICE). Read [third-party inventory and redistribution policy](THIRD_PARTY_NOTICES.md); the application license does not grant rights to downloaded tools/models or source footage/music.

The [developer-alpha task list](docs/architecture.md#developer-alpha-task-list) separates distribution readiness, proposed accuracy targets, posting/correction workflows and physical-machine validation. Proposed targets are not achieved results. Publishing remains manual and creators remain responsible for reviewing context, captions and permissions.