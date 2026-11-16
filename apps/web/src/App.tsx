import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Activity, ArrowDownToLine, ArrowUpRight, Check, ChevronDown, CircleAlert, Clapperboard, Cpu, Crop, Download, FileVideo, FlaskConical, Gauge, HardDrive, Leaf, Link, LoaderCircle, LockKeyhole, Pause, Play, Plus, RefreshCw, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { WebcamEditor } from "./WebcamEditor";
import type { ScreenshotReference } from "./WebcamEditor";
import { artifact, connect, report, request, upload } from "./api";
import type { Export, Job, Options, System } from "./api";

const defaults: Options = { mode: "clips", profile: "quiet", device: "auto", captions: true, max_clips: 5, clip_seconds: 40, threshold: 0.55, sample_seconds: 1800, zoom: 1.5, ai_rerank: true, encoder: "auto", analysis_decoder: "auto" };
const clock = (value: number) => `${Math.floor(value / 60)}:${Math.floor(value % 60).toString().padStart(2, "0")}`;
const transcriptStatuses: Record<string, string> = { usable: "Transcript checked", unreliable: "Transcript needs review", unknown: "Transcript unverified", no_speech: "No recognized speech" };
const stages: Record<string, string> = { "install-model": "Installing model", "sample-audio": "Preparing sample", "benchmark-render": "Rendering test exports", fingerprint: "Checking source", download: "Downloading", probe: "Inspecting media", audio: "Preparing audio", transcribe: "Transcribing", analyze: "Analyzing activity", rerank: "AI reranking", render: "Rendering clips", complete: "Complete", queued: "Queued" };

