import { existsSync } from "node:fs";
import path from "node:path";

import type { NextConfig } from "next";

// One .env, at the top of the repository.
//
// Next reads env files from its own directory, which is this one, and there is
// none here -- so NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY
// were undefined in every build, the Supabase client was never constructed,
// and the web app could not sign anyone in no matter what was configured on
// the API. The other three programs all find the file by walking up; this
// makes the fourth do the same rather than keeping a second copy of the same
// two values in a second place.
const ROOT_ENV = path.resolve(__dirname, "..", ".env");
if (existsSync(ROOT_ENV)) {
  process.loadEnvFile(ROOT_ENV);
}

// Where the API is. Read by the server when it starts, and never sent to the
// browser -- see the rewrite below.
const API_ORIGIN = process.env.VIDEOSYNX_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,

  // Inlined into the client bundle explicitly rather than left to the
  // NEXT_PUBLIC_ convention, because the values were only just put into
  // process.env above: this is the one place that guarantees what the config
  // read is what the browser is compiled with. Both are public by design --
  // the anon key is constrained by row-level security at the database, not by
  // being secret.
  env: {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  },

  // A build directory of its own when asked for one.
  //
  // NEXT_PUBLIC_* values are inlined into a client chunk at compile time, so
  // two dev servers sharing `.next` means whichever compiled the bundle last
  // decides the values for both -- and the loser silently runs with the
  // other's. That is not hypothetical: it made a test suite submit jobs as
  // the wrong account. The auth test run sets this and gets its own.
  distDir: process.env.VIDEOSYNX_DIST_DIR ?? ".next",

  // The browser calls /api on its own origin and the server forwards it.
  //
  // This replaces NEXT_PUBLIC_API_URL, which had two faults. The value was
  // inlined into a client chunk at compile time, so changing it needed a
  // rebuild and a hard reload -- and a browser that had cached the chunk kept
  // calling the old address until someone cleared site data. Same-origin also
  // means the browser never sends a preflight, so CORS cannot be the reason a
  // request fails.
  //
  // Because nothing is inlined into the client any more, the dev server and a
  // test run compile the same bundle and can share one build directory.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/:path*` }];
  },
};

export default config;
