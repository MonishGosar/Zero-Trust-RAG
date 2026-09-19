import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3100",
    viewport: { width: 1440, height: 900 },
    channel: process.platform === "win32" ? "msedge" : undefined,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: process.platform === "win32"
        ? "..\\.venv\\Scripts\\python.exe -m uvicorn tests.browser_server:app --app-dir .. --host 127.0.0.1 --port 8100"
        : "../.venv/bin/python -m uvicorn tests.browser_server:app --app-dir .. --host 127.0.0.1 --port 8100",
      url: "http://127.0.0.1:8100/health", timeout: 120000,
    },
    {
      command: "npm run dev -- --port 3100",
      url: "http://127.0.0.1:3100", timeout: 120000,
      env: { API_URL: "http://127.0.0.1:8100" },
    },
  ],
});
