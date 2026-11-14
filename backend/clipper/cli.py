from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, Settings, configure_tools


def main() -> None:
    parser = argparse.ArgumentParser(prog="clipper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Report local CPU/GPU, disk and media capabilities")
    serve = commands.add_parser("serve", help="Start the loopback web app and worker")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("worker", help="Run the durable processing worker")
    commands.add_parser("model-install", help="Explicitly download English model weights; no media is uploaded")
    commands.add_parser("reranker-install", help="Install verified Qwen Q4 weights and the CPU-only local runtime")
    benchmark = commands.add_parser("benchmark", help="Measure local transcription, analysis and two fixed exports")
    benchmark.add_argument("source", type=Path)
    benchmark.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    benchmark.add_argument("--seconds", type=int, default=1800)
    benchmark.add_argument("--no-captions", action="store_true")
    benchmark.add_argument("--profile", choices=["quiet", "balanced"], default="quiet")
    benchmark.add_argument("--encoder", choices=["auto", "cpu", "nvenc"], default="auto")
    benchmark.add_argument("--analysis-decoder", choices=["auto", "cpu", "nvdec"], default="auto")
    benchmark.add_argument("--keep-awake", action="store_true", help="Request system-awake only while this CLI job runs; display may sleep")
    benchmark.add_argument("--output", type=Path, help="Save the benchmark report outside managed retention; refuses to overwrite")
    benchmark.add_argument("--timeout", type=float, help="Fail after this many elapsed seconds, including standby; checked when execution resumes")
    package = commands.add_parser("package", help="Create a clean Windows testing ZIP without recordings or virtual environments")
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--include-tools", action="store_true")
    package.add_argument("--include-models", action="store_true")
    package.add_argument("--source-only", action="store_true", help="Export public source without build/tool/model assets")
    verify = commands.add_parser("verify-bundle", help="Check extracted transfer files against their manifest")
    verify.add_argument("--root", type=Path, default=ROOT)
    diagnostics = commands.add_parser("diagnostics", help="Export reviewed diagnostic fields without raw source/transcript/log data")
    diagnostics.add_argument("--report", type=Path, help="Optional saved job/benchmark report to summarize")
    diagnostics.add_argument("--output", type=Path, required=True)
    review_init = commands.add_parser("evaluation-init", help="Create private unlabeled review files from retained runs")
    review_init.add_argument("--reports", type=Path, help="Optional local folder of saved report JSON files")
    review_init.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate", help="Measure human labels and explicit held-out accuracy gates")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("evaluation-replay", help="Replay activity selection from cached features without inference")
    replay.add_argument("--manifest", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command in {"package", "verify-bundle"}:
        from .transfer import create_bundle, verify_bundle
        try:
            if arguments.command == "package":
                manifest = create_bundle(ROOT, arguments.output, arguments.include_tools, arguments.include_models, arguments.source_only)
                print(json.dumps({"archive": str(arguments.output.resolve()), "files": len(manifest["files"]),
                                  "tools_included": manifest["tools_included"], "models_included": manifest["models_included"]}, indent=2))
            else:
                print(json.dumps(verify_bundle(arguments.root), indent=2))
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
        return
    settings = Settings.load()
    settings.prepare()
    configure_tools()
    if arguments.command in {"evaluation-init", "evaluate", "evaluation-replay"}:
        from .evaluation import evaluate_workspace, initialize_review_workspace, replay_activity_baseline
        try:
            if arguments.command == "evaluation-init":
                if arguments.reports and not arguments.reports.is_dir():
                    parser.error("The reports folder must exist.")
                value = initialize_review_workspace(settings.data, arguments.output, arguments.reports)
            else:
                value = evaluate_workspace(arguments.manifest) if arguments.command == "evaluate" else replay_activity_baseline(arguments.manifest)
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                with arguments.output.open("x", encoding="utf-8") as output:
                    json.dump(value, output, indent=2)
                value = {"output": str(arguments.output.resolve()), "kind": value.get("kind"), "gate": value.get("gate")}
            print(json.dumps(value, indent=2))
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
    elif arguments.command == "doctor":
        from .preflight import inspect_system
        report = inspect_system(settings.data)
        print(json.dumps(report, indent=2))
        if not report["ready"]:
            sys.exit(1)
    elif arguments.command == "diagnostics":
        from .diagnostics import diagnostic_summary
        from .preflight import benchmark_environment
        try:
            report = json.loads(arguments.report.read_text(encoding="utf-8")) if arguments.report else None
            if report is not None and not isinstance(report, dict):
                parser.error("The diagnostic input must be a JSON report object.")
            value = diagnostic_summary(benchmark_environment(settings.data), report)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            with arguments.output.open("x", encoding="utf-8") as output:
                json.dump(value, output, indent=2)
            print("Diagnostic summary saved. Review it before attaching to a public issue.")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            parser.error(f"Diagnostic export failed ({type(error).__name__}); no raw report content is printed.")
    elif arguments.command == "worker":
        from .worker import main as worker_main
        worker_main()
    elif arguments.command == "model-install":
        from .speech import install
        install(settings.data / "models")
    elif arguments.command == "reranker-install":
        from .rerank import install
        install(settings.data / "models")
    elif arguments.command == "benchmark":
        from .domain import RunOptions
        from .storage import Store
        from .worker import work
        source = arguments.source.resolve(strict=True)
        if arguments.timeout is not None and (not 0 < arguments.timeout <= 24 * 3600):
            parser.error("Benchmark timeout must be positive and no greater than 24 hours.")
        if arguments.output:
            arguments.output = arguments.output.resolve()
            if arguments.output.exists():
                parser.error("The report output already exists; choose a new filename.")
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
        options = RunOptions(mode="benchmark", device=arguments.device, sample_seconds=arguments.seconds,
                             captions=not arguments.no_captions, profile=arguments.profile, encoder=arguments.encoder,
                             analysis_decoder=arguments.analysis_decoder)
        store = Store(settings.data / "jobs.sqlite3")
        heartbeat = settings.data / "worker.json"
        if heartbeat.is_file():
            try:
                value = json.loads(heartbeat.read_text(encoding="utf-8"))
                if time.time() - float(value["updated"]) < 10:
                    live = True
                    if value.get("pid"):
                        import psutil
                        try:
                            process = psutil.Process(int(value["pid"]))
                            live = process.is_running() and process.create_time() <= float(value["updated"])
                        except psutil.NoSuchProcess:
                            live = False
                        except psutil.AccessDenied:
                            live = True
                    if live:
                        parser.error("Stop the web launcher before a CLI benchmark; its worker is still active.")
            except (OSError, ValueError, KeyError, TypeError):
                parser.error("Cannot verify worker status. Stop the launcher and check worker.json before benchmarking.")
        with store.connect() as database:
            if database.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','paused') LIMIT 1").fetchone():
                parser.error("Finish or cancel existing queued, running or paused jobs before a CLI benchmark.")
        job = store.create(source.name, "file", str(source), options.model_dump())
        work(settings, once=True, job_id=job["id"], prevent_sleep=arguments.keep_awake, job_timeout=arguments.timeout)
        result = store.get(job["id"])
        if arguments.output:
            with arguments.output.open("x", encoding="utf-8") as output:
                json.dump({"job_id": job["id"], "status": result["status"], "message": result["message"], **result["result"]}, output, indent=2)
        print(json.dumps(result, indent=2))
        if result["status"] != "succeeded":
            sys.exit(1)
    elif arguments.command == "serve":
        import uvicorn
        from .api import create_app
        if not 8765 <= arguments.port <= 8775:
            parser.error("Choose a loopback port between 8765 and 8775.")
        port = arguments.port
        for candidate in range(port, 8776):
            with socket.socket() as check:
                try:
                    check.bind(("127.0.0.1", candidate))
                    port = candidate
                    break
                except OSError:
                    continue
        else:
            parser.error("No available Clipper loopback port.")
        worker = subprocess.Popen([sys.executable, "-m", "clipper.worker"], env=os.environ.copy())
        print(f"Clipper: http://127.0.0.1:{port}", flush=True)
        try:
            uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
        finally:
            if worker.poll() is None:
                if os.name == "nt":
                    from .media import terminate
                    terminate(worker)
                else:
                    worker.terminate()
                    worker.wait(timeout=15)


if __name__ == "__main__":
    main()