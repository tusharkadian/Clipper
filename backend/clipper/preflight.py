from __future__ import annotations

import importlib.util
import importlib.metadata
import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import ROOT, configure_gpu_libraries


def executable(name: str) -> str | None:
    override = os.environ.get(f"CLIPPER_{name.upper()}")
    if override:
        candidate = Path(override)
        return str(candidate.resolve()) if candidate.is_file() else None
    found = shutil.which(name)
    if found:
        return found
    if name == "nvidia-smi" and os.name == "nt":
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        return str(candidate) if candidate.is_file() else None
    return None


def capture(arguments: list[str], timeout: float = 10) -> str:
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def parse_gpu_rows(output: str) -> list[dict[str, Any]]:
    devices = []
    for row in output.splitlines():
        values = [value.strip() for value in row.split(",")]
        if len(values) != 6:
            continue
        def number(value: str) -> float | None:
            try:
                return float(value)
            except ValueError:
                return None
        devices.append({
            "name": values[0], "driver": values[1],
            "dedicated_mb": number(values[2]), "free_mb": number(values[3]),
            "temperature_c": number(values[4]), "power_w": number(values[5]),
        })
    return devices


def cuda_libraries_ready() -> bool:
    if os.name != "nt":
        return False
    configure_gpu_libraries()
    try:
        ctypes.WinDLL("cublas64_12.dll")
        ctypes.WinDLL("cudnn64_9.dll")
        return True
    except OSError:
        return False


def inspect_system(workspace: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(workspace)
    report: dict[str, Any] = {
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
        "ram_gb": None, "free_disk_gb": round(disk.free / 1024**3, 1),
        "workspace": str(workspace.resolve()), "cloud_enabled": False,
        "tools": {}, "gpus": [], "warnings": [], "ready": False,
        "speech_installed": importlib.util.find_spec("faster_whisper") is not None,
        "cpu_temperature_c": None,
        "cuda_runtime_ready": cuda_libraries_ready(),
        "cuda_library_dirs": configure_gpu_libraries(),
    }
    try:
        import psutil
        report["ram_gb"] = round(psutil.virtual_memory().total / 1024**3, 1)
    except ImportError:
        report["warnings"].append("Install base dependencies to read physical RAM.")
    for name in ("ffmpeg", "ffprobe"):
        path = executable(name)
        info: dict[str, Any] = {"path": path, "available": False}
        if path:
            try:
                info["version"] = capture([path, "-version"]).splitlines()[0]
                info["available"] = True
                if name == "ffmpeg":
                    filters = capture([path, "-hide_banner", "-filters"])
                    encoders = capture([path, "-hide_banner", "-encoders"])
                    info["subtitles"] = any(" subtitles " in line for line in filters.splitlines())
                    info["scene_detection"] = any(" scdet " in line for line in filters.splitlines())
                    info["libx264"] = "libx264" in encoders
                    info["nvenc_listed"] = "h264_nvenc" in encoders
            except (OSError, subprocess.SubprocessError, IndexError) as error:
                info["error"] = type(error).__name__
        report["tools"][name] = info
    gpu_tool = executable("nvidia-smi")
    if gpu_tool:
        try:
            output = capture([
                gpu_tool, "--query-gpu=name,driver_version,memory.total,memory.free,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ])
            report["gpus"] = parse_gpu_rows(output)
        except (OSError, subprocess.SubprocessError):
            report["warnings"].append("NVIDIA telemetry unavailable; GPU support is unverified.")
    if not report["gpus"]:
        report["warnings"].append("No NVIDIA telemetry detected. CPU processing remains available.")
    elif not report["cuda_runtime_ready"]:
        report["warnings"].append("CUDA 12/cuDNN 9 libraries are not loadable. Automatic transcription uses CPU INT8.")
    report["warnings"].append("CPU temperature is unavailable; this report cannot certify thermal safety.")
    if disk.free < 20 * 1024**3:
        report["warnings"].append("Less than 20 GiB free; processing is blocked to preserve OS headroom.")
    ffmpeg = report["tools"]["ffmpeg"]
    report["ready"] = bool(
        ffmpeg["available"] and report["tools"]["ffprobe"]["available"]
        and ffmpeg.get("subtitles") and ffmpeg.get("scene_detection") and ffmpeg.get("libx264") and disk.free >= 20 * 1024**3
    )
    if not report["ready"]:
        report["warnings"].append("Media processing needs working FFmpeg/FFprobe with libass, scdet and libx264, and disk headroom.")
    return report


def print_report(workspace: Path) -> None:
    print(json.dumps(inspect_system(workspace), indent=2))


def benchmark_environment(workspace: Path) -> dict:
    inspected = inspect_system(workspace)
    packages = {}
    for package in ("local-clipper", "faster-whisper", "ctranslate2", "numpy", "psutil", "yt-dlp"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    models = {}
    for name in ("small.en", "qwen2.5-1.5b-instruct-q4"):
        manifest = workspace / "models" / name / "manifest.json"
        if manifest.is_file():
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
                models[name] = {key: value[key] for key in ("repository", "revision", "model_sha256", "runtime_build") if key in value}
            except (OSError, ValueError, TypeError):
                models[name] = {"manifest": "unreadable"}
    source = hashlib.sha256()
    for path in sorted((ROOT / "backend/clipper").glob("*.py")):
        source.update(path.name.encode())
        source.update(path.read_bytes())
    lock = ROOT / "requirements.lock.txt"
    return {"version": 1,
            **{key: inspected[key] for key in ("platform", "python", "cpu", "logical_cpus", "ram_gb", "cuda_runtime_ready")},
            "gpus": [{key: gpu.get(key) for key in ("name", "driver", "dedicated_mb")} for gpu in inspected["gpus"]],
            "tools": {name: {key: value[key] for key in ("available", "version", "subtitles", "libx264") if key in value}
                      for name, value in inspected["tools"].items()},
            "packages": packages, "models": models, "backend_sha256": source.hexdigest(),
            "requirements_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None}