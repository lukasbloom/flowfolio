// CTA targets for the landing page. Read by index.astro (and Header/Footer) so
// no CTA href is ever hardcoded inline. Mutating a URL here updates
// every CTA at once.

// Public GitHub repo. Matches the `ghcr.io/lukasbloom/flowfolio` image namespace
// in compose.yml.
export const GITHUB_URL = "https://github.com/lukasbloom/flowfolio";

// Live public demo instance (the demo-mode build). The primary CTA points here.
export const DEMO_URL = "https://demo.flowfolio.lucasbarros.dev";

// Resolved primary-CTA target: the live demo once DEMO_URL is set, else the repo.
// The CTA label stays "Try the live demo" regardless, only the destination
// falls back.
export const RESOLVED_DEMO_URL = DEMO_URL || GITHUB_URL;
