"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

interface TagsManagerContextValue {
  open: boolean;
  openManager: () => void;
  closeManager: () => void;
}

const TagsManagerContext = createContext<TagsManagerContextValue | null>(null);

export function TagsManagerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <TagsManagerContext.Provider
      value={{
        open,
        openManager: () => setOpen(true),
        closeManager: () => setOpen(false),
      }}
    >
      {children}
    </TagsManagerContext.Provider>
  );
}

export function useTagsManager() {
  const ctx = useContext(TagsManagerContext);
  if (!ctx) {
    throw new Error("useTagsManager must be used inside TagsManagerProvider");
  }
  return ctx;
}
