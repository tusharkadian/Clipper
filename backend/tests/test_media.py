from pathlib import Path

import pytest

from clipper.analysis import distinct_candidates, rank_candidates, scene_boundaries, speech_boundaries
from clipper.config import configure_tools
from clipper.domain import Candidate, RunOptions
from clipper.media import MediaError, choose_encoder, encoder_arguments, extract_features, ffmpeg, probe, render, terminate, write_ass_captions, write_captions
from clipper.preflight import executable


def test_analysis_reports_separate_audio_visual_timings_and_stream_maps(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.ffmpeg") as command, patch("clipper.media.time.perf_counter", side_effect=[1, 2, 3, 7]):
        result = extract_features(Path("source.mp4"), {"has_audio": True, "duration": 20}, tmp_path, lambda: None)
    assert result["analysis"]["timings"] == {"audio_seconds": 1, "visual_seconds": 4}
    assert "0:a:0" in command.call_args_list[0].args[0]
    assert "0:v:0" in command.call_args_list[1].args[0]
    assert command.call_args_list[0].kwargs["log_name"] == "analysis-audio.log"
    assert command.call_args_list[1].kwargs["log_name"] == "analysis-visual-cpu.log"


def test_analysis_without_audio_records_zero_audio_time(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.ffmpeg") as command:
        result = extract_features(Path("silent.mp4"), {"has_audio": False, "duration": 20}, tmp_path, lambda: None)
    assert command.call_count == 1
    assert result["analysis"]["timings"]["audio_seconds"] == 0


def test_nvdec_analysis_scales_before_downloading_and_has_explicit_decoder(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.ffmpeg") as command, patch("clipper.media.validated_visual_metadata", return_value=[]):
        result = extract_features(Path("source.mkv"), {"has_audio": False, "duration": 20, "codec": "av1"},
                                  tmp_path, lambda: None, decoder="nvdec")
    arguments = command.call_args.args[0]
    assert "av1_cuvid" in arguments
    graph = arguments[arguments.index("-vf") + 1]
    assert graph.index("fps=1") < graph.index("scale_cuda")
    assert graph.index("scale_cuda") < graph.index("hwdownload") < graph.index("signalstats")
    assert result["analysis"]["video_decode"] == "nvdec"
    assert result["analysis"]["video_scale"] == "cuda"


@pytest.mark.parametrize("failure", ["process", "coverage"])
def test_auto_analysis_retries_failed_nvdec_and_removes_partial_metadata(tmp_path, failure):
    from unittest.mock import patch
    calls = []

    def process(arguments, directory, guard, **kwargs):
        calls.append(kwargs["log_name"])
        if "av1_cuvid" in arguments:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "visual.txt").write_text("partial GPU data")
            (directory / "scenes.txt").write_text("partial GPU scene data")
            if failure == "process":
                raise MediaError("decoder unavailable")
            return ""
        assert not (directory / "visual.txt").exists()
        assert not (directory / "scenes.txt").exists()

    with patch("clipper.media.ffmpeg", side_effect=process):
        result = extract_features(Path("source.mkv"), {"has_audio": False, "duration": 20, "codec": "av1"},
                                  tmp_path, lambda: None, decoder="auto")
    assert calls == ["analysis-visual-nvdec.log", "analysis-visual-cpu.log"]
    assert result["analysis"]["video_decode"] == "cpu"
    assert "CPU analysis used" in result["analysis"]["fallback_reason"]


def test_forced_nvdec_does_not_fall_back_or_swallow_cancellation(tmp_path):
    from unittest.mock import patch
    from clipper.worker import Interrupted
    info = {"has_audio": False, "duration": 20, "codec": "av1"}
    with patch("clipper.media.ffmpeg", side_effect=MediaError("unsupported")), pytest.raises(MediaError):
        extract_features(Path("source.mkv"), info, tmp_path, lambda: None, decoder="nvdec")
    with patch("clipper.media.ffmpeg", side_effect=Interrupted("cancel")), pytest.raises(Interrupted):
        extract_features(Path("source.mkv"), info, tmp_path, lambda: None, decoder="auto")


def test_invalid_hardware_feature_coverage_is_rejected(tmp_path):
    from clipper.media import validated_visual_metadata
    path = tmp_path / "visual.txt"
    path.write_text("frame:0 pts:0 pts_time:0\nlavfi.signalstats.YDIF=1\n")
    with pytest.raises(MediaError, match="coverage"):
        validated_visual_metadata(path, 20)
    path.write_text("frame:0 pts:0 pts_time:0\nlavfi.signalstats.YDIF=1\nframe:1 pts:2 pts_time:2\nlavfi.signalstats.YDIF=1\n")
    with pytest.raises(MediaError, match="timeline"):
        validated_visual_metadata(path, 2)


def test_real_scene_detection_reuses_sampled_analysis_and_maps_cut_times(tmp_path):
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "hard-cuts.mp4"
    ffmpeg(["-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=4",
            "-f", "lavfi", "-i", "color=c=white:size=320x180:rate=15:duration=4",
            "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=15:duration=4",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[out]", "-map", "[out]",
            "-c:v", "libx264", str(source)], tmp_path)
    features = extract_features(source, probe(source, tmp_path), tmp_path, lambda: None)
    assert features["scenes"] == pytest.approx([4, 8], abs=1)
    assert features["analysis"]["scene_detection"]["cuts"] == 2
    assert features["analysis"]["scene_detection"]["sample_fps"] == 1
    assert len(features["visual"]) == 12


def test_stale_features_cannot_be_reused_when_attempt_produces_no_metadata(tmp_path):
    from unittest.mock import patch
    stale = "frame:0 pts:0 pts_time:0\nlavfi.signalstats.YDIF=1\n"
    (tmp_path / "visual.txt").write_text(stale)
    (tmp_path / "audio.txt").write_text("frame:0 pts:0 pts_time:0\nlavfi.astats.Overall.RMS_level=-10\n")
    with patch("clipper.media.ffmpeg"), pytest.raises(MediaError, match="coverage"):
        extract_features(Path("source.mkv"), {"has_audio": False, "duration": 2, "codec": "av1"},
                         tmp_path, lambda: None, decoder="nvdec")
    assert not (tmp_path / "visual.txt").exists()
    assert not (tmp_path / "audio.txt").exists()


def test_auto_encoder_requires_successful_execution_not_listing(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.executable", return_value="ffmpeg"), patch("clipper.media.run_command") as command:
        decision = choose_encoder("auto", tmp_path)
    assert decision["actual"] == "h264_nvenc"
    assert "1080x1920" in " ".join(command.call_args.args[0])
    assert command.call_args.kwargs["timeout"] == 20


def test_auto_encoder_falls_back_and_forced_nvenc_fails_explicitly(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.executable", return_value="ffmpeg"), patch("clipper.media.run_command", side_effect=MediaError("unsupported GPU")):
        decision = choose_encoder("auto", tmp_path)
        assert decision["actual"] == "libx264"
        assert "probe failed" in decision["fallback_reason"]
        with pytest.raises(MediaError, match="Forced NVENC"):
            choose_encoder("nvenc", tmp_path)


def test_cpu_encoder_does_not_probe_gpu_and_uses_validated_arguments(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.run_command") as command:
        assert choose_encoder("cpu", tmp_path)["actual"] == "libx264"
    command.assert_not_called()
    assert "-cq" in encoder_arguments("h264_nvenc")
    with pytest.raises(ValueError):
        encoder_arguments("user-input-command")


def test_encoder_probe_does_not_swallow_cancellation(tmp_path):
    from unittest.mock import patch
    from clipper.worker import Interrupted
    with patch("clipper.media.executable", return_value="ffmpeg"), patch("clipper.media.run_command", side_effect=Interrupted("cancel")):
        with pytest.raises(Interrupted):
            choose_encoder("auto", tmp_path)


def test_auto_render_retries_failed_nvenc_and_records_actual_cpu(tmp_path):
    import json
    from unittest.mock import patch
    attempted = []

    def process(arguments, directory, guard, **kwargs):
        if "-c:v" in arguments:
            selected = arguments[arguments.index("-c:v") + 1]
            attempted.append(selected)
            if selected == "h264_nvenc":
                raise MediaError("encoder device lost")
            (directory / "output.partial.mp4").write_bytes(b"video")
        return ""

    decision = {"requested": "auto", "actual": "h264_nvenc", "fallback_reason": None}
    with patch("clipper.media.ffmpeg", side_effect=process), patch("clipper.media.probe", return_value={"width": 180, "height": 320, "duration": 2}):
        output = render(tmp_path / "source.mp4", 0, 2, tmp_path, [], False, width=180, height=320,
                        encoder="auto", encoder_decision=decision)
    assert attempted == ["h264_nvenc", "libx264"]
    assert output.exists()
    report = json.loads((tmp_path / "encoding.json").read_text())
    assert report["actual"] == "libx264"
    assert "retried with CPU" in report["fallback_reason"]
    assert decision["actual"] == "h264_nvenc"
    assert len(report["attempts"]) == 2
    assert not report["attempts"][0]["success"] and report["attempts"][1]["success"]
    assert set(report["timings"]) == {"encode_seconds", "inspect_seconds", "validate_seconds"}


def test_forced_render_failure_does_not_silently_change_encoder(tmp_path):
    from unittest.mock import patch
    with patch("clipper.media.ffmpeg", side_effect=MediaError("encoder failed")):
        with pytest.raises(MediaError):
            render(tmp_path / "source.mp4", 0, 2, tmp_path, [], False, encoder="nvenc",
                   encoder_decision={"requested": "nvenc", "actual": "h264_nvenc"})
    assert not (tmp_path / "clip.mp4").exists()


def test_windows_process_cleanup_does_not_depend_on_system32_in_path():
    import os
    from unittest.mock import Mock, patch
    if os.name != "nt":
        pytest.skip("Windows process cleanup")
    process = Mock(pid=1234)
    process.poll.return_value = None
    with patch("clipper.media.shutil.which", return_value=None), patch("clipper.media.subprocess.run") as command:
        terminate(process)
    assert Path(command.call_args.args[0][0]).name == "taskkill.exe"
    assert Path(command.call_args.args[0][0]).is_absolute()
    process.wait.assert_called_once()


def test_silent_video_does_not_fill_clip_quota():
    features = {"audio": [{"time": index, "value": -100} for index in range(60)], "visual": []}
    assert rank_candidates(features, 60, RunOptions()) == []


def test_scene_hints_snap_only_nearby_edges_without_losing_peak_or_minimum_length():
    assert scene_boundaries(20, 60, 40, 100, [18, 62]) == (18, 62)
    assert scene_boundaries(20, 60, 40, 100, [10, 70]) == (20, 60)
    assert scene_boundaries(20, 35, 30, 100, [23, 33]) == (20, 35)
    assert scene_boundaries(20, 60, 40, 100, [float("nan"), -1, 200]) == (20, 60)


def test_scene_boundary_snapping_does_not_override_usable_sentence_context():
    words = [{"start": float(index), "end": index + 0.8,
              "word": "finished." if index in {7, 35, 55} else "word"} for index in range(60)]
    features = {"scenes": [11, 33], "visual": [{"time": 25, "value": 80}, {"time": 0, "value": 0}, {"time": 1, "value": 0}]}
    selected = rank_candidates(features, 100, RunOptions(clip_seconds=20), words)
    assert len(selected) == 1
    assert selected[0].start == pytest.approx(7.88)
    assert selected[0].end == pytest.approx(35.98)


def test_reported_shared_footage_is_suppressed_but_unrelated_clip_can_fill_limit():
    candidates = [Candidate(start=928.96, end=978.9, score=0.735, reason="activity"),
                  Candidate(start=972.1, end=1017.62, score=0.711, reason="activity"),
                  Candidate(start=1100, end=1140, score=0.7, reason="activity")]
    selected = distinct_candidates(candidates, 2)
    assert [candidate.start for candidate in selected] == [928.96, 1100]


@pytest.mark.parametrize("probability,expected", [(0.95, 1), (0.1, 2), (None, 2)])
def test_repeated_long_dialogue_only_suppressed_with_usable_confidence(probability, expected):
    candidates = [Candidate(start=0, end=30, score=0.8, reason="activity"),
                  Candidate(start=60, end=90, score=0.7, reason="activity")]
    sentence = "The final round begins with a narrow lead and one remaining chance to reach the finish line before the clock runs out"
    words = [{"start": offset + index, "end": offset + index + 0.8, "word": word, "probability": probability}
             for offset in (0, 60) for index, word in enumerate(sentence.split())]
    assert len(distinct_candidates(candidates, 2, words)) == expected


def test_repeated_short_catchphrase_does_not_discard_distinct_action():
    candidates = [Candidate(start=0, end=30, score=0.8, reason="activity"),
                  Candidate(start=60, end=90, score=0.7, reason="activity")]
    words = [{"start": offset + index, "end": offset + index + 0.8, "word": word, "probability": 0.95}
             for offset in (0, 60) for index, word in enumerate("What a great shot".split())]
    assert len(distinct_candidates(candidates, 2, words)) == 2


def test_silent_visual_event_is_retained_and_clamped():
    visual = [{"time": index, "value": 0 if index < 58 else 80} for index in range(60)]
    candidates = rank_candidates({"visual": visual}, 60, RunOptions())
    assert len(candidates) == 1
    assert candidates[0].reason == "Visual activity change"
    assert 0 <= candidates[0].start < candidates[0].end <= 60


def test_boundary_extends_to_sentence_end_not_arbitrary_next_word():
    words = [{"start": float(index), "end": index + 0.8,
              "word": "finished." if index in {7, 35, 55} else "word"} for index in range(60)]
    adjusted = speech_boundaries(10, 30, 25, 100, words)
    assert adjusted is not None
    start, end = adjusted
    assert start == pytest.approx(7.88)
    assert end == pytest.approx(35.98)


def test_boundary_uses_pause_when_punctuation_is_missing():
    words = [{"start": float(index), "end": index + 0.8, "word": "word"} for index in range(35)]
    words += [{"start": 38.0, "end": 38.8, "word": "next"}]
    adjusted = speech_boundaries(0, 30, 25, 100, words)
    assert adjusted is not None
    assert adjusted[1] == pytest.approx(34.98)


def test_continuous_sentence_without_safe_boundary_is_not_cut():
    words = [{"start": index * 0.5, "end": index * 0.5 + 0.45, "word": "word"} for index in range(240)]
    assert speech_boundaries(10, 30, 25, 120, words) is None


def test_silent_action_and_source_edges_are_preserved():
    assert speech_boundaries(10, 40, 25, 100, [{"start": 60, "end": 62, "word": "Later."}]) == (10, 40)
    assert speech_boundaries(0, 20, 15, 20, [{"start": 19, "end": 20, "word": "ending"}]) == (0, 20)


def test_captions_use_clip_relative_times_and_escape_markup(tmp_path: Path):
    words = [{"start": 10.1, "end": 11, "word": "<test>\nword"}]
    path = tmp_path / "captions.srt"
    assert write_captions(words, 10, 12, path) == 1
    assert "00:00:00,100 --> 00:00:01,000" in path.read_text()
    assert "<test>" not in path.read_text()


def test_caption_canvas_and_style_use_output_pixels_not_video_zoom(tmp_path: Path):
    path = tmp_path / "captions.ass"
    words = [{"start": 10.1, "end": 11, "word": "{\\fs500}test"}]
    assert write_ass_captions(words, 10, 12, path, 1080, 1920) == 1
    contents = path.read_text()
    assert "PlayResX: 1080\nPlayResY: 1920" in contents
    assert "Segoe UI,97.2," in contents
    assert ",86,86,173,1" in contents
    assert ",100,100,0,0,1," in contents
    assert "Dialogue: 0,0:00:00.10,0:00:01.00" in contents
    assert "{\\fs500}" not in contents


def test_caption_glyph_height_and_aspect_are_stable_across_video_zoom(tmp_path: Path):
    import subprocess
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "gray.mp4"
    ffmpeg(["-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=2",
            "-c:v", "libx264", str(source)], tmp_path)
    bounds = []
    for zoom in (1, 2):
        output = render(source, 0, 2, tmp_path / f"caption-{zoom}",
                        [{"start": 0.2, "end": 1.8, "word": "Minimum"}], False,
                        width=360, height=640, zoom=zoom)
        pixels = subprocess.run([executable("ffmpeg"), "-v", "error", "-ss", "1", "-i", str(output),
                                 "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                capture_output=True, check=True).stdout
        points = [(index % 360, index // 360) for index in range(360 * 320, 360 * 640)
                  if min(pixels[index * 3:index * 3 + 3]) > 220]
        assert points
        left, right = min(point[0] for point in points), max(point[0] for point in points)
        top, bottom = min(point[1] for point in points), max(point[1] for point in points)
        assert 16 <= bottom - top + 1 <= 36
        assert 2.5 <= (right - left + 1) / (bottom - top + 1) <= 7
        assert left > 20 and right < 340
        bounds.append((left, top, right, bottom))
    assert bounds[0] == bounds[1]


def test_caption_glyphs_double_previous_size_and_wrapped_text_stays_inside_margins(tmp_path: Path):
    import subprocess
    from unittest.mock import patch
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "gray.mp4"
    ffmpeg(["-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=2",
            "-c:v", "libx264", str(source)], tmp_path)

    def bounds(output):
        pixels = subprocess.run([executable("ffmpeg"), "-v", "error", "-ss", "1", "-i", str(output),
                                 "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                capture_output=True, check=True).stdout
        points = [(index % 360, index // 360) for index in range(360 * 320, 360 * 640)
                  if min(pixels[index * 3:index * 3 + 3]) > 220]
        assert points
        return (min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points))

    words = [{"start": 0.2, "end": 1.8, "word": "Minimum"}]
    with patch("clipper.media.CAPTION_FONT_WIDTH_RATIO", 0.045):
        previous = bounds(render(source, 0, 2, tmp_path / "previous", words, False, width=360, height=640))
    current = bounds(render(source, 0, 2, tmp_path / "current", words, False, width=360, height=640))
    assert 1.7 <= (current[3] - current[1] + 1) / (previous[3] - previous[1] + 1) <= 2.3
    assert 1.7 <= (current[2] - current[0] + 1) / (previous[2] - previous[0] + 1) <= 2.3
    with patch("clipper.media.CAPTION_BOTTOM_MARGIN_RATIO", 0.065):
        lower = bounds(render(source, 0, 2, tmp_path / "previous-position", words, False, width=360, height=640))
    assert lower[0] == current[0] and lower[2] == current[2]
    assert lower[1] - current[1] == lower[3] - current[3] == 16
    wrapped = bounds(render(source, 0, 2, tmp_path / "wrapped",
                            [{"start": 0.2, "end": 1.8, "word": "What server are you playing on?"}],
                            False, width=360, height=640))
    assert wrapped[0] >= 26 and wrapped[2] <= 334
    assert wrapped[1] >= 320 and wrapped[3] < 610


def test_caption_cues_split_at_silence_and_expire_even_with_bad_word_end(tmp_path: Path):
    path = tmp_path / "captions.srt"
    words = [
        {"start": 0.1, "end": 0.5, "word": "first"},
        {"start": 8, "end": 30, "word": "last"},
    ]
    assert write_captions(words, 0, 30, path) == 2
    contents = path.read_text()
    assert "00:00:00,100 --> 00:00:00,500" in contents
    assert "00:00:08,000 --> 00:00:11,000" in contents
    assert "first last" not in contents


def test_caption_disappears_in_decoded_video_without_next_cue(tmp_path: Path):
    import subprocess
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "static.mp4"
    ffmpeg(["-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=15:duration=7",
            "-c:v", "libx264", str(source)], tmp_path)
    output = render(source, 0, 7, tmp_path / "export",
                    [{"start": 0.2, "end": 30, "word": "expires"}], False, width=360, height=640)

    def frame(moment):
        return subprocess.run([executable("ffmpeg"), "-v", "error", "-ss", str(moment), "-i", str(output),
                               "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                              capture_output=True, check=True).stdout

    visible, expired = frame(1), frame(5)
    assert len(visible) == len(expired) == 360 * 640 * 3
    assert sum(value > 220 for value in visible[360 * 320 * 3:]) > 50
    assert sum(value > 220 for value in expired[360 * 320 * 3:]) == 0


def test_real_ffmpeg_render_and_corrupt_input(tmp_path: Path):
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "source with spaces.mp4"
    ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", str(source)], tmp_path)
    assert probe(source, tmp_path)["has_audio"]
    output = render(source, 0.5, 2.5, tmp_path / "export", [{"start": 1, "end": 1.8, "word": "caption"}], True, width=180, height=320)
    assert output.exists()
    assert not (output.parent / "output.partial.mp4").exists()
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"not video")
    with pytest.raises(MediaError):
        probe(bad, tmp_path / "bad")


def test_portrait_background_has_no_added_black_bars_and_zoom_is_partial(tmp_path: Path):
    import subprocess
    configure_tools()
    if not executable("ffmpeg") or not executable("ffprobe"):
        pytest.skip("FFmpeg integration tools not installed")
    source = tmp_path / "framing.mp4"
    ffmpeg(["-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=15:duration=1",
            "-vf", "drawbox=x=156:y=0:w=8:h=180:color=red:t=fill", "-c:v", "libx264", str(source)], tmp_path)
    red_heights = []
    for zoom in (1, 1.5):
        output = render(source, 0, 1, tmp_path / f"zoom-{zoom}", [], False, width=180, height=320, zoom=zoom)
        pixels = subprocess.run([executable("ffmpeg"), "-v", "error", "-i", str(output), "-frames:v", "1",
                                 "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        def pixel(column, row):
            offset = (row * 180 + column) * 3
            return tuple(pixels[offset:offset + 3])
        assert min(pixel(10, 5)) > 60
        assert min(pixel(10, 315)) > 60
        red_heights.append(sum(pixel(90, row)[0] > pixel(90, row)[1] + 70 for row in range(320)))
    assert 95 <= red_heights[0] <= 105
    assert 145 <= red_heights[1] <= 155
    assert red_heights[1] < 320