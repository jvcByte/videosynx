import type { NextConfig } from "next";

// Where the API is. Read by the server when it starts, and never sent to the
// browser -- see the rewrite below.
const API_ORIGIN = process.env.PYCODING_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,

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