function IconButton({ label, children, onClick, disabled = false }: { label: string; children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return <button className="icon-button" type="button" title={label} aria-label={label} onClick={onClick} disabled={disabled}>{children}</button>;
}

export function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [system, setSystem] = useState<System | null>(null);
  const [options, setOptions] = useState<Options>(defaults);
  const [sourceTab, setSourceTab] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{ job: Job; clip: Export } | null>(null);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [sourcePreview, setSourcePreview] = useState<string | null>(null);
  const [layoutReference, setLayoutReference] = useState<ScreenshotReference | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    let ticks = 0;
    async function refresh() {
      try {
        const nextJobs = await request<Job[]>("/jobs");
        if (active) setJobs(nextJobs);
        if (ticks++ % 8 === 0) {
          const nextSystem = await request<System>("/system");
          if (active) setSystem(nextSystem);
        }
      } catch (issue) { if (active) setError((issue as Error).message); }
    }
    const ready = connect().then(() => { if (active) { setConnected(true); return refresh(); } }).catch(issue => { if (active) setError(issue.message); });
    const interval = window.setInterval(() => { void ready.then(() => { if (active) return refresh(); }); }, 2000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  useEffect(() => { if (preview && dialog.current && !dialog.current.open) dialog.current.showModal(); }, [preview]);
  useEffect(() => {
    if (!file || sourceTab !== "file") { setSourcePreview(null); return; }
    const object = URL.createObjectURL(file);
    setSourcePreview(object);
    return () => URL.revokeObjectURL(object);
  }, [file, sourceTab]);

  function resetWebcam() { setOptions(current => ({ ...current, webcam_layout: null })); setLayoutReference(null); }

  async function perform(action: () => Promise<unknown>) {
    setError("");
    try { await action(); setJobs(await request<Job[]>("/jobs")); }
    catch (issue) { setError((issue as Error).message); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setUploadProgress(0);
    const processingOptions: Options = { ...options, device: "auto", encoder: "auto", analysis_decoder: "auto" };
    await perform(async () => {
      if (sourceTab === "file") {
        if (!file) throw new Error("Choose a recording first.");
        await upload(file, processingOptions, setUploadProgress);
        setFile(null);
        if (fileInput.current) fileInput.current.value = "";
      } else {
        await request("/sources", "POST", { url, options: processingOptions });
        setUrl("");
      }
      resetWebcam();
    });
    setBusy(false);
  }

  async function remove(job: Job) {
    if (window.confirm(`Remove ${job.title}, its imported copy and all generated exports? Your original file is not changed.`)) {
      await perform(async () => {
        await request(`/jobs/${job.id}`, "DELETE");
        setSystem(await request<System>("/system"));
      });
    }
  }

  const exported = jobs.flatMap(job => (job.result.exports || []).map(clip => ({ job, clip })));
  const highlights = exported.filter(item => !item.job.result.benchmark);
  const samples = exported.filter(item => item.job.result.benchmark);
  const activeJobs = jobs.filter(job => ["queued", "running", "paused"].includes(job.status));
  const gpu = system?.gpus[0];
  const installPending = jobs.some(job => job.kind === "model" && ["queued", "running", "paused"].includes(job.status));
  const rerankerPending = jobs.some(job => job.kind === "model" && job.source === "qwen-reranker" && ["queued", "running", "paused"].includes(job.status));

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="/"><span className="brand-mark"><Clapperboard size={23} /></span>clipper<span className="brand-label">LOCAL STUDIO</span></a>
      <span className="privacy"><LockKeyhole size={13} />On-device processing<span className="status-dot" /></span>
    </header>

    <main>
      <div className="page-heading"><div><div className="eyebrow">WORKSPACE / 01</div><h1>Your next moment.</h1></div><div className="session-status"><span className={`status-dot ${system?.worker_ready ? "" : "offline"}`} />{system?.worker_ready ? "Local worker connected" : connected ? "Local API connected" : "Connecting"}</div></div>
      {error && <div role="alert" className="alert"><CircleAlert size={18} /><span>{error}</span><IconButton label="Dismiss error" onClick={() => setError("")}><X size={16} /></IconButton></div>}

      <section className="hardware-band" aria-label="System status">
        <div className="hardware-item"><Cpu /><div><span className="metric-label">COMPUTE</span><strong>{gpu ? gpu.name.replace("NVIDIA ", "").replace(" Generation Laptop GPU", "") : system ? "CPU" : "Checking"}</strong><small>{gpu ? `${(gpu.dedicated_mb / 1024).toFixed(1)} GB dedicated VRAM` : "GPU telemetry unavailable"}</small></div></div>
        <div className="hardware-item"><Activity /><div><span className="metric-label">TEMPERATURE</span><strong>{gpu?.temperature_c != null ? `${gpu.temperature_c} C` : "Unavailable"}</strong><small>CPU temperature unavailable</small></div></div>
        <div className="hardware-item"><HardDrive /><div><span className="metric-label">WORKSPACE</span><strong>{system ? `${system.workspace_gb} / ${system.workspace_limit_gb} GB` : "Checking"}</strong><small>{system ? `${system.free_disk_gb.toFixed(0)} GB free on drive` : "Storage preflight"}</small></div></div>
        <div className="hardware-item"><LockKeyhole /><div><span className="metric-label">CLOUD</span><strong>Disabled</strong><small>No inference API charges</small></div></div>
      </section>

      {system && (!system.model_ready || !system.ready || (gpu && !system.cuda_runtime_ready)) && <section className="setup-band" aria-label="Setup status">
        <span className="setup-icon"><CircleAlert size={19} /></span><div><strong>{!system.ready ? "Media prerequisites incomplete" : !system.model_ready ? "English speech model not installed" : "GPU transcription unavailable"}</strong><span>{!system.ready ? "FFmpeg, FFprobe, subtitles and disk headroom required" : !system.model_ready ? "small.en / local model weights" : "CUDA 12 / cuDNN 9 required. CPU INT8 enabled."}</span></div>
        {!system.model_ready && <button className="secondary" disabled={installPending || !connected || !system.speech_installed} onClick={() => void perform(() => request("/model", "POST"))}>{installPending ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}{installPending ? "Installation queued" : "Install English model"}</button>}
      </section>}

      {system && !system.reranker_ready && <section className="setup-band" aria-label="AI reranker setup"><span className="setup-icon"><Cpu size={19} /></span><div><strong>Local AI reranker not installed</strong><span>Qwen2.5 1.5B / Q4 / 1.04 GiB / Apache 2.0</span></div><button className="secondary" disabled={rerankerPending || !connected} onClick={() => void perform(() => request("/reranker/model", "POST"))}>{rerankerPending ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}{rerankerPending ? "Installation queued" : "Install local reranker"}</button></section>}

      <div className="work-grid">
        <section className="source-section" aria-labelledby="source-heading">
          <div className="section-heading"><h2 id="source-heading">New recording</h2><span className="section-index">01</span></div>
          <form onSubmit={submit}>
            <div className="segmented" aria-label="Processing mode"><button type="button" className={options.mode === "clips" ? "selected" : ""} onClick={() => setOptions({ ...options, mode: "clips" })}><Clapperboard size={15} />Extract clips</button><button type="button" className={options.mode === "benchmark" ? "selected" : ""} onClick={() => setOptions({ ...options, mode: "benchmark" })}><FlaskConical size={15} />Benchmark</button></div>
            <div className="source-tabs"><button type="button" className={sourceTab === "file" ? "active" : ""} onClick={() => { if (sourceTab !== "file") resetWebcam(); setSourceTab("file"); }}><FileVideo size={15} />Local file</button><button type="button" className={sourceTab === "url" ? "active" : ""} onClick={() => { if (sourceTab !== "url") resetWebcam(); setSourceTab("url"); }}><Link size={15} />Video URL</button></div>
            {sourceTab === "file" ? <div className="file-picker"><input ref={fileInput} id="video-file" aria-label="Recording file" type="file" accept=".mp4,.mkv,.mov,.webm,.avi,.m4v,.ts" onChange={event => { setFile(event.target.files?.[0] || null); resetWebcam(); }} /><label htmlFor="video-file"><Upload size={27} /><strong>{file ? file.name : "Choose recording"}</strong><span>{file ? `${(file.size / 1024 ** 2).toFixed(1)} MB` : "MP4, MKV, MOV, WebM"}</span></label></div> : <div className="url-field"><label htmlFor="source-url">YouTube video or Twitch VOD</label><input id="source-url" type="url" placeholder="https://" value={url} onChange={event => { setUrl(event.target.value); resetWebcam(); }} required /></div>}
            <button type="button" className="secondary configure-layout" onClick={() => setLayoutOpen(true)}><Crop size={16} />Configure layout</button>
            <fieldset><legend>Processing profile</legend><div className="segmented"><button type="button" className={options.profile === "quiet" ? "selected" : ""} onClick={() => setOptions({ ...options, profile: "quiet" })}><Leaf size={15} />Quiet</button><button type="button" className={options.profile === "balanced" ? "selected" : ""} onClick={() => setOptions({ ...options, profile: "balanced" })}><Gauge size={15} />Balanced</button></div></fieldset>
            <label className="duration-field">{options.mode === "clips" ? "Clip length" : "Sample length"}{options.mode === "clips" ? <select aria-label="Clip length" value={options.clip_seconds} onChange={event => setOptions({ ...options, clip_seconds: Number(event.target.value) })}>{[20, 30, 40, 60, 90].map(value => <option key={value} value={value}>{value} seconds</option>)}</select> : <select aria-label="Sample length" value={options.sample_seconds} onChange={event => setOptions({ ...options, sample_seconds: Number(event.target.value) })}>{[60, 300, 1800].map(value => <option key={value} value={value}>{value / 60} minutes</option>)}</select>}</label>
            <label className="toggle-row" htmlFor="captions"><span>English captions</span><input id="captions" type="checkbox" checked={options.captions} onChange={event => setOptions({ ...options, captions: event.target.checked })} /><span className="toggle-track" aria-hidden="true" /></label>
            {options.mode === "clips" && <label className="toggle-row" htmlFor="ai-rerank"><span>Local AI reranking</span><input id="ai-rerank" type="checkbox" checked={options.ai_rerank} onChange={event => setOptions({ ...options, ai_rerank: event.target.checked })} /><span className="toggle-track" aria-hidden="true" /></label>}
            {options.mode === "clips" && <details><summary>Selection settings<ChevronDown size={15} /></summary><div className="advanced"><label>Maximum clips<input aria-label="Maximum clips" type="number" min="1" max="20" value={options.max_clips} onChange={event => setOptions({ ...options, max_clips: Number(event.target.value) })} /></label><label>Activity threshold <output>{options.threshold.toFixed(2)}</output><input aria-label="Activity threshold" type="range" min="0.1" max="1" step="0.05" value={options.threshold} onChange={event => setOptions({ ...options, threshold: Number(event.target.value) })} /></label></div></details>}
            <button type="submit" className="primary submit" disabled={busy || !connected || (sourceTab === "file" ? !file : !url)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Plus size={18} />}{busy ? `Uploading ${uploadProgress}%` : options.mode === "benchmark" ? "Queue benchmark" : "Queue recording"}<ArrowUpRight size={17} /></button>
          </form>
        </section>

        <section className="queue-section" aria-labelledby="queue-heading">
          <div className="section-heading"><div className="heading-with-count"><h2 id="queue-heading">Processing queue</h2><span className="count">{activeJobs.length}</span></div><IconButton label="Refresh jobs" onClick={() => void perform(async () => setJobs(await request<Job[]>("/jobs")))}><RefreshCw size={16} /></IconButton></div>
          <div className="queue-labels"><span>RECORDING</span><span>STATUS</span></div>
          {jobs.length === 0 ? <div className="empty-queue"><span className="empty-mark"><Clapperboard size={35} strokeWidth={1} /></span><h3>No recordings queued</h3><span>Queue empty / 00</span></div> : <div className="job-list">{jobs.map(job => <article className="job-row" key={job.id} data-job-id={job.id}>
            <div className={`job-icon ${job.status}`} title={job.options.mode === "benchmark" ? "Benchmark" : "Recording"}>{job.kind === "model" ? <Download size={19} /> : job.options.mode === "benchmark" ? <FlaskConical size={19} /> : <FileVideo size={19} />}</div>
            <div className="job-content"><div className="job-title" title={job.title}>{job.title}</div>
              <div className="job-meta"><span>{job.options.profile}</span><span>{job.result.elapsed_seconds != null ? `${Math.round(job.result.elapsed_seconds)}s elapsed` : new Date(job.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>{job.result.exports && <span>{job.result.exports.length} exports</span>}{job.result.ranking && <span>{job.result.ranking.status === "applied" ? "AI reranked" : "Activity ranked"}</span>}{job.result.encoding && <span>{job.result.encoding.actual_encoders.includes("h264_nvenc") ? "NVENC encoded" : "CPU encoded"}</span>}{job.result.analysis && <span>{job.result.analysis.video_decode === "nvdec" ? "NVDEC analysis" : "CPU analysis"}{job.result.analysis.cached ? " / cached" : ""}</span>}{job.result.transcript && transcriptStatuses[job.result.transcript.status] && <span aria-label="Transcript quality">{transcriptStatuses[job.result.transcript.status]}</span>}</div>
              <div className="progress-track"><div style={{ width: `${job.status === "succeeded" ? 100 : Math.round(job.progress * 100)}%` }} /></div>{job.message && <p className="job-message">{job.message}</p>}{job.result.warnings?.map((warning, index) => <p className="job-message warning" key={index}>{warning}</p>)}
            </div>
            <div className="job-state"><span className={`status ${job.status}`}>{job.status === "running" ? <LoaderCircle size={12} className="spin" /> : job.status === "succeeded" ? <Check size={12} /> : null}{job.request ? `${job.request} requested` : job.status === "running" ? stages[job.stage] || job.stage : job.status === "succeeded" ? "Complete" : job.status}</span><div className="job-actions">{["running", "queued"].includes(job.status) && <IconButton label={`Pause ${job.title}`} disabled={!!job.request} onClick={() => void perform(() => request(`/jobs/${job.id}/pause`, "POST"))}><Pause size={15} /></IconButton>}{job.status === "paused" && <IconButton label={`Resume ${job.title}`} onClick={() => void perform(() => request(`/jobs/${job.id}/resume`, "POST"))}><Play size={15} /></IconButton>}{["failed", "cancelled"].includes(job.status) && <IconButton label={`Retry ${job.title}`} onClick={() => void perform(() => request(`/jobs/${job.id}/retry`, "POST"))}><RotateCcw size={15} /></IconButton>}{["running", "queued", "paused"].includes(job.status) && <IconButton label={`Cancel ${job.title}`} disabled={!!job.request} onClick={() => void perform(() => request(`/jobs/${job.id}/cancel`, "POST"))}><X size={15} /></IconButton>}{job.status === "succeeded" && <IconButton label={`Download report for ${job.title}`} onClick={() => void perform(() => report(job))}><ArrowDownToLine size={15} /></IconButton>}{["succeeded", "failed", "cancelled"].includes(job.status) && <IconButton label={`Remove ${job.title}`} onClick={() => void remove(job)}><Trash2 size={15} /></IconButton>}</div></div>
          </article>)}</div>}
          <div className="queue-footer"><span className="status-dot" />Single-stage processing<span>{system?.reranker_ready ? "Qwen 1.5B Q4 / CPU" : "Activity ranker / v1"}</span></div>
        </section>
      </div>

      <section className="exports-section" aria-labelledby="exports-heading"><div className="section-heading"><div className="heading-with-count"><h2 id="exports-heading">Clip exports</h2><span className="count">{highlights.length}</span></div><span className="format-label">9:16 / 1080p / MP4</span></div>{highlights.length === 0 ? <div className="empty-exports"><Clapperboard size={20} /><span>No highlight exports yet</span></div> : <div className="export-grid">{highlights.map(({ job, clip }) => <ExportItem key={job.id + clip.id} job={job} clip={clip} open={() => setPreview({ job, clip })} />)}</div>}</section>
      {samples.length > 0 && <section className="exports-section" aria-label="Benchmark test exports"><div className="section-heading"><h2>Benchmark test exports</h2><span className="format-label">FIXED INTERVALS</span></div><div className="export-grid">{samples.map(({ job, clip }) => <ExportItem key={job.id + clip.id} job={job} clip={clip} open={() => setPreview({ job, clip })} />)}</div></section>}
      <footer className="footer"><span>CLIPPER / LOCAL STUDIO</span><span>English<span className="footer-dot" />Cloud disabled<span className="footer-dot" />{system?.ready ? "Media tools ready" : "Checking media tools"}</span></footer>
    </main>
    {preview && <dialog ref={dialog} onClose={() => setPreview(null)} className="preview-dialog"><div className="preview-title"><strong>{preview.job.title}</strong><IconButton label="Close preview" onClick={() => dialog.current?.close()}><X size={19} /></IconButton></div><video controls autoPlay playsInline src={artifact(preview.job.id, preview.clip.id, "clip.mp4")} /><div className="preview-details"><span>{clock(preview.clip.start)} - {clock(preview.clip.end)}</span><a className="secondary" href={artifact(preview.job.id, preview.clip.id, "clip.mp4")} download><Download size={16} />Export MP4</a></div></dialog>}
    {layoutOpen && <WebcamEditor source={sourcePreview} initial={options.webcam_layout || null} reference={layoutReference} zoom={options.zoom} close={() => setLayoutOpen(false)} save={(layout, reference, zoom) => { setOptions(current => ({ ...current, zoom, webcam_layout: layout })); setLayoutReference(reference); setLayoutOpen(false); }} />}
  </div>;
}

function ExportItem({ job, clip, open }: { job: Job; clip: Export; open: () => void }) {
  return <article className="export-item" data-job-id={job.id}><button className="export-preview" onClick={open} aria-label={`Preview ${job.title} ${clip.id}`}><img src={artifact(job.id, clip.id, "thumbnail.jpg")} alt={`${job.title} clip frame`} loading="lazy" /><span className="play-mark"><Play size={22} fill="currentColor" /></span><span className="duration">{clock(clip.end - clip.start)}</span></button><div className="export-info"><h3 title={job.title}>{job.title}</h3><span>{clock(clip.start)} - {clock(clip.end)}</span><p>{clip.reason}</p><div className="export-actions"><a title="Download MP4" aria-label="Download MP4" href={artifact(job.id, clip.id, "clip.mp4")} download><Download size={16} /></a><a href={artifact(job.id, clip.id, "captions.srt")} download>SRT</a><a href={artifact(job.id, clip.id, "plan.json")} download>JSON</a></div></div></article>;
}