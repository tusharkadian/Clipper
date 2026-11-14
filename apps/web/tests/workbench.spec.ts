import { test, expect } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dataRoot = fileURLToPath(new URL("../../../.data/", import.meta.url));

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
  await expect(page.getByLabel("Portrait zoom")).toHaveValue("1.5");
  await page.getByLabel("Portrait zoom").fill("1.25");
  await expect(page.locator(".zoom-field output")).toHaveText("1.25x");
  await page.getByRole("button", { name: "Video URL", exact: true }).click();
  await page.getByLabel("YouTube video or Twitch VOD").fill("https://127.0.0.1/private");
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
    clip.encoding.renderer_version === 6 && clip.encoding.captions.bottom_margin_px === 173)).toBe(true);
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