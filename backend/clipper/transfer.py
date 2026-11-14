from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile


SOURCE_PATHS = ("README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "SECURITY.md",
                "pyproject.toml", "requirements.lock.txt", ".gitignore", ".github", "backend/clipper",
                "backend/tests", "scripts", "docs/architecture.md", "docs/testing-other-windows.md",
                "docs/local-reranker.md", "docs/gpu-setup.md", "docs/github-sharing.md",
                "docs/clip-quality-evaluation.md",
                "apps/web/src", "apps/web/dist", "apps/web/tests",
                "apps/web/index.html", "apps/web/package.json", "apps/web/package-lock.json",
                "apps/web/tsconfig.json", "apps/web/vite.config.ts", "apps/web/playwright.config.ts")
TOOL_PATHS = (".tools/node-v24.22.2-win-x64", ".tools/ffmpeg-9.0.2-essentials_build")
MODEL_PATHS = (".data/models/small.en", ".data/models/qwen2.5-1.5b-instruct-q4")
SOURCE_SUFFIXES = {"backend/clipper": {".py"}, "backend/tests": {".py"}, "scripts": {".ps1"},
                   ".github": {".yml", ".yaml", ".md"}, "apps/web/src": {".ts", ".tsx", ".css", ".json", ".svg", ".png", ".jpg", ".webp"},
                   "apps/web/tests": {".ts"}}


def linked(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def files_below(root: Path, relative: str):
    source = root / relative
    if not source.exists():
        return
    if linked(source) or not source.resolve().is_relative_to(root):
        raise ValueError(f"Refusing linked or external bundle input: {relative}")
    if source.is_file():
        yield source
        return
    for directory, folders, files in os.walk(source, followlinks=False):
        current = Path(directory)
        for name in folders:
            if linked(current / name):
                raise ValueError(f"Refusing linked bundle directory: {name}")
        folders[:] = sorted(name for name in folders if name not in {"__pycache__", ".pytest_cache"})
        for name in sorted(files):
            path = current / name
            if name.endswith((".pyc", ".pyo", ".tsbuildinfo", ".log")):
                continue
            if linked(path) or not path.resolve().is_relative_to(root):
                raise ValueError(f"Refusing linked bundle file: {name}")
            yield path


def create_bundle(root: Path, destination: Path, include_tools: bool = False, include_models: bool = False,
                  source_only: bool = False) -> dict:
    root = root.resolve()
    destination = destination.resolve()
    if source_only and (include_tools or include_models):
        raise ValueError("Source-only GitHub exports cannot include downloaded tools or models.")
    if not source_only and not (root / "apps/web/dist/index.html").is_file():
        raise ValueError("Build the frontend before packaging a test bundle.")
    paths = [*(path for path in SOURCE_PATHS if not source_only or path != "apps/web/dist"),
             *(TOOL_PATHS if include_tools else ()), *(MODEL_PATHS if include_models else ())]
    sources = sorted({path for relative in paths for path in files_below(root, relative)
                      if relative not in SOURCE_SUFFIXES or path.suffix in SOURCE_SUFFIXES[relative]})
    manifest = {"format": 1, "application": "clipper-windows-test", "created_utc": datetime.now(timezone.utc).isoformat(),
                "python": "3.12 x64 recommended; install separately", "tools_included": include_tools,
                "models_included": include_models, "files": []}
    manifest["source_only"] = source_only
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                for source in sources:
                    relative = source.relative_to(root).as_posix()
                    info = zipfile.ZipInfo.from_file(source, "clipper/" + relative)
                    info.compress_type = zipfile.ZIP_STORED if source.suffix in {".gguf", ".bin"} else zipfile.ZIP_DEFLATED
                    digest, size = hashlib.sha256(), 0
                    with source.open("rb") as content, archive.open(info, "w", force_zip64=True) as member:
                        for chunk in iter(lambda: content.read(1024 * 1024), b""):
                            member.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    manifest["files"].append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
                archive.writestr("clipper/transfer-manifest.json", json.dumps(manifest, indent=2))
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return manifest


def verify_bundle(root: Path) -> dict:
    root = root.resolve()
    manifest = json.loads((root / "transfer-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("application") != "clipper-windows-test":
        raise ValueError("Unsupported transfer manifest.")
    seen = set()
    for item in manifest["files"]:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in str(relative) or "\\" in str(relative) or str(relative) in seen:
            raise ValueError("Unsafe or repeated manifest path.")
        seen.add(str(relative))
        path = root.joinpath(*relative.parts)
        if any(linked(parent) for parent in [path, *path.parents] if parent != root and parent.is_relative_to(root)):
            raise ValueError("Linked transfer path.")
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size != item["bytes"]:
            raise ValueError(f"Missing or altered transfer file: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as content:
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError(f"Transfer checksum mismatch: {relative}")
    return {"verified": True, "files": len(seen), "tools_included": manifest["tools_included"],
            "models_included": manifest["models_included"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify an extracted Clipper test bundle before setup.")
    parser.add_argument("--verify", type=Path, required=True)
    print(json.dumps(verify_bundle(parser.parse_args().verify), indent=2))