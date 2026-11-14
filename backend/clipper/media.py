from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Callable

from .preflight import executable

CAPTION_FONT_WIDTH_RATIO = 0.09
CAPTION_BOTTOM_MARGIN_RATIO = 0.09
SCENE_THRESHOLD = 12


class MediaError(RuntimeError):
    pass


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill") or str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe")
        subprocess.run([taskkill, "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_command(arguments: list[str], directory: Path, guard: Callable[[], None] = lambda: None,
                timeout: float = 6 * 3600, log_name: str = "process.log") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / log_name
    error_path = directory / (log_name + ".stderr")
    started = time.monotonic()
    with output_path.open("wb") as output, error_path.open("wb") as errors:
        process = subprocess.Popen(
            arguments, cwd=directory, stdout=output, stderr=errors, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            while process.poll() is None:
                guard()
                if time.monotonic() - started > timeout:
                    raise MediaError("Processing exceeded its stage time limit.")
                Event().wait(0.25)
            guard()
            if process.returncode:
                details = error_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                lines = [line.strip() for line in details.splitlines() if line.strip()]
                diagnostics = [line for line in lines if any(text in line.lower() for text in (
                    "driver does not support", "minimum required", "no nvenc capable", "cannot load", "not found or cannot be loaded",
                ))]
                cause = " ".join(diagnostics[:2]) if diagnostics else (lines[-1] if lines else "No diagnostic output was produced.")
                raise MediaError(f"Media process failed (exit {process.returncode}): {cause[:600]}")
        finally:
            terminate(process)
    return output_path.read_text(encoding="utf-8", errors="replace")


def ffmpeg(arguments: list[str], directory: Path, guard: Callable[[], None] = lambda: None,
           *, log_name: str = "process.log", timeout: float = 6 * 3600) -> str:
    tool = executable("ffmpeg")
    if not tool:
        raise MediaError("FFmpeg is missing. Run clipper doctor.")
    threads = os.environ.get("CLIPPER_THREADS", "4")
    return run_command([tool, "-hide_banner", "-nostdin", "-y", "-v", "error", "-threads", threads, "-filter_threads", "2", *arguments], directory, guard, log_name=log_name, timeout=timeout)


def encoder_arguments(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "22", "-b:v", "0"]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", "4"]
    raise ValueError("Unsupported video encoder.")


def choose_encoder(requested: str, directory: Path, guard: Callable[[], None] = lambda: None) -> dict:
    if requested not in {"auto", "cpu", "nvenc"}:
        raise ValueError("Encoder must be auto, cpu or nvenc.")
    decision = {"requested": requested, "actual": "libx264", "fallback_reason": None, "probe_seconds": 0.0}
    if requested == "cpu":
        return decision
    tool = executable("ffmpeg")
    if not tool:
        raise MediaError("FFmpeg is missing. Run clipper doctor.")
    started = time.monotonic()
    try:
        run_command([
            tool, "-hide_banner", "-nostdin", "-v", "error", "-filter_threads", "1",
            "-f", "lavfi", "-i", "color=c=gray:size=1080x1920:rate=30:duration=0.1",
            "-an", "-frames:v", "3", "-pix_fmt", "yuv420p", *encoder_arguments("h264_nvenc"),
            "-f", "null", "-",
        ], directory, guard, timeout=20, log_name="nvenc-probe.log")
        decision["actual"] = "h264_nvenc"
    except (MediaError, OSError) as error:
        if requested == "nvenc":
            raise MediaError(f"Forced NVENC is unavailable: {str(error)[-400:]}") from error
        decision["fallback_reason"] = "NVENC execution probe failed; using CPU encoding. " + str(error)[-300:]
    decision["probe_seconds"] = round(time.monotonic() - started, 3)
    return decision


def probe(path: Path, directory: Path, guard: Callable[[], None] = lambda: None) -> dict:
    tool = executable("ffprobe")
    if not tool:
        raise MediaError("FFprobe is missing. Run clipper doctor.")
    result = json.loads(run_command([
        tool, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
    ], directory, guard, timeout=60, log_name="probe.json"))
    video = next((stream for stream in result.get("streams", []) if stream["codec_type"] == "video"), None)
    if not video:
        raise MediaError("The source has no video stream.")
    duration = float(result.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("The recording has no valid finite duration.")
    return {
        "duration": duration, "width": int(video["width"]), "height": int(video["height"]),
        "has_audio": any(stream["codec_type"] == "audio" for stream in result["streams"]),
        "bytes": path.stat().st_size, "codec": video.get("codec_name"),
        "color_transfer": video.get("color_transfer"),
    }


def read_metadata(path: Path, key: str) -> list[dict[str, float]]:
    values = []
    current_time = None
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "pts_time:" in line:
            current_time = float(line.split("pts_time:", 1)[1].split()[0])
        elif line.startswith(key + "=") and current_time is not None:
            value = float(line.split("=", 1)[1])
            values.append({"time": current_time, "value": value if math.isfinite(value) else -100.0})
    return values


def visual_analysis_arguments(source: Path, duration: float, codec: str | None, gpu: bool) -> list[str]:
    metadata = ("signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=visual.txt,"
                f"scdet=threshold={SCENE_THRESHOLD},metadata=print:key=lavfi.scd.time:file=scenes.txt")
    if gpu:
        decoders = {"av1": "av1_cuvid", "h264": "h264_cuvid", "hevc": "hevc_cuvid"}
        if codec not in decoders:
            raise MediaError("This codec has no configured NVDEC analysis path.")
        inputs = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-extra_hw_frames", "4",
                  "-c:v", decoders[codec], "-surfaces", "16"]
        filters = "fps=1,scale_cuda=w=160:h=-2:format=nv12:interp_algo=bicubic:passthrough=0,hwdownload,format=nv12,format=yuv420p," + metadata
    else:
        inputs = []
        filters = "fps=1,scale=160:-2,format=yuv420p," + metadata
    return [*inputs, "-i", str(source), "-t", str(duration), "-map", "0:v:0", "-an", "-vf", filters, "-f", "null", "-"]


def validated_visual_metadata(path: Path, duration: float) -> list[dict]:
    values = read_metadata(path, "lavfi.signalstats.YDIF")
    if len(values) < max(1, math.floor(duration) - 1) or len(values) > math.ceil(duration) + 2:
        raise MediaError("NVDEC analysis returned incomplete visual sample coverage.")
    if any(not 0 <= item["value"] <= 255 or not math.isfinite(item["time"])
           or not 0 <= item["time"] <= duration + 1 for item in values) or values[0]["time"] > 1:
        raise MediaError("NVDEC analysis returned invalid visual values.")
    if any(abs(current["time"] - previous["time"] - 1) > 0.001 for previous, current in zip(values, values[1:])):
        raise MediaError("NVDEC analysis returned an irregular source timeline.")
    return values


def extract_features(source: Path, info: dict, directory: Path, guard: Callable[[], None], decoder: str = "cpu") -> dict:
    if decoder not in {"auto", "cpu", "nvdec"}:
        raise ValueError("Analysis decoder must be auto, cpu or nvdec.")
    timings = {"audio_seconds": 0.0, "visual_seconds": 0.0}
    (directory / "audio.txt").unlink(missing_ok=True)
    (directory / "visual.txt").unlink(missing_ok=True)
    (directory / "scenes.txt").unlink(missing_ok=True)
    if info["has_audio"]:
        started = time.perf_counter()
        ffmpeg([
            "-i", str(source), "-t", str(info["duration"]), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "8000", "-af",
            "aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=audio.txt",
            "-f", "null", "-",
        ], directory, guard, log_name="analysis-audio.log")
        timings["audio_seconds"] = round(time.perf_counter() - started, 3)
    started = time.perf_counter()
    actual, fallback = "cpu", None
    use_gpu = decoder != "cpu" and info.get("codec") in {"av1", "h264", "hevc"}
    if decoder == "nvdec" and not use_gpu:
        raise MediaError("Forced NVDEC does not support this source codec.")
    if use_gpu:
        try:
            ffmpeg(visual_analysis_arguments(source, info["duration"], info.get("codec"), True),
                   directory, guard, log_name="analysis-visual-nvdec.log", timeout=max(30, info["duration"] * 2))
            validated_visual_metadata(directory / "visual.txt", info["duration"])
            actual = "nvdec"
        except (MediaError, ValueError) as error:
            if decoder == "nvdec":
                raise MediaError(f"Forced NVDEC analysis failed: {error}") from error
            guard()
            fallback = "NVDEC analysis failed; CPU analysis used. " + str(error)[-350:]
    elif decoder == "auto":
        fallback = "Source codec is not configured for NVDEC; CPU analysis used."
    if actual == "cpu":
        (directory / "visual.txt").unlink(missing_ok=True)
        (directory / "scenes.txt").unlink(missing_ok=True)
        ffmpeg(visual_analysis_arguments(source, info["duration"], info.get("codec"), False),
               directory, guard, log_name="analysis-visual-cpu.log")
    timings["visual_seconds"] = round(time.perf_counter() - started, 3)
    scenes = sorted({item["value"] for item in read_metadata(directory / "scenes.txt", "lavfi.scd.time")
                     if 0 < item["value"] < info["duration"]})
    return {
        "audio": read_metadata(directory / "audio.txt", "lavfi.astats.Overall.RMS_level"),
        "visual": read_metadata(directory / "visual.txt", "lavfi.signalstats.YDIF"),
        "scenes": scenes,
        "analysis": {"version": 3, "requested": decoder, "video_decode": actual,
                 "video_scale": "cuda" if actual == "nvdec" else "cpu", "features": "cpu",
                     "scene_detection": {"method": "ffmpeg-scdet", "sample_fps": 1,
                                         "threshold": SCENE_THRESHOLD, "cuts": len(scenes)},
                     "sample_seconds": info["duration"], "codec": info.get("codec"),
                     "fallback_reason": fallback, "timings": timings},
    }


def timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def caption_cues(words: list[dict], start: float, end: float) -> list[tuple[float, float, str]]:
    relevant = []
    for word in sorted(words, key=lambda item: item["start"]):
        left = max(start, word["start"])
        right = min(end, word["end"], word["start"] + 3)
        if math.isfinite(left) and math.isfinite(right) and right > left:
            text = str(word["word"]).replace("\r", " ").replace("\n", " ").replace("<", "").replace(">", "").strip()
            if text:
                relevant.append({"start": left, "end": right, "word": text})
    groups = []
    group = []
    for word in relevant:
        if group and (
            word["start"] - group[-1]["end"] > 0.65
            or word["end"] - group[0]["start"] > 3
            or len(group) >= 6
            or len(" ".join(item["word"] for item in [*group, word])) > 42
            or group[-1]["word"].rstrip('\"\')]}').endswith((".", "?", "!"))
        ):
            groups.append(group)
            group = []
        group.append(word)
    if group:
        groups.append(group)
    cues = []
    for index, group in enumerate(groups):
        text = " ".join(word["word"] for word in group)
        left = group[0]["start"] - start
        right = min(end, group[-1]["end"], group[0]["start"] + 3) - start
        if index + 1 < len(groups):
            right = min(right, groups[index + 1][0]["start"] - start)
        if right > left and text.strip():
            cues.append((left, right, text))
    return cues


def write_captions(words: list[dict], start: float, end: float, path: Path) -> int:
    cues = [f"{index + 1}\n{timestamp(left)} --> {timestamp(right)}\n{text}\n"
            for index, (left, right, text) in enumerate(caption_cues(words, start, end))]
    path.write_text("\n".join(cues), encoding="utf-8")
    return len(cues)


def write_ass_captions(words: list[dict], start: float, end: float, path: Path, width: int, height: int) -> int:
    cues = caption_cues(words, start, end)
    font_size = round(width * CAPTION_FONT_WIDTH_RATIO, 2)
    outline = round(width * 0.0056, 2)
    margin_x, margin_y = round(width * 0.08), round(height * CAPTION_BOTTOM_MARGIN_RATIO)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Segoe UI,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{outline},0,2,{margin_x},{margin_x},{margin_y},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def ass_time(seconds: float) -> str:
        centiseconds = max(0, round(seconds * 100))
        hours, remainder = divmod(centiseconds, 360000)
        minutes, remainder = divmod(remainder, 6000)
        whole_seconds, fraction = divmod(remainder, 100)
        return f"{hours}:{minutes:02}:{whole_seconds:02}.{fraction:02}"

    events = []
    for left, right, text in cues:
        safe = text.replace("\\", "/").replace("{", "(").replace("}", ")")
        events.append(f"Dialogue: 0,{ass_time(left)},{ass_time(right)},Default,,0,0,0,,{safe}\n")
    path.write_text(header + "".join(events), encoding="utf-8")
    return len(cues)


def render(source: Path, start: float, end: float, directory: Path, words: list[dict],
           has_audio: bool, guard: Callable[[], None] = lambda: None,
           width: int = 1080, height: int = 1920, zoom: float = 1.5,
           encoder: str = "cpu", encoder_decision: dict | None = None) -> Path:
    if not math.isfinite(zoom) or not 1 <= zoom <= 2:
        raise ValueError("Zoom must be between 1 and 2.")
    directory.mkdir(parents=True, exist_ok=True)
    decision = dict(encoder_decision) if encoder_decision is not None else choose_encoder(encoder, directory, guard)
    if decision.get("requested") != encoder:
        raise ValueError("Encoder decision does not match the requested mode.")
    actual = decision["actual"]
    encoder_arguments(actual)
    started = time.monotonic()
    write_captions(words, start, end, directory / "captions.srt")
    cue_count = write_ass_captions(words, start, end, directory / "captions.ass", width, height)
    background_width, background_height = max(2, width // 16 * 2), max(2, height // 16 * 2)
    factor = f"min({width}/iw,{height}/ih)*{zoom}"
    filters = (
        "[0:v]scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,split=2[background][foreground];"
        f"[background]scale={background_width}:{background_height}:force_original_aspect_ratio=increase,"
        f"crop={background_width}:{background_height},gblur=sigma=8,scale={width}:{height}[backdrop];"
        f"[foreground]scale=w='max(2,trunc(iw*{factor}/2)*2)':h='max(2,trunc(ih*{factor}/2)*2)',"
        f"crop=w='min(iw,{width})':h='min(ih,{height})':x=(iw-ow)/2:y=(ih-oh)/2[subject];"
        "[backdrop][subject]overlay=x=(W-w)/2:y=(H-h)/2:shortest=1,setsar=1,fps=30,format=yuv420p"
    )
    if cue_count:
        filters += f",subtitles=captions.ass:original_size={width}x{height}"
    filters += "[composed]"
    arguments = ["-ss", str(start), "-i", str(source), "-t", str(end - start), "-filter_complex_threads", "2",
                 "-filter_complex", filters, "-map", "[composed]", "-map", "0:a:0?"]
    if has_audio:
        arguments += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
    partial = directory / "output.partial.mp4"
    attempts = []

    def encode_and_validate(selected: str) -> dict:
        attempt = {"encoder": selected, "encode_seconds": 0.0, "inspect_seconds": 0.0,
                   "validate_seconds": 0.0, "success": False}
        attempts.append(attempt)

        def measured(name, function):
            begin = time.perf_counter()
            try:
                return function()
            finally:
                attempt[name] = round(time.perf_counter() - begin, 3)

        measured("encode_seconds", lambda: ffmpeg(
            [*arguments, *encoder_arguments(selected), "-movflags", "+faststart", "output.partial.mp4"],
            directory, guard, log_name=f"encode-{selected}.log"))
        info = measured("inspect_seconds", lambda: probe(partial, directory, guard))
        if info["width"] != width or info["height"] != height or abs(info["duration"] - (end - start)) > 0.6:
            raise MediaError("Rendered clip failed dimensions/duration validation.")
        measured("validate_seconds", lambda: ffmpeg(["-i", str(partial), "-f", "null", "-"], directory, guard))
        attempt["success"] = True
        return info

    try:
        encode_and_validate(actual)
    except MediaError as error:
        if encoder != "auto" or actual != "h264_nvenc":
            raise
        guard()
        partial.unlink(missing_ok=True)
        decision["fallback_reason"] = "NVENC render or validation failed; retried with CPU encoding. " + str(error)[-350:]
        actual = "libx264"
        encode_and_validate(actual)
    decision.update({"actual": actual, "render_seconds": round(time.monotonic() - started, 3),
                     "timings": {name: round(sum(attempt[name] for attempt in attempts), 3)
                                 for name in ("encode_seconds", "inspect_seconds", "validate_seconds")},
                     "attempts": attempts,
                     "renderer_version": 6, "video_codec": "h264", "pixel_format": "yuv420p",
                     "captions": {"canvas": [width, height], "font_size_px": round(width * CAPTION_FONT_WIDTH_RATIO, 2),
                                  "bottom_margin_px": round(height * CAPTION_BOTTOM_MARGIN_RATIO),
                                  "scale_x": 100, "scale_y": 100, "zoom_independent": True},
                     "decode": "cpu", "filters": "cpu", "bytes": partial.stat().st_size})
    metadata = directory / "encoding.partial.json"
    metadata.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    metadata.replace(directory / "encoding.json")
    destination = directory / "clip.mp4"
    partial.replace(destination)
    return destination