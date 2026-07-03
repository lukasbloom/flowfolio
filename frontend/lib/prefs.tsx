"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useSyncExternalStore,
  type ReactNode,
} from "react";

/**
 * Cookie-backed persisted UI preferences.
 *
 * Why cookies and not localStorage: these prefs (display currency, tag
 * filter, net-worth chart toggles, instrument filter) feed both rendered
 * markup (header chips, switches) and TanStack Query keys. localStorage is
 * invisible to the server, so SSR rendered default-state HTML and the stored
 * value only appeared after hydration — a visible default→stored flash on
 * every reload, plus one throwaway default-key /api/networth request (debug
 * session: networth-chart-double-render). Cookies travel with the request:
 * the (app) server layout reads them and seeds <PrefsProvider initial={...}>,
 * so `getServerSnapshot` returns the *stored* value during SSR and hydration.
 * Server HTML is correct from the first byte — no flash, no hydration
 * mismatch, no corrective re-render, no duplicate fetch.
 *
 * Trade-off: cookies have no client-side change event, so the cross-tab sync
 * the localStorage `storage` event provided is gone. Acceptable for a
 * single-user app; same-tab consumers stay in sync via the subscriber
 * registry below.
 */

/** All persisted pref slots — used by the one-time localStorage migration. */
export const PREF_KEYS = [
  "flowfolio.displayCurrency",
  "flowfolio.tagFilter",
  "flowfolio.nwShowTransactions",
  "flowfolio.nwShowCostBasis",
  "flowfolio.nwShowYields",
  "flowfolio.instrumentFilter.networth",
] as const;

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

const PrefsInitialContext = createContext<Record<string, string>>({});

const subscribers = new Map<string, Set<() => void>>();

function getSubscribers(key: string): Set<() => void> {
  let set = subscribers.get(key);
  if (!set) {
    set = new Set();
    subscribers.set(key, set);
  }
  return set;
}

function subscribe(key: string, listener: () => void): () => void {
  const set = getSubscribers(key);
  set.add(listener);
  return () => {
    set.delete(listener);
  };
}

function notify(key: string): void {
  const set = subscribers.get(key);
  if (!set) return;
  for (const listener of set) listener();
}

function readCookie(key: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${key}=`;
  for (const part of document.cookie.split("; ")) {
    if (part.startsWith(prefix)) {
      try {
        return decodeURIComponent(part.slice(prefix.length));
      } catch {
        return part.slice(prefix.length);
      }
    }
  }
  return null;
}

function writeCookie(key: string, value: string | null): void {
  if (value === null) {
    document.cookie = `${key}=; path=/; max-age=0; samesite=lax`;
  } else {
    document.cookie = `${key}=${encodeURIComponent(value)}; path=/; max-age=${ONE_YEAR_SECONDS}; samesite=lax`;
  }
}

export function PrefsProvider({
  initial,
  children,
}: {
  initial: Record<string, string>;
  children: ReactNode;
}) {
  // One-time migration of pre-cookie localStorage prefs.
  // Runs after hydration, so the very first load after this ships may still
  // flash once while values move over; every load after reads cookies on the
  // server and is flash-free. Safe to remove once all browsers that ever used
  // the localStorage slots have loaded the app once.
  useEffect(() => {
    if (typeof window === "undefined") return;
    for (const key of PREF_KEYS) {
      if (readCookie(key) !== null) continue;
      const legacy = window.localStorage.getItem(key);
      if (legacy === null) continue;
      writeCookie(key, legacy);
      window.localStorage.removeItem(key);
      notify(key);
    }
  }, []);

  return (
    <PrefsInitialContext.Provider value={initial}>
      {children}
    </PrefsInitialContext.Provider>
  );
}

export function usePref(
  key: string
): [string | null, (next: string | null) => void] {
  const initial = useContext(PrefsInitialContext);

  const value = useSyncExternalStore(
    useCallback((listener) => subscribe(key, listener), [key]),
    // Strings compare by value in useSyncExternalStore's Object.is check, so
    // a fresh parse per call is snapshot-stable.
    useCallback(() => readCookie(key), [key]),
    // Server + hydration render: the cookie map the layout read from the
    // request. Matches what readCookie() returns on the client for the same
    // request, so hydration never needs a corrective re-render.
    useCallback(() => initial[key] ?? null, [initial, key])
  );

  const setValue = useCallback(
    (next: string | null) => {
      if (typeof document === "undefined") return;
      writeCookie(key, next);
      notify(key);
    },
    [key]
  );

  return [value, setValue];
}
