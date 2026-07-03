"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { CreateInstrumentDialog } from "@/components/instruments/CreateInstrumentDialog";
import { apiFetch } from "@/lib/api-client";

interface Instrument {
  id: string;
  symbol: string;
  name: string;
  // Type and display_decimals are pulled forward into
  // the AddedInstrumentRow so the diff table can format the (always-zero)
  // app_qty at the right precision when the user adds a missing holding.
  instrument_type?: string;
  display_decimals?: number | null;
  price_currency: string | null;
}

export interface AddedInstrumentRow {
  instrument_id: string;
  instrument_symbol: string;
  instrument_name: string;
  // Required to satisfy the DriftRow contract — the
  // diff table renders quantity using decimalsFor({instrument_type, ...}).
  // Falls back to "stock" only if the instrument list response is missing
  // the field (legacy/cached payloads); the per-type default for "stock"
  // is 4dp which is a sensible neutral.
  instrument_type: string;
  display_decimals?: number | null;
  price_currency: string | null;
  app_qty: "0";
}

interface Props {
  excludeIds: string[];
  onAdd: (row: AddedInstrumentRow) => void;
}

export function AddInstrumentRow({ excludeIds, onAdd }: Props) {
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState("");

  const { data: instruments = [] } = useQuery<Instrument[]>({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments"),
  });

  const filtered = useMemo(() => {
    const exclude = new Set(excludeIds);
    const q = query.trim().toLowerCase();
    return instruments
      .filter((i) => !exclude.has(i.id))
      .filter((i) => {
        if (!q) return true;
        return (
          i.symbol.toLowerCase().includes(q) ||
          i.name.toLowerCase().includes(q)
        );
      });
  }, [instruments, excludeIds, query]);

  function handleSelect(instrument: Instrument) {
    onAdd({
      instrument_id: instrument.id,
      instrument_symbol: instrument.symbol,
      instrument_name: instrument.name,
      instrument_type: instrument.instrument_type ?? "stock",
      display_decimals: instrument.display_decimals ?? null,
      price_currency: instrument.price_currency,
      app_qty: "0",
    });
    setQuery("");
    setOpen(false);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" className="w-full justify-center gap-2">
          <Plus className="size-4" aria-hidden />
          Add a holding…
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={8}
        className="w-80 p-0"
      >
        <div className="border-b p-2">
          <Input
            autoFocus
            placeholder="Search for an instrument…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="max-h-72 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              {query
                ? `No instruments match "${query}".`
                : "No more instruments to add."}
            </p>
          ) : (
            filtered.map((instrument) => (
              <button
                key={instrument.id}
                type="button"
                onClick={() => handleSelect(instrument)}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground"
              >
                <span className="font-medium">{instrument.symbol}</span>
                <span className="truncate text-muted-foreground">
                  {instrument.name}
                </span>
              </button>
            ))
          )}
        </div>
        <div className="border-t p-1">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setCreateOpen(true);
            }}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <Plus className="size-3.5" aria-hidden /> New instrument…
          </button>
        </div>
      </PopoverContent>
      <CreateInstrumentDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(instrument) => {
          // Use the response payload directly — do NOT consult the cache, which
          // may not have repopulated yet from the invalidate. This works on
          // every machine, fast or slow.
          onAdd({
            instrument_id: instrument.id,
            instrument_symbol: instrument.symbol,
            instrument_name: instrument.name,
            // Pull instrument_type and the optional
            // display_decimals override forward so the diff table renders
            // the (always-zero) qty at the correct precision.
            instrument_type: instrument.instrument_type,
            display_decimals: instrument.display_decimals ?? null,
            // POST /api/instruments returns base_currency; the recon row uses
            // price_currency (live-price denomination). Fall back to base_currency
            // since a freshly-created instrument has no price snapshot yet.
            price_currency: instrument.base_currency,
            app_qty: "0",
          });
        }}
      />
    </Popover>
  );
}
