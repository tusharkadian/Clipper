import { test, expect } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dataRoot = process.env.CLIPPER_DATA ? resolve(process.env.CLIPPER_DATA) : fileURLToPath(new URL("../../../.data/", import.meta.url));

test("local workbench exposes real setup and source controls without overflow", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "New recording" })).toBeVisible();
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await expect(page.getByLabel("Speech device")).toHaveCount(0);
  await expect(page.getByLabel("Video encoder")).toHaveCount(0);
  await expect(page.getByLabel("Analysis decoder")).toHaveCount(0);
  await expect(page.getByRole("combobox")).toHaveCount(1);
  await expect(page.getByLabel("Local AI reranking")).toBeChecked();
  await page.getByLabel("Local AI reranking").uncheck();
  await expect(page.getByLabel("Local AI reranking")).not.toBeChecked();
  await page.getByLabel("Local AI reranking").check();
  await expect(page.getByLabel("Portrait zoom")).toHaveCount(0);
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const layout = page.getByRole("dialog", { name: "Portrait layout" });
  await expect(layout.getByLabel("Portrait zoom")).toHaveValue("1.5");
  await layout.getByLabel("Portrait zoom").fill("1.25");
  await expect(layout.locator(".portrait-zoom output")).toHaveText("1.25x");
  await layout.getByRole("button", { name: "Apply layout" }).click();
  await page.getByRole("button", { name: "Video URL", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://127.0.0.1/private");
  const urlBounds = await page.getByLabel("YouTube video or Twitch VOD").boundingBox();
  const configureBounds = await page.getByRole("button", { name: "Configure layout", exact: true }).boundingBox();
  const sourceGap = configureBounds!.y - urlBounds!.y - urlBounds!.height;
  expect(sourceGap).toBeGreaterThanOrEqual(8);
  expect(sourceGap).toBeLessThanOrEqual(20);
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.locator(".source-section").screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-compact-source.png`) });
  const [sourceRequest] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith("/api/sources") && request.method() === "POST"),
    page.getByRole("button", { name: "Queue recording", exact: true }).click(),
  ]);
  expect(sourceRequest.postDataJSON().options).toMatchObject({ device: "auto", encoder: "auto", analysis_decoder: "auto" });
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Dismiss error" }).click();
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await expect(page.getByLabel("Sample length")).toBeVisible();
  await expect(page.getByRole("button", { name: "Queue benchmark", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}.png`), fullPage: true });
});

