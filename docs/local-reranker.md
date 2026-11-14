# Local Transcript Reranker

## Model Choice

| Candidate | License | Decision |
| --- | --- | --- |
| Qwen2.5-1.5B-Instruct | Apache-2.0 | Selected: 1.54B parameters, official quantized GGUF, small CPU footprint and structured-output capability. |
| Qwen2.5-3B-Instruct | Qwen Research license | Not selected: heavier and a less generally permissive license than the selected release. |
| Phi-3.5-mini-instruct | MIT | Viable future comparison, but 3.8B parameters increases memory/CPU work; no clip-ranking advantage was established here. |

References: [selected model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), [official GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF), [Qwen 3B card/license](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct), [Phi card/license](https://huggingface.co/microsoft/Phi-3.5-mini-instruct), [llama.cpp](https://github.com/ggml-org/llama.cpp).

The model is general-purpose, not trained or validated to predict viral clips. Instruction following does not establish ranking quality.

## Installed Assets

- Model: `Qwen/Qwen2.5-1.5B-Instruct-GGUF`, revision `91cad51170dc346986eccefdc2dd33a9da36ead9`.
- File: `qwen2.5-1.5b-instruct-q4_k_m.gguf`, 1,117,320,736 bytes (~1.04 GiB).
- SHA-256: `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`.
- CPU runtime: official Windows x64 llama.cpp build `b12110`, selected by the v0.9.0 release pointer; SHA-256 `98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2`.
- Location: `.data/models/qwen2.5-1.5b-instruct-q4`. The installer verifies hashes, saves a manifest and includes the model license. This is a local development download, not a redistribution package; review all runtime/model notices before packaging.

Install explicitly:

```powershell
./.venv/Scripts/clipper.exe reranker-install
```

Or use Install local reranker in the UI / `./scripts/setup.ps1 -InstallReranker`. Already installed on the current workstation. No account, payment, CUDA or PyTorch is required. Internet is used for setup downloads, not inference. Windows can briefly lock the staging directory; a verified move/copy fallback handles that without elevation. A leftover `.installing` directory can be removed once the final model is verified and no install is active.

## Execution and Safety

The heuristic proposes a diversified non-overlapping shortlist before its final export cap: up to three times the desired output count, minimum six when available, maximum20. Reranking can promote candidates outside the original top few. It cannot recover moments absent from that shortlist.

Each candidate supplies up to2,400 characters of the English model's transcript output, divided into bounded evidence snippets, with a quality summary. Actual spoken language is unverified. The prompt treats transcripts as untrusted data and escapes role delimiters. A constrained JSON schema restricts candidate ID, evidence index and four integer ratings (0-4): hook, payoff, clarity, novelty. The application validates every assessment, resolves evidence from original snippets, and never accepts model-proposed timestamps or commands. Prompt injection resistance is not a guarantee of correct subjective scores.

CPU-only llama.cpp uses zero GPU layers, context2048tokens, one server slot, quiet mode4threads/balanced up to8, output192tokens/candidate and a240second inference budget. Its ephemeral local HTTP server binds127.0.0.1 with a random local token, ignores proxy settings and disables its web UI. The parent worker supports cancellation and process-tree cleanup. It runs after ASR exits and exits before rendering; no additional GPU memory is required for this stage. Disk weight size is not a RAM-usage measurement: context/runtime buffers add memory.

Validated semantic scores blend60% with40% of original activity scores. Silent candidates retain activity scores. This is an experimental uncalibrated combination, not a probability. Missing model, invalid/incomplete output or timeout produces visible heuristic fallback. If total ratings differ by at most2/16 across assessed contexts, the app reports `no_separation` and keeps the original scores/order rather than pretending flat ratings added evidence. Other valid but incorrect subjective assessments remain possible.

With at least three assessed contexts, if at least75% receive totals14-16/16, the app instead reports `saturated_scores` and retains original activity selection. This catches the observed five-perfect/one-zero pattern that a max-minus-min spread check misses. Flat-score checking still takes precedence. Diagnostics expose score spread and near-perfect fraction; fallback reports remain method=activity-v1 and are never labelled AI reranked.

## Transcript Health Policy

These are conservative heuristic warning thresholds, not calibrated error rates or language/semantic verification:

- Word confidence: at least five measured probabilities covering at least half the word entries; flag mean<0.45 or at least50% below0.35. Missing/nonfinite metrics remain unknown, not automatically bad.
- Repetition: at least24normalized tokens and repeated-trigram fraction>=0.60. Legitimate repeated dialogue can trigger this; activity clips are not discarded.
- Segments: flag when at least half of segments with available metrics have avg_logprob<-1 or compression_ratio>2.4. no_speech_prob is retained for diagnosis, not used alone to reject gameplay. Chunk-overlap duplicates do not create duplicate diagnostic entries.
- Language: small.en is English-only and transcription currently forces en. The report always records detected=null/verified=false. Confidence or ASCII text does not establish spoken language; no multilingual detector/model was added.

Per-window health is computed before AI assessment. Unreliable windows retain activity scores rather than passing their text to Qwen; if all eligible contexts are unreliable, status is `unreliable_transcript` and no model process starts. Mixed-quality and silent candidates remain in the shortlist. During activity proposal, unreliable local text also bypasses sentence-boundary rejection, so a bad patch cannot suppress its activity peak; reliable speech still uses the normal boundary logic. Captions are still exported with a warning and may be inaccurate.

Clip and benchmark reports contain `transcript` status/metrics/reasons and language limitations. Ranking reports contain guard_version=1, excluded_unreliable, candidate-level transcript_quality and assessment diagnostics when inference ran. Rerank cache keys include guard version and quality-aware contexts; cached assessments are revalidated. Legacy speech receipts remain reusable without forcing ASR merely to obtain segment metrics. No training, model swap, new dependencies or cloud analysis was introduced.

## Execution Controls

The UI defaults Local AI reranking on for new clip jobs; users can disable it. Turning captions off does not disable AI if a transcript can be produced; caption burn-in remains off. Benchmark mode still tests fixed intervals and does not apply highlight ranking. Existing completed exports are not modified; queue a new recording run to use the feature.

## Evidence and Limitations

Real CPU inference succeeded, the temporary runtime exited, and an approximately9second two-window check ranked routine waiting below an explicit last-second payoff. After calibration fixes, the constrained evidence-ID version assessed nine saved windows from a real recording in about33seconds. The ratings were optimistic and almost uniform, so the no-separation policy retains activity ranking for that case. These short measurements are not a four-hour throughput promise or evidence of improved audience engagement.

Early experiments exposed two real failure modes: calibration examples anchoring every score, and model-generated quotes failing exact evidence checks. The prompt removed the high-score example and the output contract switched to supplied evidence IDs. Keep the validation/fallback tests when changing prompts or models.

Next acceptance work is a human-rated held-out set across games/content types, comparing kept and rejected moments, rank changes, duplicates and usefulness. Include quiet-action footage and narration-heavy content. A text model cannot verify a silent sports play, facial expression or kill feed; visual/game-specific models are separate future work.

Tests:

```powershell
$env:CLIPPER_REAL_MODEL_TEST = '1'
./.venv/Scripts/python.exe -m pytest backend/tests/test_rerank.py -q
```

Without the flag, normal tests cover contracts/fallbacks without loading the downloaded model. Job reports include the actual ranking method/status, shortlisted/assessed counts, model hash and CPU-only setting. Applied rankings include evidence and separate semantic/activity scores. Inference inputs, raw output, logs and receipts follow the job's seven-day retention; model weights do not expire with jobs.