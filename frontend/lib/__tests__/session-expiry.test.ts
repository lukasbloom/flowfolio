import { test } from "node:test";
import assert from "node:assert/strict";

// Each test imports the module through a unique query-string specifier so the
// module-level once-guard starts fresh per test (Node caches ESM by specifier).
async function freshModule(tag: string) {
  const url = new URL("../session-expiry.ts", import.meta.url);
  url.search = `?${tag}`;
  return import(url.href) as Promise<{
    isAuthPath: (path: string) => boolean;
    handleSessionExpired: () => Promise<void>;
  }>;
}

type FetchCall = { input: string; init?: RequestInit };

function installBrowserMocks(pathname: string, search = "") {
  const fetchCalls: FetchCall[] = [];
  const assignedTo: string[] = [];
  (globalThis as Record<string, unknown>).fetch = async (
    input: string,
    init?: RequestInit,
  ) => {
    fetchCalls.push({ input, init });
    return { ok: true } as Response;
  };
  (globalThis as Record<string, unknown>).window = {
    location: {
      pathname,
      search,
      assign: (url: string) => {
        assignedTo.push(url);
      },
    },
  };
  return { fetchCalls, assignedTo };
}

test("isAuthPath matches only /api/auth/* endpoints", async () => {
  const { isAuthPath } = await freshModule("isauthpath");
  assert.equal(isAuthPath("/api/auth/login"), true);
  assert.equal(isAuthPath("/api/auth/logout"), true);
  assert.equal(isAuthPath("/api/accounts"), false);
  assert.equal(isAuthPath("/api/authors"), false);
});

test("expired session clears the cookie once and redirects with next", async () => {
  const { handleSessionExpired } = await freshModule("once");
  const { fetchCalls, assignedTo } = installBrowserMocks("/track", "?tf=1Y");

  await handleSessionExpired();
  await handleSessionExpired(); // concurrent 401s must not stampede

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].input, "/api/auth/logout");
  assert.equal(fetchCalls[0].init?.method, "POST");
  assert.deepEqual(assignedTo, [
    `/login?next=${encodeURIComponent("/track?tf=1Y")}`,
  ]);
});

test("redirect from /login itself omits the next param", async () => {
  const { handleSessionExpired } = await freshModule("onlogin");
  const { assignedTo } = installBrowserMocks("/login");

  await handleSessionExpired();

  assert.deepEqual(assignedTo, ["/login"]);
});

test("redirects even when the logout call fails", async () => {
  const { handleSessionExpired } = await freshModule("logoutfail");
  const { assignedTo } = installBrowserMocks("/holdings");
  (globalThis as Record<string, unknown>).fetch = async () => {
    throw new TypeError("network down");
  };

  await handleSessionExpired();

  assert.deepEqual(assignedTo, [
    `/login?next=${encodeURIComponent("/holdings")}`,
  ]);
});
