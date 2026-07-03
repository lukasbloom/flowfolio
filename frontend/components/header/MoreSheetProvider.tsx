"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

interface MoreSheetContextValue {
  open: boolean;
  setOpen: (v: boolean) => void;
}

const MoreSheetContext = createContext<MoreSheetContextValue | null>(null);

export function MoreSheetProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <MoreSheetContext.Provider value={{ open, setOpen }}>
      {children}
    </MoreSheetContext.Provider>
  );
}

export function useMoreSheet() {
  const ctx = useContext(MoreSheetContext);
  if (!ctx) {
    throw new Error("useMoreSheet must be used inside MoreSheetProvider");
  }
  return ctx;
}
