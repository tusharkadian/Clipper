from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RETENTION_SECONDS = 7 * 24 * 60 * 60
_DLL_DIRECTORIES: dict[str, object] = {}


@dataclass(frozen=True)
class Settings:
    data: Path
    reserve_bytes: int = 20 * 1024**3
    workspace_limit_bytes: int = 60 * 1024**3
    max_duration: float = 6 * 3600
    max_upload_bytes: int = 30 * 1024**3

    @classmethod
    def load(cls) -> Settings:
        return cls(Path(os.environ.get("CLIPPER_DATA") or ROOT / ".data").resolve())

    def prepare(self) -> None:
        for folder in (self.data, self.data / "imports", self.data / "runs", self.data / "models"):
            folder.mkdir(parents=True, exist_ok=True)


def configure_gpu_libraries() -> list[str]:
    if os.name != "nt":
        return []
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    candidates = []
    for variable in ("CLIPPER_CUDA_BIN", "CLIPPER_CUDNN_BIN"):
        if os.environ.get(variable):
            candidates.append(Path(os.path.expandvars(os.environ[variable])))
    candidates.extend(Path(os.path.expandvars(entry.strip('"'))) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
    if os.environ.get("CUDA_PATH"):
        candidates.append(Path(os.environ["CUDA_PATH"]) / "bin")
    cuda_root = program_files / "NVIDIA GPU Computing Toolkit/CUDA"
    candidates.extend(sorted(cuda_root.glob("v12.*/bin"), reverse=True))
    cudnn_root = program_files / "NVIDIA/CUDNN"
    for version in sorted(cudnn_root.glob("v9*"), reverse=True):
        candidates.extend(sorted((version / "bin").glob("12*/x64"), reverse=True))
        candidates.extend(sorted((version / "bin").glob("12*"), reverse=True))
        candidates.append(version / "bin")
    directories = []
    for directory in candidates:
        if not any((directory / filename).is_file() for filename in ("cublas64_12.dll", "cudnn64_9.dll")):
            continue
        resolved = str(directory.resolve())
        if resolved not in directories:
            directories.append(resolved)
        if resolved not in _DLL_DIRECTORIES:
            try:
                _DLL_DIRECTORIES[resolved] = os.add_dll_directory(resolved)
            except OSError:
                continue
    entries = os.environ.get("PATH", "").split(os.pathsep)
    additions = [directory for directory in directories if directory not in entries]
    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, *entries])
    return directories


def configure_tools() -> None:
    configure_gpu_libraries()
    tools = ROOT / ".tools"
    candidates = sorted(tools.glob("ffmpeg-*/bin")) if tools.exists() else []
    if candidates:
        os.environ["PATH"] = str(candidates[-1]) + os.pathsep + os.environ.get("PATH", "")
    for candidate in sorted(tools.glob("node-*-win-x64")) if tools.exists() else []:
        os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")