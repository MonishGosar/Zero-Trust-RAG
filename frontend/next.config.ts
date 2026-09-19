import type { NextConfig } from "next";

const config: NextConfig = {
  turbopack: { root: process.cwd() },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.API_URL || "http://127.0.0.1:8000"}/:path*` }];
  },
};
export default config;
