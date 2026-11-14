import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from clipper.preflight import inspect_system, parse_gpu_rows


class PreflightTests(unittest.TestCase):
    def test_only_dedicated_gpu_memory_is_reported(self):
        devices = parse_gpu_rows("RTX 500 Ada, 566.00, 4096, 3012, 45, [N/A]")
        self.assertEqual(devices[0]["dedicated_mb"], 4096)
        self.assertIsNone(devices[0]["power_w"])

    def test_missing_tools_are_not_ready(self):
        with TemporaryDirectory() as directory, patch("clipper.preflight.executable", return_value=None):
            report = inspect_system(Path(directory))
        self.assertFalse(report["ready"])
        self.assertFalse(report["cloud_enabled"])
        self.assertIsNone(report["cpu_temperature_c"])

    def test_broken_executable_is_actionable(self):
        with TemporaryDirectory() as directory, patch("clipper.preflight.executable", return_value="missing"), patch(
            "clipper.preflight.capture", side_effect=subprocess.TimeoutExpired("missing", 10)
        ):
            report = inspect_system(Path(directory))
        self.assertFalse(report["ready"])
        self.assertEqual(report["tools"]["ffmpeg"]["error"], "TimeoutExpired")


if __name__ == "__main__":
    unittest.main()


def test_windows_discovers_installed_cuda12_cudnn_without_inherited_path(tmp_path):
    import os
    from unittest.mock import Mock
    from clipper.config import configure_gpu_libraries
    if os.name != "nt":
        return
    cuda = tmp_path / "NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin"
    cudnn = tmp_path / "NVIDIA/CUDNN/v9.28/bin/12.9/x64"
    incompatible = tmp_path / "NVIDIA/CUDNN/v9.28/bin/13.5/x64"
    for directory, filename in ((cuda, "cublas64_12.dll"), (cudnn, "cudnn64_9.dll"), (incompatible, "cudnn64_9.dll")):
        directory.mkdir(parents=True)
        (directory / filename).touch()
    with patch.dict(os.environ, {"PATH": "", "ProgramFiles": str(tmp_path)}, clear=True), patch(
        "clipper.config._DLL_DIRECTORIES", {}
    ), patch("clipper.config.os.add_dll_directory", return_value=Mock()) as register:
        directories = configure_gpu_libraries()
        repeated = configure_gpu_libraries()
        assert str(cuda.resolve()) in directories
        assert str(cudnn.resolve()) in directories
        assert str(incompatible.resolve()) not in directories
        assert repeated == directories
        assert register.call_count == 2
        assert str(cudnn.resolve()) in os.environ["PATH"]


def test_cuda_readiness_registers_dll_directories_before_loading():
    import os
    from clipper.preflight import cuda_libraries_ready
    if os.name != "nt":
        return
    calls = []
    with patch("clipper.preflight.configure_gpu_libraries", side_effect=lambda: calls.append("configure")), patch(
        "clipper.preflight.ctypes.WinDLL", side_effect=lambda name: calls.append(name)
    ):
        assert cuda_libraries_ready()
    assert calls == ["configure", "cublas64_12.dll", "cudnn64_9.dll"]


def test_benchmark_environment_omits_local_paths_and_host_identity(tmp_path):
    import json
    from clipper.preflight import benchmark_environment
    system = {"platform": "Windows-test", "python": "3.12", "cpu": "test CPU", "logical_cpus": 8,
              "ram_gb": 16, "cuda_runtime_ready": False, "workspace": "C:/private/person/data",
              "cuda_library_dirs": ["C:/private/person/CUDA"],
              "gpus": [{"name": "GPU", "driver": "test", "dedicated_mb": 4096, "free_mb": 2048}],
              "tools": {"ffmpeg": {"available": True, "version": "ffmpeg test", "path": "C:/private/person/ffmpeg.exe"}}}
    with patch("clipper.preflight.inspect_system", return_value=system):
        report = benchmark_environment(tmp_path)
    assert "private" not in json.dumps(report)
    assert len(report["backend_sha256"]) == 64
    assert report["gpus"][0]["dedicated_mb"] == 4096