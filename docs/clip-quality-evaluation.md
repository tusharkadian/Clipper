# Human-Labeled Clip Quality Evaluation

This workflow prepares P1 evidence; it does not automatically certify clip accuracy. Keep datasets under .data/evaluation or another private directory, never in a public GitHub repository. Private manifests/reference text are not safe issue attachments.

## Prepare Existing Runs

Use authorized retained recordings and optional saved-report JSON files:

```powershell
./.venv/Scripts/python.exe -m clipper.cli evaluation-init --reports 'D:/PrivateReports' --output './.data/evaluation/review-set'
./.venv/Scripts/python.exe -m clipper.cli evaluation-replay --manifest './.data/evaluation/review-set/manifest.json' --output './.data/evaluation/review-set/activity-replay.json'
```

The initializer creates one review JSON per unique source and a private manifest linking clips, source, transcript/features and expiry. It matches saved reports to original runs, deduplicates source hashes and never invents labels. It references media, not copies; seven-day managed retention still applies. Keep authorized originals yourself if evaluation must outlive retention. Existing directories/results are never overwritten.

Cached replay runs only activity selection on saved features/transcripts. It records selected intervals and agreement with historical exports, not accuracy. Saved clips can come from older code. No source decode, ASR or AI inference is rerun. AI superiority requires the same held-out sources/settings and human reviews for each selection method, not agreement with its own previous predictions.

## Review Each Recording

Read the private manifest to locate source/clip paths. Open clips in a local player and compare against the source. Edit the corresponding recording_NN.review.json:

1. Fill game_or_format, language (en for the current supported evaluation), reviewed_by and reviewed_at. Never identify model-generated judgments as human review.
2. Set each clip's usable, boundary_ok, repeated_event and critical_caption_error to true/false only after review. Null means unreviewed, not correct. Usable means the clip has a worthwhile self-contained moment and needs no boundary/layout repair. Critical caption errors include wrong names, negations or meaning-changing text.
3. Fill desired_moments using intervals identified from the full source, including missed quiet plays. Do not label only exported intervals; that would hide recall failures. Empty means the source truly has no desired moments; null means not annotated.
4. Transcribe speech samples from listening to the source and fill reference_text. hypothesis_text is the existing ASR output, not the reference. Reference null means unreviewed; an empty string deliberately labels actual silence and permits hallucination counting. Include game names and negations.
5. Keep these previously used recordings as development data. Do not change previously_used_for_tuning merely to satisfy a holdout count. Add at least three genuinely untuned holdout recordings, within a10-20recording set across at least three games/formats, before claiming the proposed gate.

The schema rejects invalid intervals, duplicate clip/recording/source identities and sources reused across development/holdout splits. It cannot prove a human listened or that a self-declared holdout was never viewed; reviewer honesty and dataset provenance still matter.

## Measure

```powershell
./.venv/Scripts/python.exe -m clipper.cli evaluate --manifest './.data/evaluation/review-set/manifest.json' --output './.data/evaluation/review-set/human-evaluation.json'
```

JiWER measures substitutions/deletions/insertions against human reference text, with case/punctuation normalization. Word error rate can exceed100%; it is not model confidence. Silence-only references report hallucinated words without claiming zero WER. Desired-moment recall uses at least50% coverage of each labeled interval. Per-recording metrics expose incomplete reviews, boundaries, repeated events, critical captions and recall.

Proposed held-out targets: at least80% useful exported clips, at most10% caption WER and at most10% repeated events, with no remaining critical caption errors. The gate requires at least10unique recordings, three labeled games/formats, three untuned holdout sources, completed human clip reviews, speech references and desired-moment annotations. Missing evidence is reported as insufficient_evidence; it never becomes a passing score. Failed targets remain failed. These targets are not current achievements or publishing/virality guarantees.

Use baseline failures to choose the next experiment: local speech decoding/language support for bad captions, ranking alternatives only when they beat activity-only selection, and visual/event duplicate logic for confirmed replays. Do not swap a default model or inflate scores merely to make the report pass.