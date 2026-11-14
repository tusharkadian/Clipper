from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from .analysis import DIALOGUE_MIN_TOKENS, DIALOGUE_SIMILARITY, MAX_SHARED_SECONDS, SCENE_SNAP_SECONDS, rank_candidates
from .config import Settings, configure_tools
from .domain import ClipPlan, RunOptions
from .media import MediaError, choose_encoder, extract_features, ffmpeg, probe, render, run_command
from .preflight import benchmark_environment, capture, cuda_libraries_ready, executable, parse_gpu_rows
from .retention import prune_expired
from .rerank import (MAX_SHORTLIST, MODEL_REPOSITORY, MODEL_SHA256, QUALITY_GUARD_VERSION, RANKER_VERSION, SYSTEM_PROMPT,
                     blend_assessments, is_ready as reranker_ready, transcript_windows)
from .speech import transcript_health
from .storage import Store
from .runtime import RuntimeMonitor, inhibit_idle_sleep


class Interrupted(RuntimeError):
    def __init__(self, action: str):
        self.action = action


def write_json(path: Path, value: dict | list) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        for attempt in range(6):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                Event().wait(0.02 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)


def publish_heartbeat(directory: Path, owner: str) -> bool:
    try:
        write_json(directory / "worker.json", {"owner": owner, "updated": time.time(), "pid": os.getpid()})
        return True
    except PermissionError:
        return False


