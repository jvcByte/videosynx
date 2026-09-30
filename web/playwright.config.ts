import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// The venv, not the system python: the API and its dependencies live there.
const PYTHON =
  process.env.VIDEOSYNX_PYTHON ?? path.resolve(__dirname, "../.venv/bin/python");
const API_PORT = 8021;
const WEB_PORT = 3021;

/**
 * Both servers are started here so the tests run against the real stack: a
 * real API, real Postgres, real storage, and the real Next.js app. Only the
 * render itself is stubbed, because it takes minutes.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./tests/global-setup.ts",
  timeout: 60_000,
  fullyParallel: false, // the tests share one database and one quota
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /auth\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command:
        `VIDEOSYNX_DSN=postgresql:///videosynx_e2e ` +
        `VIDEOSYNX_ARTIFACTS=/tmp/videosynx/e2e-artifacts ` +
        `CORS_ORIGINS=http://127.0.0.1:${WEB_PORT} ` +
        `${PYTHON} -m videosynx_api --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: false,
      stdout: "ignore",
      cwd: path.resolve(__dirname, ".."),
    },
    {
      // The same identity the tests submit as. Without it the page is a
      // different account and, correctly, cannot see the test's jobs.
      // Its own build directory, or this server and a developer's `npm run
      // dev` overwrite each other's compiled bundle -- and NEXT_PUBLIC_* is
      // inlined at compile time, so the survivor's API URL wins for both.
      // VIDEOSYNX_API_URL, not NEXT_PUBLIC_API_URL: the server reads it at
      // startup and proxies, so it never reaches the browser and never needs
      // clearing from a cache.
      command:
        `PORT=${WEB_PORT} ` +
        `VIDEOSYNX_API_URL=http://127.0.0.1:${API_PORT} ` +
        `NEXT_PUBLIC_DEV_USER=e2e-user:free ` +
        `VIDEOSYNX_DIST_DIR=.next-e2e ` +
        `npm run dev`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      stdout: "ignore",
    },
  ],
});
