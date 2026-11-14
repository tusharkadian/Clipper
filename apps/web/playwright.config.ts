import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { delimiter, join } from "node:path";

if (process.platform === "win32" && process.env.SystemRoot) {
  process.env.PATH = [join(process.env.SystemRoot, "System32"), process.env.PATH || ""].join(delimiter);
}

const baseURL = process.env.CLIPPER_URL || "http://127.0.0.1:8765";
const testPython = process.env.CLIPPER_TEST_PYTHON || fileURLToPath(new URL("../../.venv/Scripts/python.exe", import.meta.url));

export default defineConfig({
  testDir: "./tests",
  timeout: 180000,
  workers: 1,
  webServer: process.env.CLIPPER_TEST_START_SERVER === "1" ? {
    command: `"${testPython}" -m clipper.cli serve --port ${new URL(baseURL).port || "8765"}`,
    url: `${baseURL}/api/health`,
    timeout: 60000,
    reuseExistingServer: false,
  } : undefined,
  use: { baseURL, headless: true, actionTimeout: 10000 },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true } },
  ],
  reporter: "list",
});