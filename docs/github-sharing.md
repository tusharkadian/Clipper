# Public GitHub Preparation

Owner-selected application license: Apache-2.0, Copyright2026 Tushar Kadian. The repository is intended for developer alpha testing, not guaranteed posting-ready accuracy. Nothing is automatically published by these scripts.

## Source-Only Checklist

1. Run the full backend suite without excluding the cache regression, build the frontend and run browser checks. Optional actual-model tests remain separately identified.
2. Review LICENSE, NOTICE and THIRD_PARTY_NOTICES.md. Public source must not contain downloaded tool/model binaries or built UI dependencies. Do not upload the older asset-containing testing ZIPs; their redistribution obligations are not cleared by this source license.
3. Build ./releases/clipper-source-alpha.zip using clipper package --source-only. Verify and inspect its actual manifest/archive. It deliberately excludes personal benchmarks, private history, queues, environments and media.
4. Extract the source-only ZIP into a new folder. Review every file before publishing; an allowlist and .gitignore cannot prove that source text contains no secrets. Enable GitHub secret scanning/push protection where available.
5. Create the empty public GitHub repository yourself. Initialize Git only in the extracted project folder, not the parent multi-project workspace. Review git status and the staged diff before the first commit. git ls-files -ci --exclude-standard must not list ignored private/generated files; ignore rules do not untrack previously committed files.
6. Push only after the review. Do not grant CI extra write permissions or use privileged pull_request_target for untrusted contributions. The prepared workflow is read-only and does not upload binaries or publish releases.
7. Enable private vulnerability reporting, review the issue templates, and confirm the Actions workflow actually passes on GitHub. Local YAML checks are not evidence of a remote CI run.

GitHub Clone/Download ZIP users run setup directly; GitHub-generated archives do not contain transfer-manifest.json. CLI-created source ZIPs do contain it and can be verified before setup. Initial dependency/tool/model downloads require internet; CUDA and Python are installed separately.

## Public Diagnostic Policy

Use the diagnostics command for allowlisted status/numeric metadata. It deliberately excludes raw error messages, logs/warnings, transcript words/evidence, source URLs/names, job IDs, local paths and arbitrary report fields. Environment metadata describes the machine/software and is not anonymous. Inspect even the restricted summary before attaching it.

Do not post source videos, screenshots, captions or unsupported-language transcripts without authorization and privacy review. Describe quality failures in words and distinguish confidence diagnostics from measured transcription or highlight accuracy.

## Alpha Scope

Packaging and tests enable developer evaluation only. Accuracy gates, correction/re-export tools, real second-machine validation and publishing checks remain in [architecture.md](architecture.md#developer-alpha-task-list). Creators should review every clip before posting and obtain source/music rights. No clip usefulness, virality, GPU lifetime or four-hour capacity guarantee is made.