test("uploaded recording produces benchmark exports with working video playback", async ({ page }, testInfo) => {
  const fixture = resolve(dataRoot, "fixtures/generated-speech.mp4");
  test.skip(!existsSync(fixture), "Generated local speech fixture is not installed");
  await page.goto("/");
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await page.getByLabel("Sample length").selectOption("60");
  await page.getByLabel("Recording file").setInputFiles(fixture);
  const [uploadResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/uploads") && response.request().method() === "POST"),
    page.getByRole("button", { name: "Queue benchmark", exact: true }).click(),
  ]);
  const uploaded = await uploadResponse.json();
  expect(uploadResponse.status()).toBe(201);
  expect(uploaded.options.zoom).toBe(1.5);
  expect(uploaded.options.encoder).toBe("auto");
  expect(uploaded.options.analysis_decoder).toBe("auto");
  expect(uploaded.options.device).toBe("auto");
  const job = page.locator(`.job-row[data-job-id="${uploaded.id}"]`);
  await expect(job).toBeVisible();
  await expect(job.locator(".status")).toHaveText("Complete", { timeout: 150000 });
  const encoding = await page.evaluate(async (id) => {
    const token = (await (await fetch("/api/session")).json()).token;
    return (await fetch(`/api/jobs/${id}/report`, { headers: { "x-clipper-token": token } })).json();
  }, uploaded.id);
  expect(encoding.encoding.requested).toBe("auto");
  expect(encoding.encoding.actual_encoders.length).toBeGreaterThan(0);
  expect(encoding.encoding.actual_encoders.every((value: string) => ["libx264", "h264_nvenc"].includes(value))).toBe(true);
  expect(encoding.analysis.requested).toBe("auto");
  expect(encoding.analysis.version).toBe(3);
  expect(encoding.analysis.scene_detection.method).toBe("ffmpeg-scdet");
  expect(encoding.analysis.scene_detection.sample_fps).toBe(1);
  expect(encoding.exports.every((clip: { encoding: { renderer_version: number; captions: { bottom_margin_px: number } } }) =>
    clip.encoding.renderer_version === 10 && clip.encoding.captions.bottom_margin_px === 173)).toBe(true);
  expect(["cpu", "nvdec"]).toContain(encoding.analysis.video_decode);
  expect(encoding.analysis.timings.audio_seconds).toBeGreaterThanOrEqual(0);
  expect(encoding.analysis.timings.visual_seconds).toBeGreaterThan(0);
  expect(encoding.transcript.version).toBe(1);
  expect(encoding.transcript.measured_segments).toBeGreaterThan(0);
  expect(encoding.transcript.language.verified).toBe(false);
  await expect(job.getByLabel("Transcript quality")).toBeVisible();
  const samples = page.getByRole("region", { name: "Benchmark test exports" });
  await expect(samples).toBeVisible();
  await samples.locator(`[data-job-id="${uploaded.id}"]`).getByRole("button", { name: /Preview/ }).first().click();
  const video = page.locator("dialog video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).videoWidth), { timeout: 15000 }).toBeGreaterThan(0);
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).currentTime), { timeout: 15000 }).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Close preview" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-exports.png`), fullPage: true });
  const runDirectory = resolve(dataRoot, "runs", uploaded.id);
  expect(existsSync(runDirectory)).toBe(true);
  expect(existsSync(uploaded.source)).toBe(true);
  page.once("dialog", dialog => dialog.accept());
  const [removed] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith(`/api/jobs/${uploaded.id}`) && response.request().method() === "DELETE"),
    page.waitForResponse(response => response.url().endsWith("/api/system")),
    job.getByRole("button", { name: /^Remove / }).click(),
  ]);
  expect(removed.status()).toBe(200);
  await expect(job).toHaveCount(0);
  expect(existsSync(runDirectory)).toBe(false);
  expect(existsSync(uploaded.source)).toBe(false);
  expect(existsSync(resolve(dataRoot, "reviews", uploaded.id))).toBe(false);
});

test("clip job records applied local AI ranking", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "CPU inference lifecycle is shared across viewports");
  const fixture = resolve(dataRoot, "fixtures/generated-speech.mp4");
  test.skip(!existsSync(fixture), "Generated local speech fixture is not installed");
  const [systemResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/system")),
    page.goto("/"),
  ]);
  const system = await systemResponse.json();
  test.skip(!system.reranker_ready || !system.model_ready, "Verified local models are not installed");
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await page.getByLabel("Clip length").selectOption("20");
  await page.getByText("Selection settings", { exact: true }).click();
  await page.getByRole("slider", { name: "Activity threshold" }).fill("0.1");
  await page.getByLabel("Recording file").setInputFiles(fixture);
  const [response] = await Promise.all([
    page.waitForResponse(result => result.url().endsWith("/api/uploads") && result.request().method() === "POST"),
    page.getByRole("button", { name: "Queue recording", exact: true }).click(),
  ]);
  const job = await response.json();
  const row = page.locator(`.job-row[data-job-id="${job.id}"]`);
  await expect(row.locator(".status")).toHaveText("Complete", { timeout: 150000 });
  await expect(row.getByText("AI reranked", { exact: true })).toBeVisible();
  const result = await page.evaluate(async (id) => {
    const token = (await (await fetch("/api/session")).json()).token;
    return (await fetch(`/api/jobs/${id}/report`, { headers: { "x-clipper-token": token } })).json();
  }, job.id);
  expect(result.ranking.status).toBe("applied");
  expect(result.ranking.gpu_layers).toBe(0);
  expect(result.ranking.assessed).toBeGreaterThan(0);
  expect(result.ranking.guard_version).toBe(1);
  expect(result.selection.version).toBe(2);
  expect(result.selection.maximum_shared_seconds).toBe(0.25);
  expect(result.selection.exported).toBe(result.exports.length);
  expect(result.stages.select).toBeGreaterThanOrEqual(0);
  expect(result.transcript.language.verified).toBe(false);
  expect(result.cloud_calls).toBe(0);
});

