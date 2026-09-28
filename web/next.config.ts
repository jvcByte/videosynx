import type { NextConfig } from "next";

const config: NextConfig = {
  // The API is the only backend; there is no server-side data fetching here
  // yet, so nothing needs proxying.
  reactStrictMode: true,
};

export default config;
