import json
import sys
import time
from unittest.mock import patch

import pytest

from clipper.cli import main
from clipper.config import Settings
from clipper.domain import RunOptions
from clipper.storage import Store


@pytest.mark.parametrize("blocker", ["worker", "queued", "paused"])
def test_cli_benchmark_refuses_shared_active_workspace_before_creating_job(tmp_path, monkeypatch, blocker):
    settings = Settings(tmp_path / "data", reserve_bytes=0)
    settings.prepare()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not decoded in this test")
    store = Store(settings.data / "jobs.sqlite3")
    if blocker == "worker":
        (settings.data / "worker.json").write_text(json.dumps({"updated": time.time()}))
    else:
        job = store.create("existing", "file", str(source), RunOptions().model_dump())
        if blocker == "paused":
            store.control(job["id"], "pause")
    before = len(store.list())
    monkeypatch.setattr(sys, "argv", ["clipper", "benchmark", str(source)])
    with patch("clipper.cli.Settings.load", return_value=settings), patch("clipper.worker.work") as worker:
        with pytest.raises(SystemExit) as result:
            main()
    assert result.value.code == 2
    assert len(store.list()) == before
    worker.assert_not_called()


def test_targeted_claim_does_not_consume_an_older_queued_job(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    older = store.create("user recording", "file", "original.mp4", {})
    benchmark = store.create("benchmark", "file", "sample.mp4", {})
    claimed = store.claim("benchmark-worker", benchmark["id"])
    assert claimed["id"] == benchmark["id"]
    assert store.get(older["id"])["status"] == "queued"
    assert store.get(benchmark["id"])["status"] == "running"


def test_runtime_monitor_flags_a_gap_without_calling_it_cpu_work():
    from clipper.runtime import RuntimeMonitor
    with patch("clipper.runtime.time.perf_counter", side_effect=[1000, 1060]), patch(
        "clipper.runtime.unbiased_seconds", side_effect=[100, 110]
    ):
        result = RuntimeMonitor().snapshot()
    assert result["possible_suspend_seconds"] == 50
    assert result["standby_suspected"]
    with patch("clipper.runtime.unbiased_seconds", return_value=None):
        assert RuntimeMonitor().snapshot()["standby_suspected"] is None


def test_keep_awake_releases_previous_thread_state_on_failure():
    from clipper.runtime import inhibit_idle_sleep
    with patch("clipper.runtime.os.name", "nt"), patch(
        "clipper.runtime.set_execution_state", side_effect=[0x80000000, 0x80000001]
    ) as request:
        with pytest.raises(RuntimeError):
            with inhibit_idle_sleep(True) as state:
                assert state["enabled"]
                raise RuntimeError("cancelled work")
    assert [call.args[0] for call in request.call_args_list] == [0x80000001, 0x80000000]


def test_keep_awake_is_opt_in_and_failure_is_reported():
    from clipper.runtime import inhibit_idle_sleep
    with patch("clipper.runtime.set_execution_state", return_value=0) as request:
        with inhibit_idle_sleep(False) as state:
            assert not state["enabled"]
        request.assert_not_called()
        with patch("clipper.runtime.os.name", "nt"), inhibit_idle_sleep(True) as state:
            assert state["error"] and not state["enabled"]


def bundle_source(root):
    for relative in ("README.md", "pyproject.toml", "requirements.lock.txt", "backend/clipper/cli.py",
                     "apps/web/dist/index.html", "apps/web/src/App.tsx", ".data/runs/private/clip.mp4",
                     "backend/clipper/private-recording.mp4", "scripts/private-settings.json",
                     ".data/jobs.sqlite3", ".venv/private.txt", "apps/web/node_modules/private.txt",
                     ".data/models/small.en/model.bin", ".tools/node-v24.22.2-win-x64/node.exe"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)


@pytest.mark.parametrize("assets", [False, True])
def test_transfer_bundle_is_clean_verifiable_and_asset_options_are_explicit(tmp_path, assets):
    import zipfile
    from clipper.transfer import create_bundle, verify_bundle
    source = tmp_path / "source"
    bundle_source(source)
    archive = tmp_path / "test.zip"
    manifest = create_bundle(source, archive, include_tools=assets, include_models=assets)
    names = {item["path"] for item in manifest["files"]}
    assert not any("private" in name or "node_modules" in name or "jobs.sqlite3" in name for name in names)
    assert (".data/models/small.en/model.bin" in names) == assets
    assert (".tools/node-v24.22.2-win-x64/node.exe" in names) == assets
    target = tmp_path / "other machine"
    with zipfile.ZipFile(archive) as package:
        package.extractall(target)
    assert verify_bundle(target / "clipper")["verified"]
    (target / "clipper/README.md").write_text("altered")
    with pytest.raises(ValueError):
        verify_bundle(target / "clipper")
    with pytest.raises(FileExistsError):
        create_bundle(source, archive)
    assert archive.exists()


def test_transfer_verifier_rejects_traversal(tmp_path):
    from clipper.transfer import verify_bundle
    (tmp_path / "transfer-manifest.json").write_text(json.dumps({
        "format": 1, "application": "clipper-windows-test", "files": [{"path": "../private.txt"}],
        "tools_included": False, "models_included": False,
    }))
    with pytest.raises(ValueError, match="Unsafe"):
        verify_bundle(tmp_path)


def test_doctor_fails_when_media_prerequisites_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["clipper", "doctor"])
    with patch("clipper.cli.Settings.load", return_value=Settings(tmp_path)), patch(
        "clipper.preflight.inspect_system", return_value={"ready": False}
    ), pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1


