"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

interface ConcentrationBannerContextValue {
  expanded: boolean;
  setExpanded: (v: boolean) => void;
  /**
   * Signature of the offender set the user dismissed (e.g.
   * sorted-joined instrument IDs). Null when the banner has not been
   * dismissed for the current offender set. Stored in memory only
   * (no localStorage); resets on full page reload.
   *
   * Previously stored as a session-wide plain boolean, which
   * suppressed the banner even after the offender set changed (e.g. a
   * new position breaching the threshold weeks later). Keying the
   * dismissal by signature makes the banner reappear automatically
   * whenever the offenders differ from the dismissed snapshot.
   */
  dismissedKey: string | null;
  setDismissedKey: (v: string | null) => void;
}

const ConcentrationBannerContext =
  createContext<ConcentrationBannerContextValue | null>(null);

export function ConcentrationBannerProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  return (
    <ConcentrationBannerContext.Provider
      value={{ expanded, setExpanded, dismissedKey, setDismissedKey }}
    >
      {children}
    </ConcentrationBannerContext.Provider>
  );
}

export function useConcentrationBanner() {
  const ctx = useContext(ConcentrationBannerContext);
  if (!ctx) {
    throw new Error(
      "useConcentrationBanner must be used inside ConcentrationBannerProvider",
    );
  }
  return ctx;
}
