from pathlib import Path
from unittest.mock import patch

from clipper.config import Settings
from clipper.domain import RunOptions
from clipper.storage import Store
from clipper.worker import publish_heartbeat, work, write_json

import pytest

from clipper.config import configure_tools
from clipper.media import ffmpeg
from clipper.preflight import executable


def test_json_replace_retries_transient_windows_lock(tmp_path: Path):
    import json
    original_replace = Path.replace
    attempts = []

    def replace_once_locked(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            raise PermissionError(5, "Access denied")
        return original_replace(source, destination)

    with patch.object(Path, "replace", replace_once_locked):
        write_json(tmp_path / "worker.json", {"owner": "test"})
    assert json.loads((tmp_path / "worker.json").read_text()) == {"owner": "test"}
    assert len(attempts) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_heartbeat_lock_does_not_fail_processing_or_destroy_previous_value(tmp_path: Path):
    target = tmp_path / "worker.json"
    target.write_text('{"owner":"previous"}')
    with patch.object(Path, "replace", side_effect=PermissionError(5, "Access denied")), patch("clipper.worker.Event.wait"):
        assert not publish_heartbeat(tmp_path, "next")
    assert target.read_text() == '{"owner":"previous"}'
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_json_writers_use_independent_temporary_files(tmp_path: Path):
    import json
    from concurrent.futures import ThreadPoolExecutor
    target = tmp_path / "worker.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: write_json(target, {"number": number}), range(12)))
    assert json.loads(target.read_text())["number"] in range(12)
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_source_fails_without_losing_job(tmp_path: Path):
    settings = Settings(tmp_path, reserve_bytes=0)
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("missing", "file", str(tmp_path / "absent.mp4"), RunOptions().model_dump())
    work(settings, once=True)
    result = store.get(job["id"])
    assert result["status"] == "failed"
    assert "missing" in result["message"]


def test_job_can_be_retried_after_failed_stage(tmp_path: Path):
    settings = Settings(tmp_path, reserve_bytes=0)
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("video", "file", "test.mp4", RunOptions().model_dump())
    with patch("clipper.worker.Processor.execute", side_effect=RuntimeError("stage failed")):
        work(settings, once=True)
    store.control(job["id"], "retry")
    with patch("clipper.worker.Processor.execute", return_value={"exports": [], "cloud_calls": 0}):
        work(settings, once=True)
    assert store.get(job["id"])["status"] == "succeeded"
    assert store.get(job["id"])["result"]["cloud_calls"] == 0


def test_real_queued_source_produces_playable_exports(tmp_path: Path):
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    settings = Settings(tmp_path / "data", reserve_bytes=0)
    settings.prepare()
    source = tmp_path / "synthetic-activity.mp4"
    ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
        "-af", "volume=if(between(t\\,12\\,14)\\,1\\,0.01):eval=frame",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ], tmp_path)
    store = Store(settings.data / "jobs.sqlite3")
    options = RunOptions(captions=False, clip_seconds=20).model_dump()
    job = store.create("Synthetic integration fixture", "file", str(source), options)
    work(settings, once=True)
    result = store.get(job["id"])
    assert result["status"] == "succeeded", result["message"]
    assert result["result"]["cloud_calls"] == 0
    exports = result["result"]["exports"]
    assert exports
    folder = settings.data / "runs" / job["id"] / exports[0]["id"]
    assert (folder / "clip.mp4").is_file()
    assert (folder / "plan.json").is_file()
    assert (folder / "thumbnail.jpg").is_file()
    assert (folder / "encoding.json").is_file()
    assert result["result"]["encoding"]["requested"] == "auto"
    assert result["result"]["exports"][0]["encoding"]["actual"] in {"libx264", "h264_nvenc"}
    assert result["result"]["speech_device"] == "not-run"
    assert result["result"]["transcript"]["status"] == "not_run"
    assert result["result"]["selection"]["version"] == 2
    assert result["result"]["selection"]["maximum_shared_seconds"] == 0.25
    assert result["result"]["stages"]["select"] >= 0


def test_encoder_capability_is_probed_once_per_job_and_reused(tmp_path: Path):
    import json
    from threading import Event
    from clipper.worker import Processor
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("source", "file", "source.mp4", RunOptions(encoder="auto").model_dump())
    store.claim("worker")
    processor = Processor(settings, store, job, "worker", Event())
    decision = {"requested": "auto", "actual": "libx264", "fallback_reason": "GPU unavailable", "probe_seconds": 0.2}

    def render_fixture(source, start, end, folder, *args, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "encoding.json").write_text(json.dumps(kwargs["encoder_decision"]))

    with patch("clipper.worker.choose_encoder", return_value=decision) as choose, patch("clipper.worker.render", side_effect=render_fixture):
        first = processor.render_clip(Path("source.mp4"), 0, 20, tmp_path / "clip-01", [], False)
        second = processor.render_clip(Path("source.mp4"), 20, 40, tmp_path / "clip-02", [], False)
    assert choose.call_count == 1
    assert first["actual"] == second["actual"] == "libx264"
    assert processor.warnings == ["GPU unavailable"]


