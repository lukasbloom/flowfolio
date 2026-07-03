// Recovery path for a stale/invalid session cookie. The Next middleware only
// checks cookie PRESENCE (FastAPI is the validity authority), so a dead cookie
// renders pages whose every API call 401s — and /login bounces back to /track.
// On the first non-auth 401, clear the cookie server-side and hard-navigate to
// the login form. See middleware.ts.

// True for the auth endpoints themselves: a failed login/logout is handled
// inline by its form, never by the global expiry redirect.
export function isAuthPath(path: string): boolean {
  return path.startsWith("/api/auth/");
}

let inFlight = false;

// Best-effort logout (clears the HttpOnly cookie), then a full navigation to
// /login so the middleware re-runs without the dead cookie. Guarded so a burst
// of concurrent 401s produces one logout and one redirect.
export async function handleSessionExpired(): Promise<void> {
  if (inFlight) return;
  inFlight = true;

  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
  } catch {
    // Redirect regardless — worst case the middleware bounces once more.
  }

  const { pathname, search } = window.location;
  const next =
    pathname === "/login"
      ? ""
      : `?next=${encodeURIComponent(pathname + search)}`;
  window.location.assign(`/login${next}`);
}
