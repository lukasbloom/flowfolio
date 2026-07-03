import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  // API calls from the browser go through Caddy → api container
  // No rewrites needed at Next.js level; Caddy handles /api routing
};

export default nextConfig;