def test_cli_benchmarks_can_repeat_after_previous_worker_exits(tmp_path, monkeypatch):
    import psutil
    settings = Settings(tmp_path / "data", reserve_bytes=0)
    settings.prepare()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"stubbed benchmark source")
    store = Store(settings.data / "jobs.sqlite3")
    output = tmp_path / "portable-report.json"
    monkeypatch.setattr(sys, "argv", ["clipper", "benchmark", str(source), "--output", str(output), "--keep-awake"])
    (settings.data / "worker.json").write_text(json.dumps({"updated": time.time(), "pid": 999999}))

    def finish(settings, once, job_id, prevent_sleep, job_timeout):
        assert once and prevent_sleep
        claimed = store.claim("test", job_id)
        store.finish(claimed["id"], "test", "succeeded", result={"benchmark": True, "exports": []})

    with patch("clipper.cli.Settings.load", return_value=settings), patch("clipper.worker.work", side_effect=finish), patch(
        "psutil.Process", side_effect=psutil.NoSuchProcess(999999)
    ):
        main()
        assert json.loads(output.read_text())["status"] == "succeeded"
        with pytest.raises(SystemExit):
            main()
        monkeypatch.setattr(sys, "argv", ["clipper", "benchmark", str(source), "--keep-awake"])
        main()
    assert len(store.list()) == 2


def test_failed_benchmark_keeps_diagnostics_and_partial_exports(tmp_path):
    from clipper.worker import work
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("failure", "file", "sample.mp4", RunOptions(mode="benchmark").model_dump())

    def fail(processor):
        processor.completed_exports = [{"id": "clip-01", "encoding": {"actual": "libx264"}}]
        processor.benchmark_environment = {"python": "3.12"}
        raise RuntimeError("requested deadline")

    with patch("clipper.worker.Processor.execute", fail):
        work(settings, once=True, job_id=job["id"], job_timeout=30)
    result = store.get(job["id"])
    assert result["status"] == "failed"
    assert result["result"]["incomplete"]
    assert result["result"]["exports"][0]["id"] == "clip-01"
    assert result["result"]["environment"]["python"] == "3.12"
    assert result["result"]["timing"]["standby_check"] in {"windows-unbiased-clock", "unavailable"}


def test_requested_deadline_is_checked_before_media_guard_throttling(tmp_path):
    from threading import Event
    from clipper.media import MediaError
    from clipper.worker import Processor
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("benchmark", "file", "sample.mp4", RunOptions(mode="benchmark").model_dump())
    claimed = store.claim("worker")
    processor = Processor(settings, store, claimed, "worker", Event())
    processor.job_timeout = 1
    with patch("clipper.worker.time.perf_counter", return_value=processor.runtime_monitor.started + 2), pytest.raises(MediaError, match="deadline"):
        processor.guard()


def test_source_only_export_excludes_internal_notes_and_generated_assets(tmp_path):
    from clipper.transfer import create_bundle
    source = tmp_path / "source"
    bundle_source(source)
    for relative in ("LICENSE", "NOTICE", "docs/architecture.md", "docs/project-context.md",
                     "docs/evaluation.md", "docs/gpu-acceleration.md", ".github/workflows/ci.yml"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    manifest = create_bundle(source, tmp_path / "public.zip", source_only=True)
    paths = {item["path"] for item in manifest["files"]}
    assert {"LICENSE", "NOTICE", "docs/architecture.md", ".github/workflows/ci.yml"} <= paths
    assert not paths.intersection({"docs/project-context.md", "docs/evaluation.md", "docs/gpu-acceleration.md"})
    assert not any(path.startswith((".data/", ".tools/", "apps/web/dist/")) for path in paths)
    with pytest.raises(ValueError, match="cannot include"):
        create_bundle(source, tmp_path / "unsafe.zip", source_only=True, include_models=True)


def test_public_diagnostic_export_omits_free_text_paths_and_source_evidence():
    from clipper.diagnostics import diagnostic_summary
    marker = "PRIVATE_TEST_VALUE"
    environment = {"platform": "Windows", "python": "3.12", "cpu": "test CPU", "logical_cpus": 4,
                   "workspace": marker, "models": {"revision": marker}, "backend_sha256": "a" * 64,
                   "packages": {"local-clipper": "0.1.0", "unknown-private-package": marker}}
    report = {"status": "failed", "requested_options": {"mode": "benchmark", "device": "auto", "max_clips": 5, "secret": marker},
              "source": {"duration": 60, "path": marker, "url": marker}, "warnings": [marker], "message": marker,
              "transcript": {"status": "usable", "words": [{"word": marker}]},
              "ranking": {"status": "fallback", "assessments": [{"evidence": marker}]},
              "stages": {"probe": 1, marker: 2}, "elapsed_seconds": float("nan"),
              "exports": [{"start": 1, "end": 20, "reason": marker, "encoding": {"actual": "libx264", "fallback_reason": marker}}]}
    result = diagnostic_summary(environment, report)
    assert marker not in json.dumps(result)
    assert "elapsed_seconds" not in result["metrics"]
    assert result["source"]["duration"] == 60
    assert result["ranking_status"] == "fallback"
    assert result["run_status"] == "failed"
    assert result["requested"]["max_clips"] == 5
    assert result["exports"][0]["encoder"] == "libx264"