test("flagged transcript and AI fallback stay visible without overflow", async ({ page }, testInfo) => {
  await page.route("**/api/jobs", route => route.fulfill({ json: [{
    id: "quality-fixture", title: "Quality review fixture", kind: "file", source: "fixture.mp4",
    status: "succeeded", stage: "complete", progress: 1, message: "", created: Date.now() / 1000,
    request: null, options: { mode: "clips", profile: "quiet" },
    result: { exports: [], transcript: { version: 1, status: "unreliable" },
      ranking: { requested: true, method: "activity-v1", status: "saturated_scores" },
      warnings: ["Local AI gave near-perfect scores to most candidates; original activity ranking was retained."] },
  }] }));
  await page.goto("/");
  const row = page.locator('.job-row[data-job-id="quality-fixture"]');
  await expect(row.getByLabel("Transcript quality")).toHaveText("Transcript needs review");
  await expect(row.getByText("Activity ranked", { exact: true })).toBeVisible();
  await expect(row.getByText("AI reranked", { exact: true })).toHaveCount(0);
  await expect(row.getByText(/near-perfect scores/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-quality.png`), fullPage: true });
});

test("benchmark works without speech models when captions are disabled", async ({ page }) => {
  const fixture = resolve(dataRoot, "fixtures/generated-speech.mp4");
  test.skip(!existsSync(fixture), "Run the synthetic fixture script first");
  await page.goto("/");
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await page.getByLabel("Sample length").selectOption("60");
  await page.getByLabel("English captions").uncheck();
  await page.getByLabel("Recording file").setInputFiles(fixture);
  const [response] = await Promise.all([
    page.waitForResponse(result => result.url().endsWith("/api/uploads") && result.request().method() === "POST"),
    page.getByRole("button", { name: "Queue benchmark", exact: true }).click(),
  ]);
  const job = await response.json();
  const row = page.locator(`.job-row[data-job-id="${job.id}"]`);
  await expect(row.locator(".status")).toHaveText("Complete", { timeout: 150000 });
  const report = await page.evaluate(async (id) => {
    const token = (await (await fetch("/api/session")).json()).token;
    return (await fetch(`/api/jobs/${id}/report`, { headers: { "x-clipper-token": token } })).json();
  }, job.id);
  expect(report.exports).toHaveLength(2);
  expect(report.speech_device).toBe("not-run");
  expect(report.transcript.status).toBe("not_run");
  page.once("dialog", dialog => dialog.accept());
  const [removed] = await Promise.all([
    page.waitForResponse(result => result.url().endsWith(`/api/jobs/${job.id}`) && result.request().method() === "DELETE"),
    row.getByRole("button", { name: /^Remove / }).click(),
  ]);
  expect(removed.status()).toBe(200);
  await expect(row).toHaveCount(0);
  expect(existsSync(job.source)).toBe(false);
  expect(existsSync(resolve(dataRoot, "runs", job.id))).toBe(false);
});

test("webcam layout is explicit, previewed and exported above gameplay", async ({ page }, testInfo) => {
  const fixture = resolve(dataRoot, "fixtures/webcam-inset.mp4");
  test.skip(!existsSync(fixture), "Synthetic webcam inset fixture is not installed");
  await page.goto("/");
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByLabel("Recording file").setInputFiles(fixture);
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Portrait layout" });
  await expect(editor).toBeVisible();
  await editor.getByLabel("Webcam in source").check();
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByLabel("Webcam in source")).not.toBeChecked();
  await editor.getByLabel("Webcam in source").check();
  await expect(editor.getByLabel("Portrait zoom")).toHaveValue("1");
  await expect.poll(() => editor.locator("video").evaluate(element => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(3);
  const frame = await editor.locator(".source-frame").boundingBox();
  if (!frame) throw new Error("Source crop frame is not visible");
  await page.mouse.move(frame.x + frame.width * 0.8, frame.y + frame.height * 0.02);
  await page.mouse.down();
  await page.mouse.move(frame.x + frame.width * 0.98, frame.y + frame.height * 0.24);
  await page.mouse.up();
  expect(Number(await editor.getByLabel("webcam x percent").inputValue())).toBeCloseTo(80, 0);
  expect(Number(await editor.getByLabel("webcam width percent").inputValue())).toBeCloseTo(18, 0);
  await editor.getByText("Fine adjustment", { exact: true }).click();
  await editor.getByLabel("webcam x percent").fill("80");
  await editor.getByLabel("webcam y percent").fill("0");
  await editor.getByLabel("webcam width percent").fill("20");
  await editor.getByLabel("webcam height percent").fill("25");
  const webcamValues = await editor.locator('input[type="number"]').evaluateAll(elements => elements.map(element => ({ label: element.getAttribute("aria-label"), value: (element as HTMLInputElement).value })));
  await editor.getByRole("button", { name: "Gameplay region", exact: true }).click();
  await expect(editor.getByLabel("gameplay x percent")).toHaveValue("0");
  await expect(editor.getByLabel("gameplay y percent")).toHaveValue("0");
  await expect(editor.getByLabel("gameplay width percent")).toHaveValue("100");
  await expect(editor.getByLabel("gameplay height percent")).toHaveValue("100");
  await editor.getByLabel("gameplay x percent").fill("0");
  await editor.getByLabel("gameplay y percent").fill("0");
  await editor.getByLabel("gameplay height percent").fill("100");
  await editor.getByLabel("gameplay width percent").fill("100");
  const cropValues = await editor.locator('input[type="number"]').evaluateAll(elements => elements.map(element => ({ label: element.getAttribute("aria-label"), value: (element as HTMLInputElement).value })));
  await expect(editor.getByRole("alert"), JSON.stringify({ webcamValues, cropValues })).toHaveCount(0);
  await expect(editor.getByRole("button", { name: "Apply layout" })).toBeEnabled();
  await editor.getByLabel("Portrait zoom").fill("1.25");
  await expect(editor.getByLabel("Webcam zoom", { exact: true })).toHaveCount(0);
  await expect(editor.getByText("Gameplay framing", { exact: true })).toHaveCount(0);
  await editor.getByLabel("gameplay width percent").fill("80");
  await editor.getByLabel("gameplay height percent").fill("100");
  await expect.poll(() => editor.locator("video").evaluate(element => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
  await expect.poll(() => editor.locator("canvas").evaluate(element => {
    const canvas = element as HTMLCanvasElement;
    const values = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    return values.some((value, index) => index % 4 !== 3 && value > 80);
  })).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-webcam-layout.png`), fullPage: true });
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByRole("button", { name: "Benchmark", exact: true }).click();
  await page.getByLabel("Sample length").selectOption("60");
  await page.getByLabel("English captions").uncheck();
  const [response] = await Promise.all([
    page.waitForResponse(result => result.url().endsWith("/api/uploads") && result.request().method() === "POST"),
    page.getByRole("button", { name: "Queue benchmark", exact: true }).click(),
  ]);
  const job = await response.json();
  expect(job.options.webcam_layout).toMatchObject({ webcam: { x: 0.8, y: 0, width: 0.2, height: 0.25 }, gameplay: { x: 0, y: 0, width: 0.8, height: 1 } });
  expect(job.options.zoom).toBe(1.25);
  expect(job.options.webcam_layout.webcam_zoom).toBe(1);
  expect(job.options.webcam_layout.gameplay_fit).toBe("fit");
  const row = page.locator(`.job-row[data-job-id="${job.id}"]`);
  await expect(row.locator(".status")).toHaveText("Complete", { timeout: 150000 });
  const result = await page.evaluate(async (id) => {
    const token = (await (await fetch("/api/session")).json()).token;
    return (await fetch(`/api/jobs/${id}/report`, { headers: { "x-clipper-token": token } })).json();
  }, job.id);
  expect(result.exports).toHaveLength(2);
  expect(result.exports.every((clip: { encoding: { layout: { mode: string; webcam_output: { height: number }; gameplay_output: { y: number } } } }) =>
    clip.encoding.layout.mode === "webcam-over-gameplay" && clip.encoding.layout.webcam_output.height === 576 && clip.encoding.layout.gameplay_output.y === 576)).toBe(true);
  expect(result.exports.every((clip: { encoding: { layout: { webcam_alignment: string; gameplay_alignment: string; internal_gap_px: number } } }) =>
    clip.encoding.layout.webcam_alignment === "bottom" && clip.encoding.layout.gameplay_alignment === "top" && clip.encoding.layout.internal_gap_px === 0)).toBe(true);
  expect(result.exports.every((clip: { encoding: { layout: { webcam_zoom: number; gameplay_zoom: number; gameplay_fit: string } } }) =>
    clip.encoding.layout.webcam_zoom === 1 && clip.encoding.layout.gameplay_zoom === 1.25 && clip.encoding.layout.gameplay_fit === "fit")).toBe(true);
  await page.getByRole("region", { name: "Benchmark test exports" }).locator(`[data-job-id="${job.id}"]`).getByRole("button", { name: /Preview/ }).first().click();
  const video = page.locator(".preview-dialog video");
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).videoWidth)).toBe(1080);
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Close preview" }).click();
  page.once("dialog", dialog => dialog.accept());
  const [removed] = await Promise.all([
    page.waitForResponse(result => result.url().endsWith(`/api/jobs/${job.id}`) && result.request().method() === "DELETE"),
    row.getByRole("button", { name: /^Remove / }).click(),
  ]);
  expect(removed.status()).toBe(200);
  await expect(row).toHaveCount(0);
  expect(existsSync(job.source)).toBe(false);
});

