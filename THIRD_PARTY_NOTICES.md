# Third-Party Inventory

Clipper's original source is Apache-2.0, Copyright2026 Tushar Kadian. That does not relicense dependencies, model weights, fonts or media. This inventory records observed upstream declarations; it is not legal clearance for a binary distribution.

## Public GitHub Distribution Policy

Publish application source, tests and documentation only. Do not commit or attach .tools, .data, .venv, node_modules, built frontend assets or old asset-containing ZIPs. Developers obtain upstream dependencies locally through setup and explicit model commands. Do not redistribute downloaded artifacts until their exact notices and corresponding-source obligations have been reviewed and fulfilled. Retain LICENSE and NOTICE with Clipper source and notices from any third-party code you modify or redistribute.

## Tools and Models Downloaded Locally

| Component | Current selection | Upstream terms / review requirement |
| --- | --- | --- |
| Python |3.12 x64, installed separately|Python/PSF terms; not shipped by this repository.|
| Node.js |24.22.2 Windows x64|Node license and bundled third-party notices; download archive hash is pinned in setup.|
| FFmpeg |Gyan9.0.2 essentials|Exact build LICENSE and FFmpeg/library configuration control obligations. Review GPL/LGPL and corresponding source before any binary redistribution; external invocation does not waive distribution obligations.|
| small.en speech weights |Systran/faster-whisper-small.en|Review the publisher model card and original Whisper/OpenAI model license/attributions. The local revision manifest is provenance, not license clearance; weights are not shipped publicly.|
| Qwen GGUF |Qwen2.5-1.5B-Instruct Q4_K_M|Publisher declares Apache-2.0; retain exact model license/notice obligations if redistributed. Pinned hash/revision in rerank.py.|
| llama.cpp runtime |b12110 CPU Windows archive|MIT project plus bundled runtime libraries, including LLVM/OpenMP notices; review the exact archive before redistribution.|

Model references: [speech model](https://huggingface.co/Systran/faster-whisper-small.en), [Whisper](https://github.com/openai/whisper), [Qwen GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF), [llama.cpp](https://github.com/ggml-org/llama.cpp). FFmpeg licensing: [FFmpeg legal guidance](https://ffmpeg.org/legal.html).

## Locked Python Dependencies

Versions are authoritative in requirements.lock.txt. The following grouping records installed metadata declarations for the current lock; mixed terms and third-party wheel contents require their own notices if redistributed.

| Observed declaration | Packages |
| --- | --- |
| MIT |annotated-types, anyio, brotli, charset-normalizer, ctranslate2, fastapi, faster-whisper, filelock, h11, iniconfig, pluggy, pydantic, pydantic_core, pytest, PyYAML, truststore, typing-inspection, urllib3|
| BSD / BSD-3-Clause |av, click, colorama, fsspec, httpcore, httpcore2, httpx, httpx2, idna, protobuf, psutil, starlette, uvicorn, websockets|
| Apache-2.0 |flatbuffers, hf-xet, huggingface_hub, python-multipart, requests, tokenizers|
| Apache-2.0 OR BSD-2-Clause |packaging|
| GPL-2.0-or-later |mutagen|
| MPL-2.0 |certifi|
| MPL-2.0 AND MIT |tqdm|
| BSD-3-Clause AND0BSD AND MIT AND Zlib AND CC0-1.0 |numpy|
| BSD / public domain components |pycryptodomex|
| PSF-2.0 |typing_extensions|
| Unlicense |yt-dlp|
| Unlicense AND MIT AND ISC |yt-dlp-ejs|

onnxruntime's observed declaration is MIT. PyAV wheels can include FFmpeg-derived libraries with additional obligations beyond PyAV's BSD source license. Source-only GitHub sharing is not permission to redistribute those wheels without review.

## Frontend and Test Tooling

The private evaluation commands add JiWER4.0.0 and RapidFuzz3.14.6 (upstream Apache-2.0 and MIT declarations respectively). They compute reference-based word-error metrics; model confidence is not a substitute. They are installed dependencies, not binaries shipped in the public source archive.

Direct declarations from package-lock.json: React/ReactDOM, Vite, its React plugin and TypeScript type packages declare MIT; lucide-react declares ISC; TypeScript and Playwright declare Apache-2.0. The full lock includes transitive packages with their own licenses. node_modules and built UI bundles are excluded from the public source artifact. Before distributing a built UI, review the exact bundled dependency notices and include them as required.

No rights to gameplay recordings, music, creator footage, platform branding or publishing are granted by the application license. Testers must use authorized recordings and review their clips before posting.