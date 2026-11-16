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
- One portrait-layout editor with live9:16 composition preview, gameplay-only Portrait zoom, webcam height and optional manual source crops.
- GPU-preferred speech/analysis/encoding when supported, actual encoder probing, validated outputs and visible CPU fallback.

The text model cannot verify silent gameplay or audience response. Unreliable transcripts and unhelpfully flat/saturated AI scores retain activity ranking. Caption text can still be wrong. Manual trim/caption correction, reliable language detection, game-event recognition and held-out usefulness evaluation remain pending.

## Webcam Above Gameplay

Choose Configure layout below the source to open the single layout editor, with or without a webcam. Webcam in source appears only inside this modal and is off by default, never inferred from faces or people. Enable it, select the actual webcam region, and inspect the portrait preview before applying. Portrait zoom and Webcam panel height update this preview together; Apply saves them and Cancel discards changes. Reopening retains applied settings for the same source. Gameplay's eligible source region starts as the full frame; adjust Gameplay region only when you want a manual crop. No source dimensions need to be entered: local videos support drag selection, and Upload source screenshot provides the same visual picker for URLs or videos your browser cannot preview. Numeric fields are optional under Fine adjustment.

Use a PNG/JPEG/WebP screenshot containing the full video frame, up to10MB/32megapixels. If it contains player borders or browser controls, select Video frame, draw around the actual video and choose Use video frame. Then draw the Webcam region; selecting or moving it does not change the gameplay crop. A screenshot cropped to only the webcam cannot identify its position in the full source. Make sure the selected video frame matches the source content and aspect ratio; screenshot selection cannot infer or reconstruct missing video edges.

Screenshots are local browser references and are not uploaded or saved in job reports; only normalized webcam/gameplay coordinates and panel height are sent. The selected reference is retained while editing the same source, and clears when the source changes or a job is queued. Corrupt/unsupported images show recoverable errors rather than guessing a crop. Replacing a screenshot requires a fresh webcam selection.

The final canvas stays9:16 (1080x1920), not16:9. The webcam band occupies20-45% of portrait height (default30%) and fits its selected source without a webcam zoom control. Enabling webcam starts gameplay aspect-fitted at1x, showing the entire selected gameplay region with blurred fill where needed. Increase Portrait zoom (1-2x) to enlarge gameplay to your preference; it never affects the webcam and can crop gameplay edges. There is no separate Gameplay framing control. Reopening a saved layout preserves the chosen zoom. Webcam content is bottom-aligned and gameplay top-aligned so their foregrounds meet directly; extra vertical blurred fill sits above or below, never between the panels. Source rectangles may overlap and webcam selection never automatically excludes camera coordinates from gameplay. Captions remain on the final canvas below the webcam panel. The live preview shows composition, not captions that have not yet been transcribed.

Source-specific webcam selection resets for a new source and is saved in normal/benchmark job options and export plans. Renderer10/cache keys include the full plan so new layouts do not reuse older renders; completed MP4s remain unchanged. Earlier explicit zoom/framing fields remain readable for existing API clients and plans, but the current editor always saves an unzoomed webcam and aspect-fitted gameplay. Crops are fixed for the job, not face tracking or dynamic camera detection. Gameplay hidden beneath the original camera cannot be reconstructed, and moving/toggling webcam layouts need user review. No face-detection model or cloud service is added.

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