def test_analysis_version_invalidates_only_old_analysis_and_reports_cache(tmp_path: Path):
    import json
    from threading import Event
    from clipper.worker import Processor
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("source", "file", "source.mp4", RunOptions().model_dump())
    store.claim("worker")
    processor = Processor(settings, store, job, "worker", Event())
    processor.cache_key = "source-and-options"
    old = {"key": processor.cache_key, "value": {"audio": [], "visual": []}}
    (processor.directory / "analyze.receipt.json").write_text(json.dumps(old))
    features = {"audio": [], "visual": [], "analysis": {
        "version": 2, "requested": "auto", "video_decode": "nvdec", "video_scale": "cuda",
        "fallback_reason": None, "timings": {"audio_seconds": 1, "visual_seconds": 2},
    }}
    with patch("clipper.worker.time.monotonic", return_value=10), patch("clipper.worker.extract_features", return_value=features) as extract:
        processor.analyze(Path("source.mp4"), {"duration": 20}, 0.5)
        assert not processor.analysis_report["cached"]
        assert processor.timings["analyze"] == 0
        assert not processor.stage_cache_hits["analyze"]
        assert processor.timings["analysis-visual"] == 2
        processor.analyze(Path("source.mp4"), {"duration": 20}, 0.5)
    assert extract.call_count == 1
    assert processor.analysis_report["cached"]
    assert processor.stage_cache_hits["analyze"]
    assert processor.timings["analysis-visual"] == 0
    assert processor.analysis_report["timings"]["visual_seconds"] == 2


def test_worker_passes_explicit_webcam_settings_to_renderer(tmp_path):
    import json
    from threading import Event
    from clipper.worker import Processor
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    layout = {"webcam": {"x": 0.8, "y": 0, "width": 0.2, "height": 0.25},
              "gameplay": {"x": 0, "y": 0, "width": 0.8, "height": 1}, "top_fraction": 0.3,
              "webcam_zoom": 1.75, "gameplay_fit": "fit"}
    job = store.create("source", "file", "source.mp4", RunOptions(zoom=1.25, webcam_layout=layout).model_dump())
    store.claim("worker")
    processor = Processor(settings, store, job, "worker", Event())

    def rendered(source, start, end, folder, *args, **kwargs):
        assert kwargs["webcam_layout"].model_dump() == layout
        assert kwargs["zoom"] == 1.25
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "encoding.json").write_text(json.dumps({"actual": "libx264"}))

    with patch("clipper.worker.choose_encoder", return_value={"actual": "libx264", "probe_seconds": 0}), patch(
        "clipper.worker.render", side_effect=rendered
    ):
        assert processor.render_clip(Path("source.mp4"), 0, 20, tmp_path / "clip", [], False)["actual"] == "libx264"


def test_analysis_fallback_reason_is_visible_in_worker_warnings(tmp_path: Path):
    from threading import Event
    from clipper.worker import Processor
    settings = Settings(tmp_path, reserve_bytes=0)
    settings.prepare()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create("source", "file", "source.mp4", RunOptions().model_dump())
    store.claim("worker")
    processor = Processor(settings, store, job, "worker", Event())
    features = {"audio": [], "visual": [], "analysis": {
        "version": 2, "requested": "auto", "video_decode": "cpu", "video_scale": "cpu",
        "fallback_reason": "GPU unavailable; CPU analysis used", "timings": {"audio_seconds": 0, "visual_seconds": 2},
    }}
    with patch("clipper.worker.extract_features", return_value=features):
        processor.analyze(Path("source.mp4"), {"duration": 20}, 0.5)
    assert processor.warnings == ["GPU unavailable; CPU analysis used"]


@pytest.mark.parametrize("mode", ["clips", "benchmark"])
def test_transcript_health_is_in_reports_and_warnings_without_blocking_exports(tmp_path: Path, mode):
    import json
    from threading import Event
    from clipper.domain import Candidate
    from clipper.worker import Processor
    settings = Settings(tmp_path / "data", reserve_bytes=0)
    settings.prepare()
    model = settings.data / "models/small.en/model.bin"
    model.parent.mkdir(parents=True)
    model.touch()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"stubbed media")
    store = Store(settings.data / "jobs.sqlite3")
    job = store.create("source", "file", str(source), RunOptions(mode=mode).model_dump())
    store.claim("worker")
    processor = Processor(settings, store, job, "worker", Event())
    words = [{"start": index + 1, "end": index + 1.8, "word": word, "probability": 0.1}
             for index, word in enumerate("These words have low recognition confidence in this source".split())]
    transcript = {"words": words, "device": "cpu", "segments": [{"start": 1, "end": 10, "avg_logprob": -2}]}
    info = {"duration": 20, "has_audio": True}

    def render_fixture(source, start, end, folder, *args):
        folder.mkdir(parents=True, exist_ok=True)
        metadata = {"actual": "libx264"}
        (folder / "encoding.json").write_text(json.dumps(metadata))
        return metadata

    with patch("clipper.worker.ffmpeg", return_value=None), patch("clipper.worker.probe", return_value=info), patch.object(
        processor, "speech", return_value=transcript
    ), patch.object(processor, "analyze", return_value={}), patch.object(
        processor, "select_candidates", return_value=([Candidate(start=0, end=20, score=0.8, reason="activity")], {"method": "activity-v1"})
    ), patch.object(processor, "render_clip", side_effect=render_fixture):
        result = processor.execute()
    assert result["transcript"]["status"] == "unreliable"
    assert result["transcript"]["measured_segments"] == 1
    assert "low_word_confidence" in result["transcript"]["reasons"]
    assert any("Transcript quality" in warning for warning in result["warnings"])
    assert not result["transcript"]["language"]["verified"]
    assert result["cloud_calls"] == 0
    assert len(result["exports"]) == (2 if mode == "benchmark" else 1)
    assert json.loads((processor.directory / "report.json").read_text())["transcript"] == result["transcript"]