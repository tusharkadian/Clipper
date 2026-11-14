from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from jiwer import process_words
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from typing import Literal


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("An interval must have positive duration.")
        return self


class ClipReview(Interval):
    id: str = Field(min_length=1)
    usable: StrictBool | None = None
    boundary_ok: StrictBool | None = None
    repeated_event: StrictBool | None = None
    critical_caption_error: StrictBool | None = None


class SpeechReference(Interval):
    reference_text: str | None = None
    hypothesis_text: str


class RecordingReview(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: Literal["development", "holdout"] = "development"
    previously_used_for_tuning: bool = True
    game_or_format: str | None = None
    language: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    clips: list[ClipReview]
    desired_moments: list[Interval] | None = None
    speech_references: list[SpeechReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_clips(self):
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("Clip review IDs must be unique within a recording.")
        if self.split == "holdout" and self.previously_used_for_tuning:
            raise ValueError("Previously tuned recordings cannot be declared held out.")
        return self


def normalized_words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+(?:'[^\W_]+)?", text.lower()))


def evaluate_reviews(recordings: list[RecordingReview]) -> dict:
    if len({recording.id for recording in recordings}) != len(recordings):
        raise ValueError("Recording IDs must be unique.")
    splits = {}
    for recording in recordings:
        previous = splits.setdefault(recording.source_sha256, recording.split)
        if previous != recording.split:
            raise ValueError("The same source cannot occur in development and holdout splits.")
    if len(splits) != len(recordings):
        raise ValueError("The same source cannot be counted as multiple recordings.")
    per_recording = []
    for recording in recordings:
        human = bool(recording.reviewed_by and recording.reviewed_by.strip() and recording.reviewed_at)
        clips = recording.clips

        def metric(field):
            labels = [getattr(clip, field) for clip in clips if human and getattr(clip, field) is not None]
            return {"reviewed": len(labels), "rate": sum(labels) / len(labels) if labels else None}

        references, errors, hallucinated, measured = 0, 0, 0, 0
        for sample in recording.speech_references:
            if not human or sample.reference_text is None:
                continue
            measured += 1
            result = process_words(normalized_words(sample.reference_text), normalized_words(sample.hypothesis_text))
            count = result.hits + result.substitutions + result.deletions
            references += count
            errors += result.substitutions + result.deletions + result.insertions
            if not count:
                hallucinated += result.insertions
        desired = recording.desired_moments if human else None
        recalled = sum(any(max(0, min(moment.end, clip.end) - max(moment.start, clip.start))
                                  / (moment.end - moment.start) >= 0.5 for clip in clips) for moment in desired or [])
        per_recording.append({"recording_id": recording.id, "split": recording.split,
            "game_or_format": recording.game_or_format, "language": recording.language,
            "human_reviewed": human, "exported": len(clips),
            "usefulness": metric("usable"), "boundary_ok": metric("boundary_ok"),
            "repeated_event": metric("repeated_event"), "critical_caption_error": metric("critical_caption_error"),
            "desired_moments": len(desired) if desired is not None else None,
            "recalled_moments": recalled if desired is not None else None,
            "moment_recall": recalled / len(desired) if desired else None,
            "speech_samples_reviewed": measured, "reference_words": references, "word_errors": errors,
            "wer": errors / references if references else None, "silence_hallucinated_words": hallucinated})
    holdout = [value for value in per_recording if value["split"] == "holdout"]
    exported = sum(value["exported"] for value in holdout)
    useful_labels = sum(value["usefulness"]["reviewed"] for value in holdout)
    duplicate_labels = sum(value["repeated_event"]["reviewed"] for value in holdout)
    useful = sum((value["usefulness"]["rate"] or 0) * value["usefulness"]["reviewed"] for value in holdout)
    repeated = sum((value["repeated_event"]["rate"] or 0) * value["repeated_event"]["reviewed"] for value in holdout)
    reference_words = sum(value["reference_words"] for value in holdout)
    word_errors = sum(value["word_errors"] for value in holdout)
    reasons = []
    if len({recording.source_sha256 for recording in recordings}) < 10:
        reasons.append("fewer_than_10_unique_recordings")
    if len({value.game_or_format for value in recordings if value.game_or_format}) < 3:
        reasons.append("fewer_than_3_labeled_games_or_formats")
    if len({recording.source_sha256 for recording in recordings if recording.split == "holdout"}) < 3:
        reasons.append("fewer_than_3_untuned_holdout_recordings")
    if not exported or useful_labels != exported or duplicate_labels != exported:
        reasons.append("holdout_clip_reviews_incomplete")
    if not reference_words:
        reasons.append("no_human_holdout_speech_reference")
    if any(value["boundary_ok"]["reviewed"] != value["exported"] or
           value["critical_caption_error"]["reviewed"] != value["exported"] for value in holdout):
        reasons.append("holdout_boundary_or_critical_caption_reviews_incomplete")
    if any((value["critical_caption_error"]["rate"] or 0) > 0 for value in holdout):
        critical_errors = True
    else:
        critical_errors = False
    if any(value["desired_moments"] is None for value in holdout) or not any(value["desired_moments"] for value in holdout):
        reasons.append("holdout_recall_annotations_missing")
    if any(value["language"] != "en" for value in holdout):
        reasons.append("holdout_english_scope_unconfirmed")
    rates = {"useful_clip_rate": useful / useful_labels if useful_labels else None,
             "repeated_event_rate": repeated / duplicate_labels if duplicate_labels else None,
             "caption_wer": word_errors / reference_words if reference_words else None}
    thresholds_met = not reasons and not critical_errors and rates["useful_clip_rate"] >= 0.8 and rates["repeated_event_rate"] <= 0.1 and rates["caption_wer"] <= 0.1
    return {"schema_version": 1, "kind": "human_label_evaluation", "recordings": per_recording,
            "recording_count": len(recordings), "split_counts": dict(Counter(value.split for value in recordings)),
            "holdout": {**rates, "exported": exported, "reviewed_clips": useful_labels, "reference_words": reference_words},
            "gate": {"status": "insufficient_evidence" if reasons else "passed" if thresholds_met else "failed",
                     "reasons": [*reasons, *(["critical_caption_errors_remain"] if critical_errors else [])],
                     "proposed_targets": {"useful_clip_rate_min": 0.8, "wer_max": 0.1, "repeated_event_rate_max": 0.1}},
            "limitations": ["Human labels are required; confidence/model scores are not ground truth.",
                            "Recall uses at least 50% coverage of each human-labeled desired interval.",
                            "This result does not establish publishing rights, virality or performance capacity."]}


def initialize_review_workspace(data: Path, output: Path, reports: Path | None = None) -> dict:
    from .storage import Store
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Choose a new review-workspace directory; existing labels are never overwritten.")
    store = Store(data / "jobs.sqlite3")
    with store.connect() as database:
        jobs = [store.decode(row) for row in database.execute("SELECT * FROM jobs WHERE status='succeeded'")]
    external = []
    for path in sorted(reports.glob("*.json")) if reports else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            value = value["result"]
        if not isinstance(value, dict) or not isinstance(value.get("exports"), list):
            raise ValueError("The reports folder must contain only job-report JSON files.")
        external.append((path, value))
    output.mkdir(parents=True)
    items, used_sources, matched_reports = [], set(), set()
    for job in sorted(jobs, key=lambda item: item["created"]):
        result = job["result"]
        if result.get("benchmark") or not result.get("exports"):
            continue
        run = data / "runs" / job["id"]
        if job["kind"] == "file":
            source = Path(job["source"])
        elif (run / "download.json").is_file():
            source = Path(json.loads((run / "download.json").read_text(encoding="utf-8"))["path"])
        else:
            continue
        if not source.is_file():
            continue
        digest = hashlib.sha256()
        with source.open("rb") as content:
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(chunk)
        source_hash = digest.hexdigest()
        if source_hash in used_sources:
            continue
        used_sources.add(source_hash)
        identifier = f"recording_{len(items) + 1:02}"
        words_path = run / "words.json"
        payload = json.loads(words_path.read_text(encoding="utf-8")) if words_path.is_file() else {}
        words = payload.get("words", []) if isinstance(payload, dict) else payload
        clips, references = [], []
        clip_paths = {}
        for clip in result["exports"]:
            if not re.fullmatch(r"clip-[0-9]{2}", clip["id"]):
                raise ValueError("Invalid managed clip ID in imported report.")
            clips.append(ClipReview(id=clip["id"], start=clip["start"], end=clip["end"]))
            text = " ".join(str(word["word"]).strip() for word in words
                            if word["end"] > clip["start"] and word["start"] < clip["end"])
            references.append(SpeechReference(start=clip["start"], end=clip["end"], hypothesis_text=text))
            clip_paths[clip["id"]] = str((run / clip["id"] / "clip.mp4").resolve())
        review = RecordingReview(id=identifier, source_sha256=source_hash, clips=clips, speech_references=references)
        review_path = output / f"{identifier}.review.json"
        review_path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
        matching = []
        for path, value in external:
            if value.get("exports") == result.get("exports") and value.get("source") == result.get("source"):
                matching.append(str(path.resolve()))
                matched_reports.add(path)
        items.append({"id": identifier, "review": review_path.name, "source_sha256": source_hash,
                      "source": str(source.resolve()), "run": str(run.resolve()), "clips": clip_paths,
                      "report": str((run / "report.json").resolve()), "transcript": str(words_path.resolve()),
                      "feature_receipt": str((run / "analyze.receipt.json").resolve()),
                      "requested_options": job["options"], "reported_ranking": result.get("ranking", {}),
                      "expires_at": job["expires_at"], "matching_external_reports": matching})
    manifest = {"schema_version": 1, "private": True, "label_status": "awaiting_human_review",
                "data_root": str(data.resolve()),
                "recordings": items, "unmatched_external_reports": [str(path.resolve()) for path, _ in external if path not in matched_reports],
                "warnings": ["All imported recordings are development examples, not untuned holdout data.",
                             "Review files contain private transcript hypotheses; never publish this workspace.",
                             "Media is referenced, not copied; managed sources/clips still expire under seven-day retention."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"workspace": str(output), "data_root": str(data.resolve()), "recordings": len(items), "clips": sum(len(item["clips"]) for item in items),
            "unmatched_reports": len(manifest["unmatched_external_reports"]), "label_status": manifest["label_status"]}


def evaluate_workspace(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported evaluation manifest.")
    root = manifest_path.parent.resolve()
    recordings = []
    for entry in manifest["recordings"]:
        path = (root / entry["review"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Review file must remain inside its workspace.")
        review = RecordingReview.model_validate_json(path.read_text(encoding="utf-8"))
        if review.id != entry["id"] or review.source_sha256 != entry["source_sha256"]:
            raise ValueError("Review identity differs from the imported source manifest.")
        recordings.append(review)
    return evaluate_reviews(recordings)


def replay_activity_baseline(manifest_path: Path) -> dict:
    from .analysis import rank_candidates
    from .domain import RunOptions
    from .speech import transcript_health
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    for entry in manifest["recordings"]:
        receipt = json.loads(Path(entry["feature_receipt"]).read_text(encoding="utf-8"))
        payload = json.loads(Path(entry["transcript"]).read_text(encoding="utf-8"))
        report = json.loads(Path(entry["report"]).read_text(encoding="utf-8"))
        options = RunOptions.model_validate(entry["requested_options"])
        words, segments = payload.get("words", []), payload.get("segments", [])
        boundary_words = [] if transcript_health(words, segments)["status"] == "unreliable" else words
        features = receipt["value"]
        selected = rank_candidates(features, report["source"]["duration"], options, boundary_words, segments=segments)
        reported = report.get("exports", [])
        matched = sum(any(abs(candidate.start - clip["start"]) < 0.1 and abs(candidate.end - clip["end"]) < 0.1
                          for clip in reported) for candidate in selected)
        results.append({"recording_id": entry["id"], "activity_candidates": [candidate.model_dump() for candidate in selected],
                        "reported_export_count": len(reported), "matching_intervals": matched,
                        "reported_ranking_status": report.get("ranking", {}).get("status", "unknown"),
                        "reported_method": report.get("ranker", "unknown"),
                        "reported_ai_seconds": report.get("stages", {}).get("rerank"),
                        "interpretation": "Selection agreement and runtime only; usefulness/recall require human labels."})
    return {"schema_version": 1, "private": True, "kind": "cached_activity_replay",
            "recordings": results, "inference_performed": False,
            "limitations": ["No source decoding, transcription or AI inference was rerun.",
                            "Saved exports may come from a previous selection version.",
                            "Agreement with the app's outputs is not accuracy ground truth."]}