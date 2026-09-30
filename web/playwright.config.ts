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
        // Empty, and deliberately so. The API loads the repository's .env,
        // which in a configured deployment has a Supabase project in it --
        // and configuring one disables the X-Dev-User header these tests
        // sign in with, so every request would come back 401. An empty value
        // wins: loading a .env file never overwrites a variable that is
        // already set, which is what makes this work.
        `SUPABASE_URL= SUPABASE_JWKS_URL= SUPABASE_JWT_SECRET= ` +
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
        // The same, on the browser side: with a Supabase URL compiled in, the
        // app renders the sign-in screen and never sends the development
        // header at all, so there is nothing here to test.
        `NEXT_PUBLIC_SUPABASE_URL= NEXT_PUBLIC_SUPABASE_ANON_KEY= ` +
        `npm run dev`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      stdout: "ignore",
    },
  ],
});
