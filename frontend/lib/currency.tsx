"use client";

import { createContext, useContext, type ReactNode } from "react";

import { usePref } from "@/lib/prefs";

type Currency = "EUR" | "USD";
const STORAGE_KEY = "flowfolio.displayCurrency";

interface CurrencyContextValue {
  currency: Currency;
  setCurrency: (c: Currency) => void;
}

const CurrencyContext = createContext<CurrencyContextValue | null>(null);

export function CurrencyProvider({ children }: { children: ReactNode }) {
  // Cookie-backed pref (lib/prefs.tsx): the (app) server
  // layout reads the cookie and seeds PrefsProvider, so SSR renders the
  // stored currency, no default→stored flash after hydration and no
  // throwaway default-key fetch.
  const [raw, setRaw] = usePref(STORAGE_KEY);
  const currency: Currency = raw === "USD" ? "USD" : "EUR";

  const setCurrency = (c: Currency) => {
    setRaw(c);
  };

  return (
    <CurrencyContext.Provider value={{ currency, setCurrency }}>
      {children}
    </CurrencyContext.Provider>
  );
}

export function useCurrency() {
  const ctx = useContext(CurrencyContext);
  if (!ctx) throw new Error("useCurrency must be used inside CurrencyProvider");
  return ctx;
}
