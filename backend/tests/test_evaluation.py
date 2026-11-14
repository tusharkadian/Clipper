import pytest
from pydantic import ValidationError

from clipper.evaluation import RecordingReview, evaluate_reviews


def recording(**changes):
    return RecordingReview.model_validate({"id": "recording_1", "source_sha256": "a" * 64,
        "clips": [{"id": "clip-01", "start": 0, "end": 20}], **changes})


def test_unreviewed_generated_exports_do_not_become_quality_success():
    result = evaluate_reviews([recording()])
    assert result["gate"]["status"] == "insufficient_evidence"
    assert result["recordings"][0]["usefulness"]["rate"] is None
    assert result["recordings"][0]["wer"] is None


def test_wer_uses_human_reference_and_reports_missed_moment():
    value = recording(reviewed_by="tester", reviewed_at="2026-11-14", clips=[
        {"id": "clip-01", "start": 0, "end": 20, "usable": True, "boundary_ok": True, "repeated_event": False}],
        desired_moments=[{"start": 1, "end": 4}, {"start": 50, "end": 55}],
        speech_references=[{"start": 0, "end": 5, "reference_text": "we did not win", "hypothesis_text": "we did win"}])
    result = evaluate_reviews([value])["recordings"][0]
    assert result["wer"] == 0.25
    assert result["moment_recall"] == 0.5
    assert result["usefulness"]["rate"] == 1


def test_holdout_tuning_leakage_and_source_overlap_are_rejected():
    with pytest.raises(ValidationError, match="held out"):
        recording(split="holdout")
    with pytest.raises(ValueError, match="same source"):
        evaluate_reviews([recording(), recording(id="second", split="holdout", previously_used_for_tuning=False)])


def test_empty_speech_reference_tracks_hallucinations_without_false_zero_wer():
    value = recording(reviewed_by="tester", reviewed_at="2026-11-14", speech_references=[
        {"start": 0, "end": 5, "reference_text": "", "hypothesis_text": "invented words"}])
    result = evaluate_reviews([value])["recordings"][0]
    assert result["wer"] is None
    assert result["silence_hallucinated_words"] == 2


@pytest.mark.parametrize("start,end", [(10, 5), (0, float("nan")), (-1, 5)])
def test_invalid_annotation_intervals_are_rejected(start, end):
    with pytest.raises(ValidationError):
        recording(clips=[{"id": "clip", "start": start, "end": end}])


def test_review_workspace_imports_private_references_without_inventing_labels(tmp_path):
    import json
    from clipper.config import Settings
    from clipper.evaluation import evaluate_workspace, initialize_review_workspace
    from clipper.storage import Store
    settings = Settings(tmp_path / "data", reserve_bytes=0)
    settings.prepare()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"authorized fixture")
    store = Store(settings.data / "jobs.sqlite3")
    job = store.create("fixture", "file", str(source), {})
    store.claim("worker")
    result = {"exports": [{"id": "clip-01", "start": 0, "end": 20}], "source": {"duration": 60}}
    store.finish(job["id"], "worker", "succeeded", result=result)
    run = settings.data / "runs" / job["id"]
    run.mkdir(parents=True)
    (run / "words.json").write_text(json.dumps({"words": [{"start": 1, "end": 2, "word": "hypothesis"}]}))
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report.json").write_text(json.dumps(result))
    output = tmp_path / "private-review"
    imported = initialize_review_workspace(settings.data, output, reports)
    assert imported["recordings"] == imported["clips"] == 1
    assert imported["unmatched_reports"] == 0
    review = json.loads((output / "recording_01.review.json").read_text())
    assert review["clips"][0]["usable"] is None
    assert review["speech_references"][0]["reference_text"] is None
    assert review["speech_references"][0]["hypothesis_text"] == "hypothesis"
    assert evaluate_workspace(output / "manifest.json")["gate"]["status"] == "insufficient_evidence"
    assert source.read_bytes() == b"authorized fixture"
    with pytest.raises(FileExistsError):
        initialize_review_workspace(settings.data, output, reports)


def test_same_source_cannot_inflate_recording_count():
    with pytest.raises(ValueError, match="multiple recordings"):
        evaluate_reviews([recording(), recording(id="another_alias")])


def test_gate_requires_complete_untuned_human_holdout_evidence():
    values = []
    for index in range(10):
        values.append(recording(id=f"sample_{index}", source_sha256=f"{index:064x}",
            split="holdout" if index < 3 else "development", previously_used_for_tuning=False,
            game_or_format=f"game_{index % 3}", language="en", reviewed_by="human", reviewed_at="2026-11-14",
            clips=[{"id": "clip", "start": 0, "end": 20, "usable": True, "boundary_ok": True,
                    "repeated_event": False, "critical_caption_error": False}], desired_moments=[{"start": 2, "end": 5}],
            speech_references=[{"start": 1, "end": 5, "reference_text": "we won the round", "hypothesis_text": "we won the round"}]))
    assert evaluate_reviews(values)["gate"]["status"] == "passed"
    values[0].clips[0].critical_caption_error = True
    assert evaluate_reviews(values)["gate"]["status"] == "failed"


def test_empty_data_override_uses_project_managed_data(monkeypatch):
    from clipper.config import ROOT, Settings
    monkeypatch.setenv("CLIPPER_DATA", "")
    assert Settings.load().data == (ROOT / ".data").resolve()