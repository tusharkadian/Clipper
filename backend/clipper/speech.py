from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import configure_tools
from .preflight import capture, executable


os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"


def transcript_health(words: list[dict], segments: list[dict] | None = None) -> dict:
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    probabilities = [word["probability"] for word in words
                     if finite(word.get("probability")) and 0 <= word["probability"] <= 1]
    mean_probability = sum(probabilities) / len(probabilities) if probabilities else None
    low_fraction = sum(value < 0.35 for value in probabilities) / len(probabilities) if probabilities else None
    tokens = re.findall(r"[^\W_]+", " ".join(str(word.get("word", "")) for word in words).lower())
    trigrams = Counter(zip(tokens, tokens[1:], tokens[2:]))
    repeated_fraction = sum(count - 1 for count in trigrams.values()) / max(1, len(tokens) - 2)
    measured_segments, suspicious_segments = 0, 0
    for segment in segments or []:
        log_probability = segment.get("avg_logprob")
        compression = segment.get("compression_ratio")
        if not finite(log_probability) and not finite(compression):
            continue
        measured_segments += 1
        if (finite(log_probability) and log_probability < -1) or (finite(compression) and compression > 2.4):
            suspicious_segments += 1
    suspicious_fraction = suspicious_segments / measured_segments if measured_segments else None
    reasons = []
    enough_probabilities = len(probabilities) >= 5 and len(probabilities) >= len(words) / 2
    if enough_probabilities and (mean_probability < 0.45 or low_fraction >= 0.5):
        reasons.append("low_word_confidence")
    if len(tokens) >= 24 and repeated_fraction >= 0.6:
        reasons.append("repetitive_transcript")
    if suspicious_fraction is not None and suspicious_fraction >= 0.5:
        reasons.append("suspicious_segment_diagnostics")
    return {"version": 1, "status": "unreliable" if reasons else (
                "no_speech" if not tokens else "usable" if enough_probabilities or measured_segments else "unknown"),
            "word_count": len(words), "measured_words": len(probabilities),
            "mean_word_probability": round(mean_probability, 4) if mean_probability is not None else None,
            "low_probability_fraction": round(low_fraction, 4) if low_fraction is not None else None,
            "repeated_trigram_fraction": round(repeated_fraction, 4),
            "measured_segments": measured_segments, "suspicious_segment_fraction": suspicious_fraction,
            "reasons": reasons,
            "language": {"requested": "en", "detected": None, "verified": False, "model_english_only": True}}


def install(models: Path) -> None:
    from huggingface_hub import HfApi, snapshot_download
    repository = "Systran/faster-whisper-small.en"
    revision = HfApi(token=False).model_info(repository).sha
    temporary = models / "small.en.installing"
    temporary.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repository, revision=revision, local_dir=temporary, token=False,
        allow_patterns=["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    )
    if not (temporary / "model.bin").is_file() or not (temporary / "config.json").is_file():
        raise RuntimeError("The model download is incomplete.")
    (temporary / "manifest.json").write_text(json.dumps({"repository": repository, "revision": revision}), encoding="utf-8")
    target = models / "small.en"
    if target.exists():
        raise RuntimeError("Model already exists; remove it explicitly before replacing it.")
    shutil.move(str(temporary), str(target))


def transcribe(source: Path, destination: Path, models: Path, device: str) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    import numpy as np
    from faster_whisper import WhisperModel
    configure_tools()
    decoder, inspector = executable("ffmpeg"), executable("ffprobe")
    if not decoder or not inspector:
        raise RuntimeError("FFmpeg and FFprobe are required for bounded audio decoding.")
    model_path = models / "small.en"
    if not (model_path / "model.bin").is_file():
        raise RuntimeError("English model missing. Install it explicitly before enabling captions.")
    model = WhisperModel(
        str(model_path), device=device, compute_type="int8_float16" if device == "cuda" else "int8",
        cpu_threads=int(os.environ.get("CLIPPER_THREADS", "4")), num_workers=1, local_files_only=True,
    )
    words, diagnostics = [], []
    duration = float(capture([inspector, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)]))
    offset = 0.0
    while offset < duration:
        decoded = subprocess.run([
            decoder, "-v", "error", "-nostdin", "-threads", os.environ.get("CLIPPER_THREADS", "4"),
            "-ss", str(offset), "-i", str(source), "-t", "300", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-",
        ], check=True, capture_output=True, timeout=180)
        audio = np.frombuffer(decoded.stdout, dtype=np.float32)
        if not len(audio):
            break
        segments, _ = model.transcribe(audio, language="en", word_timestamps=True, vad_filter=True, beam_size=3)
        for segment in segments:
            first_word = len(words)
            for word in segment.words or []:
                start, end = offset + word.start, min(duration, offset + word.end)
                if end > start and (not words or end > words[-1]["end"] + 0.05):
                    words.append({"start": max(start, words[-1]["end"] if words else 0), "end": end, "word": word.word, "probability": word.probability})
            if len(words) > first_word:
                values = {name: getattr(segment, name, None) for name in ("avg_logprob", "no_speech_prob", "compression_ratio")}
                diagnostics.append({"start": words[first_word]["start"], "end": words[-1]["end"],
                                    **{name: float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
                                       for name, value in values.items()}})
        if offset + 300 >= duration:
            break
        offset += 298
    destination.write_text(json.dumps({"words": words, "device": device, "model": "small.en",
                                       "segments": diagnostics, "quality": transcript_health(words, diagnostics)}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    arguments = parser.parse_args()
    if arguments.install:
        install(arguments.models)
    elif arguments.source and arguments.output:
        transcribe(arguments.source, arguments.output, arguments.models, arguments.device)
    else:
        parser.error("Provide --install or --source and --output.")


if __name__ == "__main__":
    main()