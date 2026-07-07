import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  // Allow the local dev-proxy hosts to reach Next dev resources (HMR websocket,
  // RSC). Without this, Next 15+ blocks cross-origin dev requests from a
  // non-localhost host, so the client never hydrates behind the proxy. The
  // dev-proxy uses the .test convention, so a subdomain wildcard covers every
  // site (flowfolio.test, etc.). Dev-only (the standalone prod build ignores it).
  allowedDevOrigins: ["*.test"],
  // API calls from the browser go through Caddy → api container
  // No rewrites needed at Next.js level; Caddy handles /api routing
};

export default nextConfig;
