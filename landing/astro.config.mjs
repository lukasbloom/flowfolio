// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// Static landing site (Astro default output). Tailwind 4 is wired through the
// Vite plugin, not the legacy @astrojs/tailwind integration.
export default defineConfig({
  output: "static",
  // Keep readable, line-broken HTML in dist (each element on its own line). The
  // raw-byte cost is paid back almost entirely by gzip/brotli at the edge
  // (Caddy), so the static-fast perf budget is unaffected, and the OSS launch
  // page's output stays human-auditable.
  compressHTML: false,
  vite: {
    plugins: [tailwindcss()],
  },
});
