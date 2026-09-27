import { defineConfig, devices } from "@playwright/test";

// Smoke test: starts the FastAPI backend on fixture data (fresh SQLite file, auto-scan on
// startup) and the Vite dev server, then drives the dashboard in Chromium.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8765);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5199);
const E2E_DB = "../frontend/test-results/e2e.db";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  outputDir: "./test-results/artifacts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `mkdir -p ../frontend/test-results && rm -f ${E2E_DB} && uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: {
        DATABASE_URL: `sqlite+aiosqlite:///${E2E_DB}`,
        DATA_MODE: "fixture",
        AUTO_SCAN_ON_STARTUP: "true",
        LOG_LEVEL: "WARNING",
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { VITE_API_PROXY: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
