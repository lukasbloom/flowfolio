"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

interface FxRateResponse {
  date: string;
  rate: string; // EUR-base rate (USD per 1 EUR), Decimal-as-string
}

interface Props {
  txnDate: string; // YYYY-MM-DD
  value: string;
  onChange: (rate: string) => void;
}

export function FxRateField({ txnDate, value, onChange }: Props) {
  const [userTouched, setUserTouched] = useState(false);
  const lastPrefillRef = useRef<string>("");

  // INFO #7: changing the txn date is a stronger user-intent signal than a previous
  // keystroke — re-arm the prefill so the new ECB rate fills the field. This effect
  // MUST run before the data-driven prefill effect so the userTouched=false state is
  // visible when the new query data arrives. Setting state in an effect is the
  // legitimate pattern here: the trigger (txnDate prop change) is external and the
  // alternative (lifting userTouched into the parent form) would couple FxRateField
  // to TxnForm internals. Same exception precedent as lib/currency.tsx.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUserTouched(false);
  }, [txnDate]);

  const { data, isError } = useQuery<FxRateResponse>({
    queryKey: ["fx-rate", txnDate],
    queryFn: () => apiFetch<FxRateResponse>(`/api/fx/${txnDate}?from=USD&to=EUR`),
    enabled: Boolean(txnDate),
    retry: false,
  });

  // Prefill when ECB rate arrives and user has not manually touched the field
  useEffect(() => {
    if (data && !userTouched) {
      onChange(data.rate);
      lastPrefillRef.current = data.rate;
    }
  }, [data, userTouched, onChange]);

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setUserTouched(true);
    onChange(e.target.value);
  }

  function handleReset() {
    if (data) {
      setUserTouched(false);
      onChange(data.rate);
      lastPrefillRef.current = data.rate;
    }
  }

  return (
    <div>
      <label htmlFor="fx-rate" className="text-base font-semibold">
        FX rate (EUR base, USD per 1 EUR)
      </label>
      <Input
        id="fx-rate"
        inputMode="decimal"
        className="tabular-nums mt-1"
        value={value}
        onChange={handleInputChange}
        placeholder="1.0512"
      />
      {isError ? (
        <p className="mt-1 text-xs text-destructive">
          Could not fetch ECB rate for {txnDate}. Enter the rate manually below.
        </p>
      ) : data ? (
        <p className={cn("mt-1 text-xs text-muted-foreground")}>
          {`ECB published rate for ${data.date}. Override if your broker applied a different rate.`}
          {userTouched && (
            <button
              type="button"
              onClick={handleReset}
              className="ml-2 underline underline-offset-2 hover:text-foreground"
            >
              Reset to ECB rate
            </button>
          )}
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">Fetching ECB rate…</p>
      )}
    </div>
  );
}