test("webcam confirmation resets for a different source", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Video URL", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://youtu.be/abcdefghijk");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Portrait layout" });
  await editor.getByLabel("Webcam in source").check();
  await expect(editor.getByText("Source preview unavailable")).toBeVisible();
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://youtu.be/anotherclip");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByLabel("Webcam in source")).not.toBeChecked();
});

test("URL webcam crop uses a local screenshot and selected video frame without dimensions", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByText("Local worker connected")).toBeVisible({ timeout: 25000 });
  await page.getByRole("button", { name: "Video URL", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://youtu.be/abcdefghijk");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Portrait layout" });
  await editor.getByLabel("Webcam in source").check();
  await expect(editor.getByLabel("Portrait zoom")).toHaveValue("1");
  const screenshot = await page.evaluate(() => {
    const image = document.createElement("canvas"); image.width = 1000; image.height = 600;
    const context = image.getContext("2d")!;
    context.fillStyle = "#202020"; context.fillRect(0, 0, 1000, 600);
    context.fillStyle = "#008000"; context.fillRect(100, 75, 800, 450);
    context.fillStyle = "#0000ff"; context.fillRect(100, 75, 640, 18);
    context.fillStyle = "#ff0000"; context.fillRect(740, 75, 160, 112.5);
    return image.toDataURL("image/png").split(",")[1];
  });
  const posts: string[] = [];
  page.on("request", request => { if (request.method() === "POST") posts.push(request.url()); });
  await editor.getByLabel("Source screenshot", { exact: true }).setInputFiles({ name: "source-frame.png", mimeType: "image/png", buffer: Buffer.from(screenshot, "base64") });
  await expect(editor.getByRole("img", { name: "Source screenshot for region selection" })).toBeVisible();
  await expect(editor.getByRole("alert")).toHaveText("Select the webcam region on the screenshot.");
  await expect(editor.getByRole("button", { name: "Apply layout" })).toBeDisabled();
  await editor.getByRole("button", { name: "Video frame", exact: true }).click();
  let bounds = await editor.locator(".source-frame").boundingBox();
  if (!bounds) throw new Error("Screenshot frame is not visible");
  await page.mouse.move(bounds.x + bounds.width * 0.1, bounds.y + bounds.height * 0.125);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.9, bounds.y + bounds.height * 0.875);
  await page.mouse.up();
  await editor.getByRole("button", { name: "Use video frame", exact: true }).click();
  bounds = await editor.locator(".source-frame").boundingBox();
  if (!bounds) throw new Error("Selected video frame is not visible");
  expect(bounds.width / bounds.height).toBeCloseTo(16 / 9, 1);
  await page.mouse.move(bounds.x + bounds.width * 0.8, bounds.y + bounds.height * 0.02);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.99, bounds.y + bounds.height * 0.25);
  await page.mouse.up();
  await expect(editor.getByRole("button", { name: "Apply layout" })).toBeEnabled();
  await expect.poll(() => editor.getByLabel("Portrait layout preview").evaluate(element => {
    const pixel = (element as HTMLCanvasElement).getContext("2d")!.getImageData(100, 20, 1, 1).data;
    return pixel[0] > 180 && pixel[1] < 80;
  })).toBe(true);
  await expect.poll(() => editor.getByLabel("Portrait layout preview").evaluate(element => {
    const context = (element as HTMLCanvasElement).getContext("2d")!;
    const seam = context.getImageData(75, 146, 1, 1).data;
    const originalInset = context.getImageData(250, 150, 1, 1).data;
    return seam[2] > 180 && seam[0] < 70 && seam[1] < 70 && originalInset[0] > 180 && originalInset[1] < 70;
  })).toBe(true);
  expect(posts).toEqual([]);
  await expect(editor.getByLabel("webcam x percent")).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-screenshot-crop.png`), fullPage: true });
  expect(await editor.locator('input[type="number"]').evaluateAll(elements => elements.every(element => (element as HTMLInputElement).validity.valid))).toBe(true);
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByRole("img", { name: "Source screenshot for region selection" })).toBeVisible();
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await page.route("**/api/sources", route => route.fulfill({ status: 422, json: { detail: "Test request retained for crop inspection." } }));
  const [submitted] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith("/api/sources") && request.method() === "POST"),
    page.getByRole("button", { name: "Queue recording", exact: true }).click(),
  ]);
  const payload = submitted.postDataJSON();
  expect(payload.options.webcam_layout.webcam.x).toBeCloseTo(0.8, 2);
  expect(payload.options.webcam_layout.webcam.y).toBeCloseTo(0.02, 2);
  expect(payload.options.webcam_layout.webcam.width).toBeCloseTo(0.19, 2);
  expect(payload.options.webcam_layout.webcam.height).toBeCloseTo(0.23, 2);
  expect(payload.options.webcam_layout.gameplay).toEqual({ x: 0, y: 0, width: 1, height: 1 });
  expect(Object.keys(payload.options.webcam_layout).sort()).toEqual(["gameplay", "gameplay_fit", "top_fraction", "webcam", "webcam_zoom"]);
  expect(payload.options.webcam_layout.gameplay_fit).toBe("fit");
  expect(JSON.stringify(payload)).not.toContain("source-frame.png");
  expect(JSON.stringify(payload)).not.toContain("base64");
  await expect(page.getByRole("alert")).toHaveText("Test request retained for crop inspection.");
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
  await page.getByRole("button", { name: "Dismiss error" }).click();
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByLabel("Webcam in source")).toBeChecked();
  await expect(editor.getByRole("img")).toHaveCount(1);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://youtu.be/anotherclip");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByLabel("Webcam in source")).not.toBeChecked();
  await expect(editor.getByRole("img")).toHaveCount(0);
});

test("webcam screenshot errors are recoverable without queuing a job", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Portrait layout" });
  await editor.getByLabel("Webcam in source").check();
  await editor.getByLabel("Source screenshot", { exact: true }).setInputFiles({ name: "image.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg/>") });
  await expect(editor.getByRole("alert")).toHaveText("Use a PNG, JPEG or WebP screenshot up to 10 MB.");
  await expect(editor.getByRole("button", { name: "Apply layout" })).toBeDisabled();
  await editor.getByLabel("Source screenshot", { exact: true }).setInputFiles({ name: "broken.png", mimeType: "image/png", buffer: Buffer.from("not an image") });
  await expect(editor.getByRole("alert")).toHaveText("The screenshot could not be decoded.");
  for (const mimeType of ["image/jpeg", "image/webp"]) {
    const encoded = await page.evaluate(type => {
      const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
      const context = canvas.getContext("2d")!; context.fillStyle = "#008000"; context.fillRect(0, 0, 320, 180);
      return canvas.toDataURL(type).split(",")[1];
    }, mimeType);
    await editor.getByLabel("Source screenshot", { exact: true }).setInputFiles({ name: `replacement.${mimeType.split("/")[1]}`, mimeType, buffer: Buffer.from(encoded, "base64") });
    await expect(editor.getByRole("alert")).toHaveText("Select the webcam region on the screenshot.");
    await expect(editor.getByRole("button", { name: "Apply layout" })).toBeDisabled();
  }
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Webcam in source")).toHaveCount(0);
});

test("one portrait modal starts aspect-fitted with gameplay-only zoom and transactional settings", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Video URL", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://youtu.be/abcdefghijk");
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Portrait layout" });
  await expect(editor.getByLabel("Webcam in source")).not.toBeChecked();
  await expect(editor.getByLabel("Webcam zoom", { exact: true })).toHaveCount(0);
  await editor.getByLabel("Webcam in source").check();
  await expect(editor.getByLabel("Portrait zoom")).toHaveValue("1");
  await expect(editor.getByLabel("Webcam zoom", { exact: true })).toHaveCount(0);
  await expect(editor.getByText("Gameplay framing", { exact: true })).toHaveCount(0);
  await expect(editor.getByRole("button", { name: /^(Fill|Fit)$/ })).toHaveCount(0);
  const screenshot = await page.evaluate(() => {
    const image = document.createElement("canvas"); image.width = 320; image.height = 180;
    const context = image.getContext("2d")!;
    context.fillStyle = "#008000"; context.fillRect(0, 0, 320, 180);
    context.fillStyle = "#ffffff"; context.fillRect(148, 88, 24, 24);
    context.fillStyle = "#ff0000"; context.fillRect(256, 0, 64, 45);
    context.fillStyle = "#ffffff"; context.fillRect(275, 12, 12, 12);
    return image.toDataURL("image/png").split(",")[1];
  });
  await editor.getByLabel("Source screenshot", { exact: true }).setInputFiles({ name: "zoom-check.png", mimeType: "image/png", buffer: Buffer.from(screenshot, "base64") });
  await expect(editor.getByRole("img")).toBeVisible();
  await editor.getByText("Fine adjustment", { exact: true }).click();
  await editor.getByLabel("webcam x percent").fill("80");
  await editor.getByLabel("webcam y percent").fill("0");
  await editor.getByLabel("webcam width percent").fill("20");
  await editor.getByLabel("webcam height percent").fill("25");
  await editor.getByText("Fine adjustment", { exact: true }).click();
  const canvas = editor.getByLabel("Portrait layout preview");
  const checksum = (top: number, height: number) => canvas.evaluate((element, area) => {
    const image = (element as HTMLCanvasElement).getContext("2d")!.getImageData(0, area.top, 270, area.height);
    return image.data.reduce((total, value, index) => (total + value * (index % 97 + 1)) % 1000000007, 0);
  }, { top, height });
  await expect.poll(() => checksum(0, 144)).toBeGreaterThan(0);
  await expect.poll(() => canvas.evaluate(element => {
    const row = (element as HTMLCanvasElement).getContext("2d")!.getImageData(0, 228, 270, 1).data;
    let white = 0;
    for (let index = 0; index < row.length; index += 4) {
      if (Math.min(row[index], row[index + 1], row[index + 2]) > 220) white++;
    }
    return white;
  })).toBeGreaterThanOrEqual(18);
  expect(await canvas.evaluate(element => {
    const row = (element as HTMLCanvasElement).getContext("2d")!.getImageData(0, 228, 270, 1).data;
    let white = 0;
    for (let index = 0; index < row.length; index += 4) {
      if (Math.min(row[index], row[index + 1], row[index + 2]) > 220) white++;
    }
    return white;
  })).toBeLessThanOrEqual(22);
  const camera = await checksum(0, 144), gameplay = await checksum(144, 336);
  await editor.getByLabel("Portrait zoom").fill("2");
  await expect.poll(() => checksum(144, 336)).not.toBe(gameplay);
  expect(await checksum(0, 144)).toBe(camera);
  await editor.getByLabel("Webcam panel height").fill("35");
  await expect.poll(() => canvas.evaluate(element => {
    const pixel = (element as HTMLCanvasElement).getContext("2d")!.getImageData(130, 155, 1, 1).data;
    return pixel[0] > 180 && pixel[1] < 70;
  })).toBe(true);
  const bounds = await canvas.boundingBox();
  expect(bounds!.width / bounds!.height).toBeCloseTo(9 / 16, 3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(dataRoot, `screenshots/${testInfo.project.name}-unified-portrait.png`), fullPage: true });
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await editor.getByLabel("Portrait zoom").fill("1.1");
  await editor.getByLabel("Webcam panel height").fill("40");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Configure layout", exact: true }).click();
  await expect(editor.getByLabel("Portrait zoom")).toHaveValue("2");
  await expect(editor.getByLabel("Webcam panel height")).toHaveValue("35");
  await expect(editor.getByLabel("Webcam zoom", { exact: true })).toHaveCount(0);
  await editor.getByRole("button", { name: "Apply layout" }).click();
  await page.route("**/api/sources", route => route.fulfill({ status: 422, json: { detail: "Test crop saved." } }));
  const [submitted] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith("/api/sources") && request.method() === "POST"),
    page.getByRole("button", { name: "Queue recording", exact: true }).click(),
  ]);
  expect(submitted.postDataJSON().options).toMatchObject({ zoom: 2,
    webcam_layout: { webcam_zoom: 1, top_fraction: 0.35, gameplay_fit: "fit", gameplay: { x: 0, y: 0, width: 1, height: 1 } } });
});