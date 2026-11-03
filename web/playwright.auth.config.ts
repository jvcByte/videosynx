import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const WEB_PORT = 3022;

/**
 * The app with a Supabase project configured, which is what a deployment is.
 *
 * A config of its own rather than a second project in the main one, because
 * both would run `next dev` in this directory at the same time and share a
 * `.next` build -- and `NEXT_PUBLIC_*` values are inlined into a client chunk
 * at compile time, so whichever server compiled it last wins for both. That
 * is not a theory: running them together made the main suite submit jobs as
 * the wrong account, which is the same fault the comment in
 * `next.config.ts` was written about.
 *
 * So this runs on its own, after the other one has finished and its server has
 * been torn down. `npm test` runs both in order.
 *
 * No API server: nothing here makes an API request, which is the point of the
 * main assertion. The Supabase URL points at a port nothing answers, because
 * signed out is decided before Supabase is ever contacted.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: /auth\.spec\.ts/,
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "auth", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        `PORT=${WEB_PORT} ` +
        // Its own build directory. Sharing `.next` with the main run makes
        // this server serve the bundle that run compiled -- which has no
        // Supabase in it, so the app renders as if there were no project and
        // every assertion here fails for the wrong reason.
        `VIDEOSYNX_DIST_DIR=.next-auth ` +
        `NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:9 ` +
        `NEXT_PUBLIC_SUPABASE_ANON_KEY=test-anon-key ` +
        `pnpm run dev`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      stdout: "ignore",
      cwd: path.resolve(__dirname),
    },
  ],
});