def workspace_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class Processor:
    def __init__(self, settings: Settings, store: Store, job: dict, owner: str, stop: Event):
        self.settings, self.store, self.job, self.owner, self.stop = settings, store, job, owner, stop
        self.directory = settings.data / "runs" / job["id"]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.options = RunOptions.model_validate(job["options"])
        self.last_check = 0.0
        self.last_cleanup = time.monotonic()
        self.timings: dict[str, float] = {}
        self.stage_cache_hits: dict[str, bool] = {}
        self.warnings: list[str] = []
        self.gpu_peak_mb = 0.0
        self.gpu_peak_c: float | None = None
        self.ram_peak_mb = 0.0
        self.cache_key = ""
        self.started = time.monotonic()
        self.encoder_decision: dict | None = None
        self.analysis_report: dict = {}
        self.source_sha256: str | None = None
        self.runtime_monitor = RuntimeMonitor()
        self.power_request = {"requested": False, "enabled": False, "release_on_exit": False, "error": None}
        self.job_timeout: float | None = None
        self.benchmark_environment: dict = {}
        self.source_info: dict = {}
        self.completed_exports: list = []
        self.transcript_report: dict = {"version": 1, "status": "not_run"}

    def guard(self) -> None:
        if self.stop.is_set():
            raise Interrupted("stop")
        if self.job_timeout is not None and time.perf_counter() - self.runtime_monitor.started > self.job_timeout:
            raise MediaError("Benchmark exceeded the requested elapsed-time deadline; inspect timing diagnostics for standby or interruptions.")
        now = time.monotonic()
        if now - self.last_check < 3:
            return
        self.last_check = now
        publish_heartbeat(self.settings.data, self.owner)
        request = self.store.heartbeat(self.job["id"], self.owner)
        if request:
            raise Interrupted(request)
        if now - self.last_cleanup >= 60:
            prune_expired(self.settings, self.store)
            self.last_cleanup = time.monotonic()
        if shutil.disk_usage(self.settings.data).free < self.settings.reserve_bytes:
            raise MediaError("Disk reserve reached; free space before retrying this stage.")
        if workspace_size(self.settings.data) > self.settings.workspace_limit_bytes:
            raise MediaError("Workspace size limit reached; remove unused downloads before retrying.")
        try:
            import psutil
            processes = [psutil.Process(), *psutil.Process().children(recursive=True)]
            self.ram_peak_mb = max(self.ram_peak_mb, sum(process.memory_info().rss for process in processes if process.is_running()) / 1024**2)
        except Exception:
            pass
        gpu_tool = executable("nvidia-smi")
        if gpu_tool:
            try:
                gpus = parse_gpu_rows(capture([gpu_tool, "--query-gpu=name,driver_version,memory.total,memory.free,temperature.gpu,power.draw", "--format=csv,noheader,nounits"], timeout=5))
                for gpu in gpus:
                    self.gpu_peak_mb = max(self.gpu_peak_mb, (gpu["dedicated_mb"] or 0) - (gpu["free_mb"] or 0))
                    if gpu["temperature_c"] is not None:
                        self.gpu_peak_c = max(self.gpu_peak_c or 0, gpu["temperature_c"])
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass

    def stage(self, name: str, progress: float, function, version: int | None = None):
        self.stage_cache_hits[name] = False
        self.guard()
        self.store.progress(self.job["id"], self.owner, name, progress)
        receipt = self.directory / f"{name}.receipt.json"
        key = self.cache_key if version is None else hashlib.sha256(
            f"{self.cache_key}:{name}:{version}".encode()
        ).hexdigest()
        started = time.monotonic()
        if receipt.is_file() and self.cache_key:
            cached = json.loads(receipt.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                self.warnings = list(dict.fromkeys([*self.warnings, *cached.get("warnings", [])]))
                self.stage_cache_hits[name] = True
                self.timings[name] = 0
                return cached["value"]
        result = function()
        self.timings[name] = round(time.monotonic() - started, 3)
        if self.cache_key:
            write_json(receipt, {"key": key, "value": result, "warnings": self.warnings})
        return result

    def analyze(self, source: Path, info: dict, progress: float) -> dict:
        features = self.stage("analyze", progress, lambda: extract_features(
            source, info, self.directory, self.guard, decoder=self.options.analysis_decoder
        ), version=3)
        cached = self.stage_cache_hits.get("analyze", False)
        self.analysis_report = {**features["analysis"], "cached": cached}
        for key, value in features["analysis"]["timings"].items():
            self.timings[f"analysis-{key.removesuffix('_seconds')}"] = 0 if cached else value
        warning = features["analysis"].get("fallback_reason")
        if warning and warning not in self.warnings:
            self.warnings.append(warning)
        return features

    def speech(self, source: Path) -> dict:
        models = self.settings.data / "models"
        if not (models / "small.en/model.bin").is_file():
            raise MediaError("English model missing. Install the English model in Setup, or explicitly disable captions.")
        devices = ["cpu"]
        try:
            import ctranslate2
            if cuda_libraries_ready() and ctranslate2.get_cuda_device_count() and "int8_float16" in ctranslate2.get_supported_compute_types("cuda"):
                devices.insert(0, "cuda")
        except (ImportError, RuntimeError):
            pass
        if self.options.device != "auto":
            devices = [self.options.device]
        elif devices == ["cpu"]:
            self.warnings.append("Automatic speech mode used CPU INT8; GPU runtime is unavailable or unverified.")
        for device in devices:
            try:
                run_command([
                    sys.executable, "-m", "clipper.speech", "--models", str(models), "--source", str(source),
                    "--output", str(self.directory / "words.json"), "--device", device,
                ], self.directory, self.guard, log_name=f"speech-{device}.log")
                result = json.loads((self.directory / "words.json").read_text(encoding="utf-8"))
                if device == "cpu" and len(devices) > 1:
                    self.warnings.append("GPU speech inference failed; this run used CPU INT8 instead.")
                return result
            except MediaError:
                if device == "cpu":
                    raise
        raise MediaError("No supported speech runtime was available.")

    def record_transcript_health(self, transcription: dict) -> dict:
        quality = transcript_health(transcription["words"], transcription.get("segments")) if transcription else {
            "version": 1, "status": "not_run"}
        if quality["status"] == "unreliable":
            self.warnings.append("Transcript quality checks flagged " + ", ".join(reason.replace("_", " ") for reason in quality["reasons"]) +
                                 ". Captions may be inaccurate; activity candidates remain available.")
        self.transcript_report = quality
        return quality

    def failed_benchmark_report(self, message: str) -> dict:
        timing = self.runtime_monitor.snapshot()
        report = {"benchmark": True, "incomplete": True, "exports": self.completed_exports,
                  "requested_options": self.options.model_dump(), "source_sha256": self.source_sha256,
                  "source": self.source_info, "environment": self.benchmark_environment,
                  "transcript": self.transcript_report, "timing": timing, "keep_awake": self.power_request,
                  "stages": self.timings, "elapsed_seconds": timing["elapsed_seconds"],
                  "warnings": [*self.warnings, message, "Incomplete benchmark; do not use elapsed time as a throughput comparison."],
                  "cloud_calls": 0}
        write_json(self.directory / "report.json", report)
        return report

    def select_candidates(self, features: dict, duration: float, words: list[dict], segments: list[dict] | None = None):
        quality = transcript_health(words, segments)
        boundary_words = [] if quality["status"] == "unreliable" else words
        baseline = rank_candidates(features, duration, self.options, boundary_words, segments=segments)
        ranking = {"requested": self.options.ai_rerank, "method": "activity-v1", "status": "disabled",
                   "guard_version": QUALITY_GUARD_VERSION}
        if not self.options.ai_rerank:
            return baseline, ranking
        if not words or not baseline:
            ranking["status"] = "no_transcript_or_candidates"
            return baseline, ranking
        if not reranker_ready(self.settings.data / "models"):
            ranking["status"] = "fallback"
            self.warnings.append("AI reranking unavailable: install the local Qwen model. Activity scores were used.")
            return baseline, ranking
        limit = self.options.max_clips
        shortlist = rank_candidates(features, duration, self.options, boundary_words,
                                    shortlist_limit=min(MAX_SHORTLIST, max(6, limit * 3)), segments=segments)
        all_contexts = transcript_windows(shortlist, words, segments)
        contexts = [context for context in all_contexts if context["quality"]["status"] != "unreliable"]
        rejected = len(all_contexts) - len(contexts)
        ranking.update({"shortlisted": len(shortlist), "excluded_unreliable": rejected,
                        "transcript_quality": [{"candidate_id": context["candidate_id"], **context["quality"]}
                                               for context in all_contexts]})
        if rejected:
            self.warnings.append(f"Unreliable transcript in {rejected} candidate(s); excluded from AI assessment and kept activity scores.")
        if not contexts:
            ranking["status"] = "unreliable_transcript" if rejected else "no_eligible_speech"
            return baseline, ranking
        key = hashlib.sha256(json.dumps({
            "version": RANKER_VERSION, "guard_version": QUALITY_GUARD_VERSION,
            "model_sha256": MODEL_SHA256, "prompt": SYSTEM_PROMPT,
            "candidates": [item.model_dump() for item in shortlist], "contexts": contexts,
        }, sort_keys=True).encode()).hexdigest()
        receipt = self.directory / "rerank.receipt.json"
        started = time.monotonic()
        self.store.progress(self.job["id"], self.owner, "rerank", 0.6, "Local Qwen CPU transcript assessment")
        try:
            result = None
            cached = False
            if receipt.is_file():
                try:
                    saved = json.loads(receipt.read_text(encoding="utf-8"))
                    if saved.get("key") == key:
                        result, cached = saved["value"], True
                except (ValueError, KeyError):
                    pass
            if result is None:
                write_json(self.directory / "rerank-input.json", {"contexts": contexts})
                run_command([
                    sys.executable, "-m", "clipper.rerank", "--models", str(self.settings.data / "models"),
                    "--input", str(self.directory / "rerank-input.json"),
                    "--output", str(self.directory / "rerank-output.json"),
                ], self.directory, self.guard, timeout=250, log_name="rerank.log")
                result = json.loads((self.directory / "rerank-output.json").read_text(encoding="utf-8"))
            selected = blend_assessments(shortlist, contexts, result["assessments"])
            if not cached:
                write_json(receipt, {"key": key, "value": result})
            self.timings["rerank"] = 0 if cached else round(time.monotonic() - started, 3)
            totals = [sum(item[field] for field in ("hook", "payoff", "clarity", "novelty"))
                      for item in result["assessments"]]
            near_perfect = sum(total >= 14 for total in totals) / len(totals)
            ranking.update({"assessed": len(contexts), "cached": cached, "model": MODEL_REPOSITORY,
                            "model_sha256": MODEL_SHA256, "gpu_layers": 0,
                            "assessment_diagnostics": {"score_spread": max(totals) - min(totals),
                                                       "near_perfect_fraction": near_perfect}})
            fallback = None
            if len(contexts) > 1 and max(totals) - min(totals) <= 2:
                fallback = ("no_separation", "Local AI transcript scores were too similar; original activity ranking was retained.")
            elif len(contexts) >= 3 and near_perfect >= 0.75:
                fallback = ("saturated_scores", "Local AI gave near-perfect scores to most candidates; original activity ranking was retained.")
            if fallback:
                ranking["status"] = fallback[0]
                self.warnings.append(fallback[1])
                return baseline, ranking
            context_by_id = {context["candidate_id"]: context for context in contexts}
            ranking.update({"method": RANKER_VERSION, "status": "applied", "model": MODEL_REPOSITORY,
                            "model_sha256": MODEL_SHA256, "shortlisted": len(shortlist), "assessed": len(contexts),
                            "cached": cached, "gpu_layers": 0, "semantic_weight": 0.6,
                            "assessments": [dict(item, evidence=context_by_id[item["candidate_id"]]["evidence"][item["evidence_index"]])
                                            for item in result["assessments"]]})
            return selected[:limit], ranking
        except (MediaError, OSError, ValueError, KeyError) as error:
            self.timings["rerank"] = round(time.monotonic() - started, 3)
            ranking["status"] = "fallback"
            self.warnings.append(f"Local AI reranking failed; activity scores were retained. {str(error)[-250:]}")
            return baseline, ranking

    def execute(self) -> dict:
        started = time.monotonic()
        os.environ["CLIPPER_THREADS"] = str(min(os.cpu_count() or 1, 4 if self.options.profile == "quiet" else 8))
        if self.job["kind"] == "model":
            reranker = self.job["source"] == "qwen-reranker"
            self.store.progress(self.job["id"], self.owner, "install-model", 0.1, "Downloading local model weights; no media is uploaded")
            module = "clipper.rerank" if reranker else "clipper.speech"
            run_command([sys.executable, "-m", module, "--models", str(self.settings.data / "models"), "--install"], self.directory, self.guard, log_name="model.log")
            return {"model": MODEL_REPOSITORY if reranker else "small.en", "elapsed_seconds": round(time.monotonic() - started, 2)}
        source = Path(self.job["source"])
        if self.job["kind"] == "url":
            self.store.progress(self.job["id"], self.owner, "download", 0.02)
            download_receipt = self.directory / "download.json"
            if not download_receipt.exists():
                run_command([sys.executable, "-m", "clipper.download", self.job["source"], str(self.directory)], self.directory, self.guard, log_name="download.log")
            source = Path(json.loads(download_receipt.read_text(encoding="utf-8"))["path"])
        if not source.is_file():
            raise MediaError("Source recording is missing. Import it again.")
        self.store.progress(self.job["id"], self.owner, "fingerprint", 0.08)
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(8 * 1024**2):
                digest.update(chunk)
                self.guard()
        self.source_sha256 = digest.hexdigest()
        self.cache_key = hashlib.sha256(json.dumps({"source": self.source_sha256, "options": self.job["options"], "pipeline": 1}).encode()).hexdigest()
        info = self.stage("probe", 0.1, lambda: probe(source, self.directory, self.guard))
        self.source_info = info
        if info["duration"] > self.settings.max_duration:
            raise MediaError("Recording exceeds the six-hour limit.")
        if info.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
            raise MediaError("HDR sources are not supported yet. Import an SDR recording.")
        if self.options.mode == "benchmark":
            return self.benchmark(source, info, started)
        estimated_bytes = info["duration"] * 128000 / 8 + self.options.max_clips * 150 * 1024**2
        if shutil.disk_usage(self.settings.data).free - estimated_bytes < self.settings.reserve_bytes:
            raise MediaError("Not enough disk space for processing while preserving the OS reserve.")
        speech_available = (self.settings.data / "models/small.en/model.bin").is_file()
        speech_device = "not-run"
        transcription = {}
        needs_transcript = self.options.captions or (self.options.ai_rerank and speech_available and reranker_ready(self.settings.data / "models"))
        if needs_transcript and info["has_audio"]:
            speech_source = self.directory / "speech.flac"
            def prepare_audio():
                ffmpeg(["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(speech_source)], self.directory, self.guard)
                return {"path": str(speech_source)}
            if not (self.settings.data / "models/small.en/model.bin").is_file():
                raise MediaError("English model missing. Install it in Setup or explicitly disable captions.")
            self.stage("audio", 0.15, prepare_audio)
            transcription = self.stage("transcribe", 0.25, lambda: self.speech(speech_source))
            words = transcription["words"]
            speech_device = transcription["device"]
        else:
            words = []
            self.warnings.append("No captions: disabled for this run or source has no audio.")
        quality = self.record_transcript_health(transcription)
        features = self.analyze(source, info, 0.45)
        selection_started = time.monotonic()
        candidates, ranking = self.select_candidates(features, info["duration"], words, transcription.get("segments"))
        self.timings["select"] = round(max(0, time.monotonic() - selection_started - self.timings.get("rerank", 0)), 3)
        write_json(self.directory / "candidates.json", [candidate.model_dump() for candidate in candidates])
        exports = []
        errors = []
        if candidates and self.options.encoder == "nvenc":
            self.encoder_decision = choose_encoder("nvenc", self.directory, self.guard)
            self.timings["encoder-probe"] = self.encoder_decision["probe_seconds"]
        for index, candidate in enumerate(candidates):
            self.guard()
            self.store.progress(self.job["id"], self.owner, "render", 0.65 + 0.3 * index / max(1, len(candidates)))
            folder = self.directory / f"clip-{index + 1:02}"
            try:
                plan = ClipPlan(source_start=candidate.start, source_end=candidate.end, candidate=candidate,
                                zoom=self.options.zoom, ranker=ranking["method"], encoder=self.options.encoder)
                render_key = hashlib.sha256(json.dumps({
                    "upstream": self.cache_key, "renderer": 6, "plan": plan.model_dump(),
                }, sort_keys=True).encode()).hexdigest()
                folder.mkdir(parents=True, exist_ok=True)
                receipt = folder / "complete.json"
                valid_cache = receipt.is_file() and json.loads(receipt.read_text())["key"] == render_key and (folder / "clip.mp4").is_file() and (folder / "encoding.json").is_file()
                if not valid_cache:
                    encoding = self.render_clip(source, candidate.start, candidate.end, folder,
                                                words if self.options.captions else [], info["has_audio"])
                    ffmpeg(["-i", str(folder / "clip.mp4"), "-frames:v", "1", "-vf", "scale=270:-2", str(folder / "thumbnail.jpg")], folder, self.guard)
                    write_json(folder / "plan.json", {**plan.model_dump(), "source_sha256": digest.hexdigest(),
                                                     "pipeline_version": 1, "renderer_version": 6, "encoding": encoding})
                    write_json(receipt, {"key": render_key})
                encoding = json.loads((folder / "encoding.json").read_text(encoding="utf-8"))
                self.encoding_warning(encoding)
                exports.append({"id": folder.name, "start": candidate.start, "end": candidate.end,
                                "score": candidate.score, "reason": candidate.reason,
                                "scene_adjusted": candidate.scene_adjusted,
                                "heuristic_score": candidate.heuristic_score, "semantic_score": candidate.semantic_score,
                                "encoding": encoding})
            except MediaError as error:
                errors.append(f"Clip {index + 1}: {str(error)[-500:]}")
        self.store.progress(self.job["id"], self.owner, "complete", 1)
        result = {
            "exports": exports, "warnings": self.warnings + errors, "source": info, "speech_device": speech_device,
            "transcript": quality,
            "elapsed_seconds": round(time.monotonic() - started, 2), "stages": self.timings,
            "peak_process_ram_mb": round(self.ram_peak_mb), "peak_system_gpu_used_mb": self.gpu_peak_mb,
            "peak_gpu_temperature_c": self.gpu_peak_c, "ranker": ranking["method"], "ranking": ranking, "cloud_calls": 0,
            "encoding": {"requested": self.options.encoder, "actual_encoders": sorted({item["encoding"]["actual"] for item in exports}),
                         "decode": "cpu", "filters": "cpu"},
            "analysis": self.analysis_report,
            "selection": {"version": 2, "maximum_clips": self.options.max_clips, "exported": len(exports),
                          "scene_snap_seconds": SCENE_SNAP_SECONDS, "maximum_shared_seconds": MAX_SHARED_SECONDS,
                          "dialogue_minimum_tokens": DIALOGUE_MIN_TOKENS, "dialogue_similarity": DIALOGUE_SIMILARITY,
                          "scene_adjusted_exports": sum(item["scene_adjusted"] for item in exports)},
        }
        write_json(self.directory / "report.json", result)
        if candidates and not exports:
            raise MediaError("All candidate exports failed. " + " ".join(errors))
        return result

    def encoding_warning(self, encoding: dict) -> None:
        warning = encoding.get("fallback_reason")
        if warning and warning not in self.warnings:
            self.warnings.append(warning)

    def render_clip(self, source: Path, start: float, end: float, folder: Path, words: list[dict], has_audio: bool) -> dict:
        if self.encoder_decision is None:
            self.encoder_decision = choose_encoder(self.options.encoder, self.directory, self.guard)
            self.timings["encoder-probe"] = self.encoder_decision["probe_seconds"]
        render(source, start, end, folder, words, has_audio, self.guard, zoom=self.options.zoom,
               encoder=self.options.encoder, encoder_decision=self.encoder_decision)
        encoding = json.loads((folder / "encoding.json").read_text(encoding="utf-8"))
        self.encoding_warning(encoding)
        if encoding.get("fallback_reason"):
            self.encoder_decision = dict(encoding)
        return encoding

    def benchmark(self, source: Path, info: dict, started: float) -> dict:
        environment = benchmark_environment(self.settings.data)
        self.benchmark_environment = environment
        duration = min(info["duration"], self.options.sample_seconds)
        sample_info = {**info, "duration": duration}
        words = []
        actual_device = "not-run"
        transcript = {}
        if self.options.captions and info["has_audio"]:
            audio = self.directory / "sample.flac"
            self.stage("sample-audio", 0.15, lambda: ffmpeg([
                "-i", str(source), "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", str(audio),
            ], self.directory, self.guard))
            transcript = self.stage("transcribe", 0.3, lambda: self.speech(audio))
            words, actual_device = transcript["words"], transcript["device"]
        quality = self.record_transcript_health(transcript)
        self.analyze(source, sample_info, 0.5)
        exports = []
        self.completed_exports = exports
        length = min(20.0, duration / 2)
        for index, start in enumerate((0.0, max(0.0, duration / 2))):
            folder = self.directory / f"clip-{index + 1:02}"
            self.store.progress(self.job["id"], self.owner, "benchmark-render", 0.7 + index * 0.1)
            begin = time.monotonic()
            encoding = self.render_clip(source, start, start + length, folder, words, info["has_audio"])
            ffmpeg(["-i", str(folder / "clip.mp4"), "-frames:v", "1", "-vf", "scale=270:-2", str(folder / "thumbnail.jpg")], folder, self.guard)
            write_json(folder / "plan.json", {"benchmark": True, "source_start": start, "source_end": start + length,
                                             "layout": "partial-zoom-blurred-background", "zoom": self.options.zoom,
                                             "renderer_version": 6, "encoding": encoding})
            exports.append({"id": folder.name, "start": start, "end": start + length, "score": 0,
                            "reason": "Fixed benchmark interval; not a detected highlight", "encoding": encoding})
            self.timings[f"render-{index + 1}"] = round(time.monotonic() - begin, 3)
        timing = self.runtime_monitor.snapshot()
        if timing["standby_suspected"]:
            self.warnings.append("Possible standby or clock gap during this benchmark; elapsed time is not a comparable processing-speed measurement.")
        elif timing["standby_check"] == "unavailable":
            self.warnings.append("Standby timing detection is unavailable; keep the machine awake for comparable benchmarks.")
        if self.power_request["error"]:
            self.warnings.append("Keep-awake request was not applied: " + self.power_request["error"])
        result = {
            "benchmark": True, "sample_seconds": duration, "speech_device": actual_device,
            "timing": timing, "keep_awake": self.power_request,
            "environment": environment, "requested_options": self.options.model_dump(), "source_sha256": self.source_sha256,
            "transcript": quality,
            "elapsed_seconds": round(time.monotonic() - started, 2), "stages": self.timings,
            "peak_process_ram_mb": round(self.ram_peak_mb), "peak_system_gpu_used_mb": self.gpu_peak_mb,
            "peak_gpu_temperature_c": self.gpu_peak_c, "cloud_calls": 0,
            "exports": exports, "warnings": self.warnings,
            "encoding": {"requested": self.options.encoder, "actual_encoders": sorted({item["encoding"]["actual"] for item in exports}),
                         "decode": "cpu", "filters": "cpu"},
            "analysis": self.analysis_report, "source": info,
        }
        write_json(self.directory / "report.json", result)
        self.store.progress(self.job["id"], self.owner, "complete", 1)
        return result


def work(settings: Settings, stop: Event | None = None, once: bool = False, job_id: str | None = None,
         prevent_sleep: bool = False, job_timeout: float | None = None) -> None:
    configure_tools()
    settings.prepare()
    store = Store(settings.data / "jobs.sqlite3")
    owner = uuid4().hex
    stop = stop or Event()
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except (ImportError, OSError):
        pass
    last_cleanup = None
    while not stop.is_set():
        publish_heartbeat(settings.data, owner)
        if last_cleanup is None or time.monotonic() - last_cleanup >= 60:
            prune_expired(settings, store)
            last_cleanup = time.monotonic()
        job = store.claim(owner, job_id)
        if job:
            processor = Processor(settings, store, job, owner, stop)
            processor.job_timeout = job_timeout
            try:
                with inhibit_idle_sleep(prevent_sleep) as power:
                    processor.power_request = power
                    result = processor.execute()
                store.finish(job["id"], owner, "succeeded", result=result)
            except Interrupted as error:
                status = {"pause": "paused", "cancel": "cancelled", "stop": "paused"}.get(error.action, "failed")
                message = "Processing interrupted at a resumable stage."
                report = processor.failed_benchmark_report(message) if processor.options.mode == "benchmark" else None
                store.finish(job["id"], owner, status, message, result=report)
            except Exception as error:
                message = str(error)[-1800:]
                report = processor.failed_benchmark_report(message) if processor.options.mode == "benchmark" else None
                store.finish(job["id"], owner, "failed", message, result=report)
        if once:
            return
        stop.wait(1)


def main() -> None:
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    work(Settings.load(), stop)


if __name__ == "__main__":
    main()