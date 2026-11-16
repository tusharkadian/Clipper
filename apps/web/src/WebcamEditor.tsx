import { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { Check, ImagePlus, Webcam, X } from "lucide-react";
import type { SourceRegion, WebcamLayout } from "./api";

export const defaultWebcamLayout: WebcamLayout = {
  webcam: { x: 0.78, y: 0, width: 0.22, height: 0.26 },
  gameplay: { x: 0, y: 0, width: 1, height: 1 }, top_fraction: 0.3,
  webcam_zoom: 1, gameplay_fit: "fit",
};

export interface ScreenshotReference { file: File; frame: SourceRegion }
const fullFrame: SourceRegion = { x: 0, y: 0, width: 1, height: 1 };

function valid(region: SourceRegion) {
  return Object.values(region).every(Number.isFinite) && region.x >= 0 && region.y >= 0 &&
    region.width >= 0.02 && region.height >= 0.02 && region.x + region.width <= 1.00000001 && region.y + region.height <= 1.00000001;
}

export function WebcamEditor({ source, initial, zoom, reference, save, close }: {
  source: string | null; initial: WebcamLayout | null; zoom: number;
  reference: ScreenshotReference | null;
  save: (layout: WebcamLayout | null, reference: ScreenshotReference | null, zoom: number) => void; close: () => void;
}) {
  const [draft, setDraft] = useState<WebcamLayout>(() => ({ ...defaultWebcamLayout, ...structuredClone(initial || {}), webcam_zoom: 1, gameplay_fit: "fit" }));
  const [webcamEnabled, setWebcamEnabled] = useState(!!initial);
  const [portraitZoom, setPortraitZoom] = useState(zoom);
  const [mode, setMode] = useState<"webcam" | "gameplay" | "frame">("webcam");
  const [ratio, setRatio] = useState(16 / 9);
  const [screenshot, setScreenshot] = useState<File | null>(reference?.file || null);
  const [screenshotFrame, setScreenshotFrame] = useState<SourceRegion>(reference?.frame || fullFrame);
  const [confirmed, setConfirmed] = useState(!!reference);
  const [screenshotURL, setScreenshotURL] = useState<string | null>(null);
  const [imageError, setImageError] = useState("");
  const [imageReady, setImageReady] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const cropError = webcamEnabled && (!valid(draft.webcam) || !valid(draft.gameplay)) ? "Regions must fit inside the source frame." : "";
  const error = imageError || (screenshot && !imageReady ? "Loading screenshot." : "") ||
    (mode === "frame" ? (valid(screenshotFrame) ? "" : "Select a video frame inside the screenshot.") :
      cropError || (webcamEnabled && screenshot && !confirmed ? "Select the webcam region on the screenshot." : ""));
  const visibleFrame = screenshotURL && mode !== "frame" ? screenshotFrame : fullFrame;

  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    if (!screenshot) { setScreenshotURL(null); return; }
    const object = URL.createObjectURL(screenshot);
    setScreenshotURL(object);
    return () => URL.revokeObjectURL(object);
  }, [screenshot]);

  useEffect(() => {
    if (!source && !screenshotURL) return;
    let frame = 0;
    let lastTime = -1;
    const paint = () => {
      const player = video.current, picture = image.current;
      const media = screenshotURL ? picture : player;
      const sourceWidth = screenshotURL ? picture?.naturalWidth : player?.videoWidth;
      const sourceHeight = screenshotURL ? picture?.naturalHeight : player?.videoHeight;
      const position = screenshotURL ? 0 : player?.currentTime;
      const output = canvas.current, context = output?.getContext("2d");
      if (media && sourceWidth && sourceHeight && output && context && !imageError && !cropError && valid(screenshotFrame) && position !== lastTime &&
          (screenshotURL ? imageReady : player && player.readyState >= 2)) {
        lastTime = position ?? 0;
        context.clearRect(0, 0, output.width, output.height);
        const top = Math.round(output.height * draft.top_fraction);
        const panel = (region: SourceRegion, y: number, height: number, scale: number, alignment: "top" | "bottom" | "center") => {
          const frame = screenshotURL ? screenshotFrame : fullFrame;
          const cropWidth = sourceWidth * frame.width * region.width, cropHeight = sourceHeight * frame.height * region.height;
          const left = sourceWidth * (frame.x + region.x * frame.width), upper = sourceHeight * (frame.y + region.y * frame.height);
          context.save(); context.beginPath(); context.rect(0, y, output.width, height); context.clip();
          const cover = Math.max(output.width / cropWidth, height / cropHeight);
          context.filter = "blur(8px)";
          context.drawImage(media, left, upper, cropWidth, cropHeight,
            (output.width - cropWidth * cover) / 2, y + (height - cropHeight * cover) / 2, cropWidth * cover, cropHeight * cover);
          const fit = Math.min(output.width / cropWidth, height / cropHeight) * scale;
          const foregroundHeight = cropHeight * fit;
          const upperOffset = foregroundHeight > height || alignment === "center" ? (height - foregroundHeight) / 2 : alignment === "bottom" ? height - foregroundHeight : 0;
          context.filter = "none";
          context.drawImage(media, left, upper, cropWidth, cropHeight,
            (output.width - cropWidth * fit) / 2, y + upperOffset, cropWidth * fit, foregroundHeight);
          context.restore();
        };
        if (webcamEnabled) {
          panel(draft.webcam, 0, top, 1, "bottom");
          panel(draft.gameplay, top, output.height - top, portraitZoom, "top");
        } else {
          panel(fullFrame, 0, output.height, portraitZoom, "center");
        }
      }
      if (!screenshotURL) frame = requestAnimationFrame(paint);
    };
    frame = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(frame);
  }, [source, screenshotURL, imageReady, screenshotFrame, draft, portraitZoom, webcamEnabled, cropError, imageError]);

  function chooseScreenshot(file: File | undefined) {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setImageError("Use a PNG, JPEG or WebP screenshot up to 10 MB.");
      return;
    }
    setImageError(""); setImageReady(false); setConfirmed(false); setScreenshotFrame(fullFrame);
    setMode("webcam"); setScreenshot(file);
  }

  function update(region: SourceRegion) {
    const rounded = { x: Math.round(region.x * 10000) / 10000, y: Math.round(region.y * 10000) / 10000,
      width: Math.round(region.width * 10000) / 10000, height: Math.round(region.height * 10000) / 10000 };
    if (mode === "frame") { setScreenshotFrame(rounded); setConfirmed(false); return; }
    if (mode === "webcam") setConfirmed(true);
    setDraft(current => ({ ...current, [mode]: rounded }));
  }

  function point(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.parentElement!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
             y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
  }

  function drag(event: PointerEvent<HTMLDivElement>) {
    if (!anchor.current) return;
    const end = point(event), start = anchor.current;
    const width = Math.max(0.02, Math.abs(end.x - start.x)), height = Math.max(0.02, Math.abs(end.y - start.y));
    update({ x: Math.min(Math.min(start.x, end.x), 1 - width), y: Math.min(Math.min(start.y, end.y), 1 - height), width, height });
  }

  const region = mode === "frame" ? screenshotFrame : draft[mode];
  return <dialog ref={dialog} className="layout-dialog" onClose={close} aria-labelledby="layout-title">
    <form onSubmit={event => {
      event.preventDefault();
      if (error) return;
      if (mode === "frame") { setMode("webcam"); return; }
      save(webcamEnabled ? draft : null, screenshot ? { file: screenshot, frame: screenshotFrame } : null, portraitZoom);
    }}>
      <div className="preview-title"><h2 id="layout-title"><Webcam size={18} />Portrait layout</h2>
        <button type="button" className="icon-button" title="Close layout editor" aria-label="Close layout editor" onClick={close}><X size={18} /></button></div>
      <label className="toggle-row" htmlFor="layout-webcam"><span>Webcam in source</span><input id="layout-webcam" type="checkbox" checked={webcamEnabled} onChange={event => { setWebcamEnabled(event.target.checked); if (event.target.checked) setPortraitZoom(1); setMode("webcam"); }} /><span className="toggle-track" aria-hidden="true" /></label>
      <label className="secondary screenshot-picker"><ImagePlus size={16} />{screenshot ? "Replace source screenshot" : "Upload source screenshot"}<input type="file" aria-label="Source screenshot" accept="image/png,image/jpeg,image/webp" onChange={event => { chooseScreenshot(event.target.files?.[0]); event.target.value = ""; }} /></label>
      {source && screenshot && <button className="secondary screenshot-source" type="button" onClick={() => { setScreenshot(null); setImageReady(false); setImageError(""); setMode("webcam"); }}>Use source video</button>}
      {(webcamEnabled || screenshot) && <div className="segmented" aria-label="Source region">{screenshot && <button type="button" className={mode === "frame" ? "selected" : ""} onClick={() => setMode("frame")}>Video frame</button>}{webcamEnabled && <><button type="button" className={mode === "webcam" ? "selected" : ""} onClick={() => setMode("webcam")}>Webcam region</button><button type="button" className={mode === "gameplay" ? "selected" : ""} onClick={() => setMode("gameplay")}>Gameplay region</button></>}</div>}
      <div className="layout-previews"><div>{source || screenshotURL ? <><div className="layout-label">{screenshotURL ? "Source screenshot" : "Source"}</div>
        <div className={`source-frame ${screenshotURL ? "screenshot-frame" : ""}`} style={{ aspectRatio: ratio * visibleFrame.width / visibleFrame.height }}>
          {screenshotURL ? <img ref={image} src={screenshotURL} alt="Source screenshot for region selection" style={{ position: "absolute", width: `${100 / visibleFrame.width}%`, height: `${100 / visibleFrame.height}%`, left: `${-visibleFrame.x / visibleFrame.width * 100}%`, top: `${-visibleFrame.y / visibleFrame.height * 100}%` }} onLoad={event => {
            const picture = event.currentTarget;
            if (picture.naturalWidth * picture.naturalHeight > 32_000_000) { setImageError("Use a screenshot with at most 32 megapixels."); return; }
            setRatio(picture.naturalWidth / picture.naturalHeight); setImageReady(true);
          }} onError={() => { setImageReady(false); setImageError("The screenshot could not be decoded."); }} /> : <video ref={video} src={source!} controls muted playsInline preload="auto" onLoadedMetadata={event => setRatio(event.currentTarget.videoWidth / event.currentTarget.videoHeight || 16 / 9)} />}
          {(webcamEnabled || mode === "frame") && <div className="crop-interaction" aria-label={`Select ${mode} region`} onPointerDown={event => { event.preventDefault(); anchor.current = point(event); event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={drag} onPointerUp={event => { drag(event); anchor.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={() => { anchor.current = null; }} />}
          {mode === "frame" ? <div className="source-region active" style={{ left: `${screenshotFrame.x * 100}%`, top: `${screenshotFrame.y * 100}%`, width: `${screenshotFrame.width * 100}%`, height: `${screenshotFrame.height * 100}%` }}><span>Video frame</span></div> : webcamEnabled && (["webcam", "gameplay"] as const).map(name => <div key={name} className={`source-region ${name} ${mode === name ? "active" : ""}`} style={{ left: `${draft[name].x * 100}%`, top: `${draft[name].y * 100}%`, width: `${draft[name].width * 100}%`, height: `${draft[name].height * 100}%` }}><span>{name === "webcam" ? "Webcam" : "Gameplay"}</span></div>)}
        </div></> : <div className="layout-empty"><Webcam size={25} /><span>Source preview unavailable</span></div>}
      <div className="layout-controls">
        <label className="zoom-field portrait-zoom" htmlFor="portrait-zoom">Portrait zoom <output htmlFor="portrait-zoom">{portraitZoom.toFixed(2)}x</output><input id="portrait-zoom" aria-label="Portrait zoom" type="range" min="1" max="2" step="0.05" value={portraitZoom} onChange={event => setPortraitZoom(Number(event.target.value))} /></label>
        {webcamEnabled && <label className="zoom-field" htmlFor="webcam-share">Webcam panel height <output>{Math.round(draft.top_fraction * 100)}%</output><input id="webcam-share" aria-label="Webcam panel height" type="range" min="20" max="45" step="1" value={Math.round(draft.top_fraction * 100)} onChange={event => setDraft({ ...draft, top_fraction: Number(event.target.value) / 100 })} /></label>}
        {(webcamEnabled || mode === "frame") && <details className="crop-coordinates"><summary>Fine adjustment</summary><div className="region-fields">{(["x", "y", "width", "height"] as const).map(field => <label key={field}>{({ x: "Left", y: "Top", width: "Width", height: "Height" })[field]} %<input type="number" aria-label={`${mode} ${field} percent`} min={field === "x" || field === "y" ? 0 : 2} max="100" step="0.01" value={Number((region[field] * 100).toFixed(2))} onChange={event => update({ ...region, [field]: Number(event.target.value) / 100 })} /></label>)}</div></details>}
      </div></div><div className="portrait-preview"><div className="layout-label">Portrait / 9:16</div><canvas ref={canvas} width={270} height={480} aria-label="Portrait layout preview" /></div></div>
      <div className="layout-feedback">{error && <p className="layout-error" role="alert">{error}</p>}</div>
      <div className="layout-actions"><button type="button" className="secondary" onClick={close}>Cancel</button><button className="primary" disabled={!!error}><Check size={16} />{mode === "frame" ? "Use video frame" : "Apply layout"}</button></div>
    </form>
  </dialog>;
}