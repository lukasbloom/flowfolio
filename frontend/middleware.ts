import { NextResponse, type NextRequest } from "next/server";

// Cookie name matches backend/app/core/auth.py SESSION_COOKIE_NAME = "session".
// Cookie-presence ONLY: the FastAPI AuthMiddleware remains the source of truth
// for cookie validity (it already returns 401 on forged/expired cookies).
const SESSION_COOKIE = "session";

// Consult GET /api/setup/status to learn whether the instance has been claimed.
// This fetch is issued ONLY from the no-session entry-point paths and the
// /setup branch — never on a request that already carries the session cookie
// (an authenticated user is, by definition, already claimed). This avoids the
// per-request blocking-fetch anti-pattern.
// On any failure the underlying fetch returns `null` (status unknown) and each
// branch picks its own safe default: the /setup branch fails OPEN toward
// the wizard so a fresh install is never locked out, while the no-session entry
// block defaults to "claimed → /login" so a transient hiccup never loops a
// claimed instance back into the wizard.
// Origin the Next server (middleware runs server-side, inside the container)
// uses to reach the API for the setup-status check. The external request host
// (`request.url`) is often NOT reachable from inside the container — in the
// single image the published HOST port (e.g. 8082) is not bound internally, so
// fetching `request.url` fails and falls back to "claimed=true", which wrongly
// routes an UNCLAIMED first-run instance to /login instead of /setup. Set
// SETUP_STATUS_ORIGIN to the in-container API origin per stack (the single image
// sets http://127.0.0.1:8080, the Caddy loopback). Defaults to the request
// origin so claimed stacks that never hit this path are unaffected.
const SETUP_STATUS_ORIGIN = process.env.SETUP_STATUS_ORIGIN;

// Fetch the raw claimed boolean, returning `null` when the status cannot be
// positively determined (non-200, throw, or a body missing `claimed`). Callers
// pick their own failure default. The safe default differs per branch.
async function fetchClaimed(request: NextRequest): Promise<boolean | null> {
  try {
    // Absolute URL on the in-container API origin when configured, else the
    // request origin (the same origin Caddy fronts; /api/* proxies to FastAPI).
    const base = SETUP_STATUS_ORIGIN ?? request.url;
    const statusUrl = new URL("/api/setup/status", base);
    const res = await fetch(statusUrl, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as { claimed?: boolean };
    return typeof data.claimed === "boolean" ? data.claimed : null;
  } catch {
    return null;
  }
}

// No-session entry block default: an UNKNOWN status falls back to "claimed" so
// the user lands on /login rather than being looped into /setup on a transient
// hiccup (a claimed instance must never be sent to the wizard). The unclaimed
// first-run case is positively confirmed (claimed === false) before redirecting
// to /setup.
async function isClaimedOrUnknown(request: NextRequest): Promise<boolean> {
  return (await fetchClaimed(request)) ?? true;
}

// /setup-branch default: fail OPEN toward the wizard. Only report
// "claimed" when we positively confirm it (claimed === true); an unknown status
// returns false so an unclaimed first-run instance is never bounced away from
// its only recovery path on a transient status-check failure. The backend
// /api/setup/claim 409 remains the real lock against an actually-claimed
// instance.
async function isClaimedStrict(request: NextRequest): Promise<boolean> {
  return (await fetchClaimed(request)) === true;
}

// Read the demo boot flag from GET /api/config, mirroring fetchClaimed:
// same SETUP_STATUS_ORIGIN base + cache: "no-store". Defaults to FALSE on any
// non-200 / throw / missing field — a status hiccup must never auto-enter the
// demo session. The demo-login endpoint's own 404-unless-demo
// is the backstop, so this is the UI half of a layered gate.
async function fetchDemo(request: NextRequest): Promise<boolean> {
  try {
    const base = SETUP_STATUS_ORIGIN ?? request.url;
    const configUrl = new URL("/api/config", base);
    const res = await fetch(configUrl, { cache: "no-store" });
    if (!res.ok) return false;
    const data = (await res.json()) as { demo?: boolean };
    return data.demo === true;
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);

  // /setup branch FIRST: the wizard must not re-appear once the instance is
  // claimed. Consult the status (one fetch, only on this route); a claimed
  // instance is redirected to /login, an unclaimed one is allowed through.
  if (pathname === "/setup") {
    // Fail-open toward the wizard: only redirect away when the instance
    // is POSITIVELY claimed. A failed/uncertain status check leaves the wizard
    // reachable so a fresh install is never permanently locked out.
    if (await isClaimedStrict(request)) {
      return NextResponse.redirect(new URL("/login", request.url), 307);
    }
    return NextResponse.next();
  }

  if (pathname === "/login") {
    if (hasSession) {
      // Logged-in user shouldn't see the login form — redirect to Track (the new default landing route).
      return NextResponse.redirect(new URL("/track", request.url), 307);
    }
    // In demo mode skip the password form entirely. Bounce the
    // visitor into the credential-free auto-session mint instead. The target
    // /api/auth/demo-login is outside the middleware matcher (Caddy → FastAPI),
    // so this never re-enters middleware. fetchDemo defaults to false, and the
    // endpoint itself 404s unless demo_mode, so a non-demo instance is never
    // redirected here and its password login is byte-for-byte unchanged.
    if (await fetchDemo(request)) {
      return NextResponse.redirect(
        new URL("/api/auth/demo-login", request.url),
        307,
      );
    }
    return NextResponse.next();
  }

  // The root route folder is deleted; redirect
  // every request for `/` to `/track`. This branch must run BEFORE the
  // auth-gate block below so that an unauthed `/` redirects to `/track`
  // (and the auth gate then re-redirects to `/login?next=/track`).
  if (pathname === "/") {
    return NextResponse.redirect(new URL("/track", request.url), 307);
  }

  if (!hasSession) {
    // Entry point for an unauthenticated user: an unclaimed instance must be
    // routed to the first-run wizard rather than the login form. The status
    // fetch happens only here (no session) — authenticated navigations skip it.
    // Redirect to /setup ONLY when the instance is positively unclaimed; an
    // unknown status falls through to /login (isClaimedOrUnknown defaults to
    // claimed) so a transient hiccup never loops a claimed instance into the
    // wizard.
    if (!(await isClaimedOrUnknown(request))) {
      return NextResponse.redirect(new URL("/setup", request.url), 307);
    }
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = ""; // drop existing query params before appending ?next=
    if (pathname !== "/") {
      url.searchParams.set("next", pathname + search);
    }
    return NextResponse.redirect(url, 307);
  }

  return NextResponse.next();
}

export const config = {
  // Run on every request EXCEPT:
  //  - /api/*   (Caddy proxies these to FastAPI; FastAPI AuthMiddleware enforces 401)
  //  - /_next/* (Next.js internals: static assets, image optimizer, HMR)
  //  - /favicon.ico, /robots.txt, /sitemap.xml, /manifest.webmanifest (public static files)
  //  - any path with a file extension (e.g. .png, .svg, .css, .js) served from /public
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|manifest.webmanifest|.*\\..*).*)",
  ],
};
