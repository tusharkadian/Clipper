import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from clipper.speech import transcribe, transcript_health


def test_transcript_health_reports_confidence_without_claiming_language_detection():
    words = [{"word": word, "probability": 0.95} for word in "One second left and we won the match".split()]
    result = transcript_health(words, [{"avg_logprob": -0.2, "compression_ratio": 1.1, "no_speech_prob": 0.01}])
    assert result["status"] == "usable"
    assert result["mean_word_probability"] == 0.95
    assert result["reasons"] == []
    assert result["language"]["detected"] is None
    assert not result["language"]["verified"]


@pytest.mark.parametrize("probability", [0.1, 0.4])
def test_low_word_confidence_is_reported_without_changing_words(probability):
    words = [{"word": word, "probability": probability} for word in "These are the words recognized in this recording".split()]
    result = transcript_health(words)
    assert result["status"] == "unreliable"
    assert "low_word_confidence" in result["reasons"]
    assert all(word["probability"] == probability for word in words)


def test_repetition_and_suspicious_segments_are_flagged_independently():
    repeated = [{"word": "I go to the car", "probability": 0.99} for _ in range(12)]
    assert "repetitive_transcript" in transcript_health(repeated)["reasons"]
    ordinary = [{"word": "A short distinct sentence about a final shot."}]
    assert "suspicious_segment_diagnostics" in transcript_health(ordinary, [{"avg_logprob": -1.5}])["reasons"]
    assert "suspicious_segment_diagnostics" in transcript_health(ordinary, [{"compression_ratio": 3}])["reasons"]


def test_missing_or_invalid_diagnostics_are_unknown_not_bad_speech():
    words = [{"word": "A short sentence with no confidence metadata", "probability": float("nan")}]
    result = transcript_health(words, [{"avg_logprob": float("inf")}])
    assert result["status"] == "unknown"
    assert result["mean_word_probability"] is None
    assert transcript_health([])["status"] == "no_speech"
    assert transcript_health([{"word": "\u4f60\u597d"}])["status"] == "unknown"


def test_bounded_decoder_maps_overlapping_word_timestamps(tmp_path: Path):
    models = tmp_path / "models"
    (models / "small.en").mkdir(parents=True)
    (models / "small.en/model.bin").touch()
    output = tmp_path / "words.json"
    first = SimpleNamespace(start=298.2, end=299.2, word="overlap", probability=0.9)
    repeated = SimpleNamespace(start=0.2, end=1.2, word="overlap", probability=0.9)
    following = SimpleNamespace(start=2, end=2.8, word="next", probability=0.9)
    batches = [([SimpleNamespace(words=[first], avg_logprob=-0.2, compression_ratio=1.1, no_speech_prob=0.01)], None),
               ([SimpleNamespace(words=[repeated, following], avg_logprob=-0.3, compression_ratio=1.2, no_speech_prob=0.02)], None)]
    with patch("faster_whisper.WhisperModel") as model, patch("clipper.speech.executable", return_value="tool"), patch(
        "clipper.speech.capture", return_value="301"
    ), patch("clipper.speech.subprocess.run") as decoder:
        decoder.return_value.stdout = np.zeros(16000, dtype=np.float32).tobytes()
        model.return_value.transcribe.side_effect = batches
        transcribe(tmp_path / "source.wav", output, models, "cpu")
    result = json.loads(output.read_text())
    words = result["words"]
    assert len(words) == 2
    assert words[1]["start"] == 300
    assert words[1]["end"] <= 301
    assert decoder.call_count == 2
    assert len(result["segments"]) == 2
    assert result["segments"][1]["start"] == 300
    assert result["segments"][1]["avg_logprob"] == -0.3
    assert result["quality"]["measured_segments"] == 2