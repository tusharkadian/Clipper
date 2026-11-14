from __future__ import annotations

import math
import re


def diagnostic_summary(environment: dict, report: dict | None = None) -> dict:
    report = report or {}
    status = report.get("status")
    requested = report.get("requested_options", report.get("options", {}))
    if isinstance(report.get("result"), dict):
        report = report["result"]
        requested = report.get("requested_options", requested)

    def numbers(value, fields):
        return {key: value[key] for key in fields if isinstance(value.get(key), (int, float))
                and not isinstance(value[key], bool) and math.isfinite(value[key])}

    def enum(value, choices):
        return value if isinstance(value, str) and value in choices else "unavailable"

    stages = {"probe", "fingerprint", "audio", "sample-audio", "transcribe", "analyze", "analysis-audio",
              "analysis-visual", "rerank", "select", "encoder-probe", "render-1", "render-2"}
    packages = {name: version for name, version in environment.get("packages", {}).items()
                if name in {"local-clipper", "faster-whisper", "ctranslate2", "numpy", "psutil", "yt-dlp"}
                and isinstance(version, str) and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", version)}
    result = {"schema_version": 1, "sharing": "Review before posting; this is diagnostic metadata, not anonymous telemetry.",
              "environment": {key: environment[key] for key in ("platform", "python", "cpu", "logical_cpus", "ram_gb", "cuda_runtime_ready")
                              if key in environment},
              "packages": packages,
              "source": numbers(report.get("source", {}), ("duration", "width", "height", "bytes")),
              "metrics": numbers(report, ("elapsed_seconds", "peak_process_ram_mb", "peak_system_gpu_used_mb", "peak_gpu_temperature_c")),
              "stages": numbers(report.get("stages", {}), stages),
              "speech_device": enum(report.get("speech_device"), {"cpu", "cuda", "not-run"}),
              "ranking_status": enum(report.get("ranking", {}).get("status"),
                  {"disabled", "applied", "fallback", "no_separation", "saturated_scores", "unreliable_transcript", "no_eligible_speech", "no_transcript_or_candidates"}),
              "transcript_status": enum(report.get("transcript", {}).get("status"),
                  {"usable", "unreliable", "unknown", "no_speech", "not_run"}),
              "analysis_decode": enum(report.get("analysis", {}).get("video_decode"), {"cpu", "nvdec"}),
              "exports": []}
    result["run_status"] = enum(status, {"queued", "running", "paused", "succeeded", "failed", "cancelled"})
    result["requested"] = {
        "profile": enum(requested.get("profile"), {"quiet", "balanced"}),
        "mode": enum(requested.get("mode"), {"clips", "benchmark"}),
        "device": enum(requested.get("device"), {"auto", "cpu", "cuda"}),
        "encoder": enum(requested.get("encoder"), {"auto", "cpu", "nvenc"}),
        "analysis_decoder": enum(requested.get("analysis_decoder"), {"auto", "cpu", "nvdec"}),
        **numbers(requested, ("sample_seconds", "max_clips", "clip_seconds", "threshold", "zoom")),
    }
    for key in ("backend_sha256", "requirements_sha256"):
        value = environment.get(key)
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            result[key] = value
    for item in report.get("exports", []):
        encoding = item.get("encoding", {})
        result["exports"].append({**numbers(item, ("start", "end", "score")),
                                  "encoder": enum(encoding.get("actual"), {"libx264", "h264_nvenc"}),
                                  "encoding": numbers(encoding, ("renderer_version", "render_seconds", "bytes")),
                                  "phases": numbers(encoding.get("timings", {}), ("encode_seconds", "inspect_seconds", "validate_seconds"))})
    result["timing"] = numbers(report.get("timing", {}), ("elapsed_seconds", "unbiased_elapsed_seconds", "possible_suspend_seconds"))
    return result