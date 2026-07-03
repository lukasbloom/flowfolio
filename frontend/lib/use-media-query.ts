"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Subscribe to a CSS media query.
 *
 * Built on React 18's `useSyncExternalStore`, which is the canonical primitive
 * for binding component state to an external store (browser APIs, redux,
 * matchMedia, …) without hydration races:
 *
 * - On the server (`getServerSnapshot`) it always returns `false`, so the
 *   server-rendered HTML is deterministic.
 * - On the client (`getSnapshot`) it reads `matchMedia(query).matches`
 *   synchronously, so the very first client render after hydration already
 *   has the correct value — no `useEffect` round-trip, no extra render
 *   with the wrong branch.
 * - React reconciles a server/client snapshot mismatch automatically by
 *   re-rendering after the initial commit; callers don't have to handle it.
 * - `subscribe` is bound to `matchMedia(query)`'s `change` event, so a window
 *   resize that crosses the breakpoint re-renders consumers.
 *
 * Why this matters: TxnList renders a desktop table AND a mobile card stack
 * inside `useWindowVirtualizer`s. The hidden branch's rows sit inside
 * `display:none` and report `offsetHeight: 0`, so the inactive virtualizer
 * mounts ~190 hidden cards (each carrying a Radix DropdownMenu) trying to
 * fill the viewport — a ~2.6s main-thread block per SPA navigation. Gating
 * the inactive virtualizer's `count` to 0 fixes it, but only if the very
 * first render already knows which branch is active. The lazy-init `useState`
 * pattern also achieves that, but `useSyncExternalStore` is React's blessed
 * answer for this exact problem (and avoids the `eslint-disable
 * react-hooks/set-state-in-effect` workaround the old implementation needed).
 *
 * Used by AddTxnPicker, AddTxnFormSheet,
 * EditTxnDialog, MoreSheet, AddButton, AddTxnFab, TxnList, TxnRowActions
 * branching at the `(min-width: 768px)` breakpoint.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined") return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );

  const getSnapshot = useCallback(
    () => window.matchMedia(query).matches,
    [query],
  );

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

const getServerSnapshot = () => false;
