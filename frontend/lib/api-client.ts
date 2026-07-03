import { handleSessionExpired, isAuthPath } from "./session-expiry";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`API ${status}: ${detail}`);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include", // session cookie
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    // A 401 outside the auth endpoints means the session cookie is dead —
    // recover to the login form instead of stranding the user on a page of
    // failed queries. The ApiError still throws so in-flight UI settles.
    if (res.status === 401 && typeof window !== "undefined" && !isAuthPath(path)) {
      void handleSessionExpired();
    }
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
