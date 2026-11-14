# Optional Windows GPU Setup

CPU is the supported fallback and does not require CUDA. Auto independently tests speech, visual analysis and encoding; GPU telemetry or an encoder name alone is not proof that a path works.

## Speech

The selected CTranslate2/faster-whisper versions require compatible CUDA12 cuBLAS and cuDNN9 libraries for NVIDIA speech inference. Install vendor-supported packages manually for the target system. Clipper discovers standard CUDA12/cuDNN9 Windows DLL directories and registers process-local paths; it does not alter system PATH, install drivers or request elevation.

Explicit local overrides CLIPPER_CUDA_BIN/CLIPPER_CUDNN_BIN may point to the directories containing cublas64_12.dll/cudnn64_9.dll. Do not select a CUDA13-only cuDNN directory for this runtime. Use doctor to check loadability, then a short benchmark to prove actual execution.

## Video

FFmpeg's pinned build supports CPU fallback and NVIDIA NVDEC/NVENC. Auto tries configured AV1/H.264/HEVC analysis decoders and validates feature coverage; it probes an actual portrait encode before NVENC selection. Forced modes fail rather than silently changing paths. Driver support varies by machine; Clipper does not update drivers.

Final composition/subtitles and the selected Qwen runtime still run on CPU. Shared GPU memory is not extra dedicated VRAM. Keep stages serial and use the normal OEM power/cooling configuration. Sampled telemetry cannot certify safe temperatures, lifespan or sustained capacity.

See [testing-other-windows.md](testing-other-windows.md) for source-matched CPU/Auto benchmarks. Report actual paths and fallback warnings using reviewed diagnostics, not raw private logs.