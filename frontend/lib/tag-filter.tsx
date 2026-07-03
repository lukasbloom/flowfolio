"use client";

import { createContext, useContext, type ReactNode } from "react";

import { usePref } from "@/lib/prefs";

type TagFilter = string | null;
const STORAGE_KEY = "flowfolio.tagFilter";

interface TagFilterContextValue {
  tagFilter: TagFilter;
  setTagFilter: (t: TagFilter) => void;
}

const TagFilterContext = createContext<TagFilterContextValue | null>(null);

export function TagFilterProvider({ children }: { children: ReactNode }) {
  // Cookie-backed pref (lib/prefs.tsx): the (app) server
  // layout reads the cookie and seeds PrefsProvider, so SSR renders the
  // stored tag, no default→stored flash after hydration and no throwaway
  // default-key fetch.
  const [raw, setRaw] = usePref(STORAGE_KEY);
  const tagFilter: TagFilter = raw && raw.length > 0 ? raw : null;

  const setTagFilter = (t: TagFilter) => {
    setRaw(t);
  };

  return (
    <TagFilterContext.Provider value={{ tagFilter, setTagFilter }}>
      {children}
    </TagFilterContext.Provider>
  );
}

export function useTagFilter() {
  const ctx = useContext(TagFilterContext);
  if (!ctx) throw new Error("useTagFilter must be used inside TagFilterProvider");
  return ctx;
}
