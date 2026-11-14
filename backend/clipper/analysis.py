from __future__ import annotations

from difflib import SequenceMatcher
import math
import re
import statistics

from .domain import Candidate, RunOptions
from .speech import transcript_health

SCENE_SNAP_SECONDS = 3
MAX_SHARED_SECONDS = 0.25
DIALOGUE_MIN_TOKENS = 20
DIALOGUE_SIMILARITY = 0.9


def scene_boundaries(start: float, end: float, moment: float, duration: float,
                     cuts: list[float]) -> tuple[float, float]:
    valid = [cut for cut in cuts if isinstance(cut, (int, float)) and math.isfinite(cut) and 0 < cut < duration]
    starts = [cut for cut in valid if abs(cut - start) <= SCENE_SNAP_SECONDS and cut <= moment]
    endings = [cut for cut in valid if abs(cut - end) <= SCENE_SNAP_SECONDS and cut >= moment]
    left = min(starts, key=lambda cut: (abs(cut - start), cut)) if starts else start
    right = min(endings, key=lambda cut: (abs(cut - end), -cut)) if endings else end
    return (left, right) if 15 <= right - left <= 90 else (start, end)


def distinct_candidates(candidates: list[Candidate], limit: int, words: list[dict] | None = None,
                        segments: list[dict] | None = None) -> list[Candidate]:
    selected, transcripts = [], []
    for candidate in sorted(candidates, key=lambda value: (-value.score, value.start)):
        if any(min(candidate.end, previous.end) - max(candidate.start, previous.start) > MAX_SHARED_SECONDS for previous in selected):
            continue
        overlapping = [word for word in words or [] if word["end"] > candidate.start and word["start"] < candidate.end]
        quality = transcript_health(overlapping, [segment for segment in segments or []
                                    if segment["end"] > candidate.start and segment["start"] < candidate.end])
        tokens = re.findall(r"[^\W_]+", " ".join(str(word["word"]) for word in overlapping).lower())
        comparable = tokens if len(tokens) >= DIALOGUE_MIN_TOKENS and quality["status"] == "usable" else []
        if comparable and any(previous and SequenceMatcher(None, previous, comparable, autojunk=False).ratio() >= DIALOGUE_SIMILARITY
                              for previous in transcripts):
            continue
        selected.append(candidate)
        transcripts.append(comparable)
        if len(selected) >= limit:
            break
    return selected


def speech_boundaries(start: float, end: float, moment: float, duration: float,
                      words: list[dict]) -> tuple[float, float] | None:
    ordered = sorted((word for word in words if word["end"] > word["start"]), key=lambda word: word["start"])
    if not ordered:
        return start, end
    starts = [max(0, ordered[0]["start"] - 0.12)]
    endings = []
    for index, word in enumerate(ordered):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        punctuation = str(word["word"]).rstrip().rstrip('\"\')]}').endswith((".", "?", "!"))
        pause = (following["start"] if following else duration) - word["end"] >= 0.65
        if punctuation or pause or following is None:
            endings.append(min(duration, word["end"] + 0.18,
                               following["start"] if following else duration))
            if following:
                starts.append(max(word["end"], following["start"] - 0.12))
    for boundary in ("start", "end"):
        point = start if boundary == "start" else end
        crossing = any(word["start"] - 0.12 < point < word["end"] + 0.18 for word in ordered)
        between_words = any(previous["end"] <= point <= following["start"]
                            and following["start"] - previous["end"] < 0.65
                            and not str(previous["word"]).rstrip().rstrip('\"\')]}').endswith((".", "?", "!"))
                            for previous, following in zip(ordered, ordered[1:]))
        if not crossing and not between_words:
            continue
        if boundary == "start" and start > 0:
            before = [value for value in starts if max(0, start - 12) <= value <= start]
            after = [value for value in starts if start < value <= min(moment, start + 12)]
            if before:
                start = max(before)
            elif after:
                start = min(after)
            else:
                return None
        elif boundary == "end" and end < duration:
            after = [value for value in endings if end <= value <= min(duration, end + 15, start + 90)]
            before = [value for value in endings if max(start + 15, moment + 0.25, end - 15) <= value <= end]
            if after:
                end = min(after)
            elif before:
                end = max(before)
            else:
                return None
    if end - start < 15 or end - start > 90 or not start <= moment <= end:
        return None
    return start, end


def rank_candidates(features: dict, duration: float, options: RunOptions, words: list[dict] | None = None,
                    shortlist_limit: int | None = None, segments: list[dict] | None = None) -> list[Candidate]:
    if duration < 15:
        return []
    audio = features.get("audio", [])
    visual = features.get("visual", [])
    median_audio = statistics.median([item["value"] for item in audio]) if audio else -100
    median_visual = statistics.median([item["value"] for item in visual]) if visual else 0
    bins: dict[int, list[float]] = {}
    for item in audio:
        score = min(1.0, max(0.0, (item["value"] - median_audio) / 12)) if item["value"] > -45 else 0
        bins.setdefault(int(item["time"]), [0, 0])[0] = score
    for item in visual:
        score = min(1.0, max(0.0, (item["value"] - median_visual) / max(12, median_visual)))
        bins.setdefault(int(item["time"]), [0, 0])[1] = score
    proposals = []
    for moment, (audio_score, visual_score) in bins.items():
        score = 0.65 * max(audio_score, visual_score) + 0.35 * min(audio_score, visual_score)
        if score < options.threshold:
            continue
        length = min(options.clip_seconds, duration)
        start = max(0, min(moment - length * 0.65, duration - length))
        end = min(duration, start + length)
        start, end = scene_boundaries(start, end, moment, duration, features.get("scenes", []))
        if words:
            overlapping = [word for word in words if word["end"] > start and word["start"] < end]
            quality = transcript_health(overlapping, [segment for segment in segments or []
                                        if segment["end"] > start and segment["start"] < end])
            if quality["status"] != "unreliable":
                adjusted = speech_boundaries(start, end, moment, duration, words)
                if adjusted is None:
                    continue
                start, end = adjusted
        reason = "Audio and visual activity change" if min(audio_score, visual_score) > 0.25 else (
            "Visual activity change" if visual_score > audio_score else "Audio activity change"
        )
        proposals.append(Candidate(start=start, end=end, score=round(score, 3), reason=reason,
                                    scene_adjusted=any(abs(edge - cut) < 0.001 for edge in (start, end)
                                                       for cut in features.get("scenes", [])),
                                    audio_score=audio_score, visual_score=visual_score))
    limit = shortlist_limit if shortlist_limit is not None else options.max_clips
    return distinct_candidates(proposals, limit, words, segments)