# Developer Alpha

This is an experimental Windows-first clipper, not a validated viral predictor. Contributions are accepted under the repository's Apache-2.0 license unless explicitly agreed otherwise. Confirm you have the rights to contribute source and test material.

## Local Setup

Install Python3.12 x64 on PATH. From this repository, run ./scripts/setup.ps1 -InstallModel and ./scripts/start.ps1. Model installation is explicit; use -InstallReranker only when testing the optional local AI stage. CPU mode is supported; CUDA drivers/libraries are manual optional prerequisites. Internet is required for initial setup.

Run the full backend suite with ./.venv/Scripts/python.exe -m pytest backend/tests -q. Do not exclude the cache-state regression. The optional downloaded-model check uses CLIPPER_REAL_MODEL_TEST=1; do not confuse synthetic smoke tests with content-quality acceptance.

For browser smoke tests, run ./scripts/test-fixture.ps1, install Playwright Chromium using the pinned local Node and Playwright CLI, and run the web test configuration with the app running. The synthetic fixture uses local Windows speech and FFmpeg patterns; no private gameplay is required. The CI workflow shows exact commands.

Keep tests focused while editing. Before a release, run the full suite, build the frontend, validate source-only packaging and review the final Git diff/archive. Preserve existing user recordings and unrelated changes. Follow docs/architecture.md for alpha milestones and proposed, not achieved, clip-accuracy targets.

## Public Issues

Use the issue forms. Generate a diagnostic summary with ./.venv/Scripts/python.exe -m clipper.cli diagnostics --report path/to/report.json --output diagnostic-summary.json, then inspect it before attaching. Raw job/benchmark reports, screenshots, captions and logs can reveal private content. Do not post them automatically or upload secrets, source videos or copyrighted evidence. Source snippets require the owner's permission and deliberate redaction.

Keep services on loopback. Do not add accounts, publishing integration, cloud fallback or larger models without explicit design review. Report security problems privately as described in SECURITY.md.