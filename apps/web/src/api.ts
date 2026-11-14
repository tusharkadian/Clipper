export interface Options {
  mode: "clips" | "benchmark";
  profile: "quiet" | "balanced";
  device: "auto" | "cpu" | "cuda";
  captions: boolean;
  max_clips: number;
  clip_seconds: number;
  threshold: number;
  sample_seconds: number;
  zoom: number;
  ai_rerank: boolean;
  encoder: "auto" | "cpu" | "nvenc";
  analysis_decoder: "auto" | "cpu" | "nvdec";
}

export interface Export {
  id: string;
  start: number;
  end: number;
  score: number;
  reason: string;
  heuristic_score?: number | null;
  semantic_score?: number | null;
  scene_adjusted?: boolean;
  encoding?: { requested: string; actual: string; fallback_reason: string | null; render_seconds: number };
}

export interface Job {
  id: string;
  title: string;
  kind: "file" | "url" | "model";
  source: string;
  status: "queued" | "running" | "paused" | "cancelled" | "failed" | "succeeded";
  stage: string;
  progress: number;
  message: string;
  created: number;
  request: string | null;
  options: Options;
  result: {
    exports?: Export[];
    warnings?: string[];
    elapsed_seconds?: number;
    benchmark?: boolean;
    speech_device?: string;
    peak_gpu_temperature_c?: number | null;
    stages?: Record<string, number>;
    transcript?: {
      version: number; status: "usable" | "unreliable" | "unknown" | "no_speech" | "not_run";
      reasons?: string[]; mean_word_probability?: number | null; measured_segments?: number;
      language?: { requested: string; detected: string | null; verified: boolean; model_english_only: boolean };
    };
    ranking?: { requested: boolean; method: string; status: string; assessed?: number; shortlisted?: number };
    encoding?: { requested: string; actual_encoders: string[]; decode: string; filters: string };
    analysis?: {
      version: number; requested: string; video_decode: string; video_scale: string; features: string;
      cached: boolean; fallback_reason: string | null; timings: { audio_seconds: number; visual_seconds: number };
      scene_detection?: { method: string; sample_fps: number; threshold: number; cuts: number };
    };
    selection?: { version: number; maximum_clips: number; exported: number; scene_snap_seconds: number;
      maximum_shared_seconds: number; dialogue_minimum_tokens: number; dialogue_similarity: number; scene_adjusted_exports: number };
  };
}

export interface System {
  ready: boolean;
  worker_ready: boolean;
  cuda_runtime_ready: boolean;
  model_ready: boolean;
  reranker_ready: boolean;
  reranker_model: string;
  speech_installed: boolean;
  ram_gb: number | null;
  free_disk_gb: number;
  workspace_gb: number;
  workspace_limit_gb: number;
  warnings: string[];
  gpus: { name: string; dedicated_mb: number; free_mb: number; temperature_c: number | null }[];
  tools: Record<string, { available: boolean; subtitles?: boolean }>;
}

let token = "";

export async function connect() {
  const response = await fetch("/api/session", { credentials: "same-origin" });
  if (!response.ok) throw new Error("Local API is unavailable.");
  token = (await response.json()).token;
}

export async function request<Result>(path: string, method = "GET", body?: unknown): Promise<Result> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "x-clipper-token": token, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Request rejected. Check the source and settings.");
  return result;
}

export function upload(file: File, options: Options, progress: (value: number) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const data = new FormData();
    data.append("file", file);
    data.append("options", JSON.stringify(options));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.setRequestHeader("x-clipper-token", token);
    xhr.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
    xhr.onerror = () => reject(new Error("Recording upload failed."));
    xhr.onload = () => {
      try {
        const result = JSON.parse(xhr.responseText);
        if (xhr.status >= 400) reject(new Error(result.detail || "Upload rejected."));
        else resolve(result);
      } catch { reject(new Error("The local API returned an invalid response.")); }
    };
    xhr.send(data);
  });
}

export function artifact(job: string, clip: string, name: string) {
  return `/api/jobs/${encodeURIComponent(job)}/files/${encodeURIComponent(clip)}/${encodeURIComponent(name)}`;
}

export async function report(job: Job) {
  const result = await request(`/jobs/${job.id}/report`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${job.id}-report.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}