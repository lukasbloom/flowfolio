"use client";

/**
 * InstrumentFormDialog — owns the PUT mutation + success toast + close-
 * on-success for the instrument editor. Mirrors `AccountFormDialog`.
 *
 * Parent (OverviewTab) mounts this CONDITIONALLY on its `editOpen` flag
 * so InstrumentForm's `useState(defaultValues)` cannot bleed between
 * different instruments (the component re-mounts fresh every time the
 * dialog opens).
 */

import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import type { InstrumentResponse } from "./useCreateInstrument";
import { useUpdateInstrument } from "./useUpdateInstrument";
import { InstrumentForm, type InstrumentFormValues } from "./InstrumentForm";

interface Props {
  instrument: InstrumentResponse;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function InstrumentFormDialog({ instrument, open, onOpenChange }: Props) {
  const updateMut = useUpdateInstrument();

  const defaultValues: InstrumentFormValues = {
    name: instrument.name,
    instrument_type: instrument.instrument_type,
    base_currency: instrument.base_currency,
    price_source: instrument.price_source,
    risk_level: instrument.risk_level,
    ticker_override: instrument.ticker_override ?? "",
    display_decimals:
      instrument.display_decimals == null
        ? ""
        : String(instrument.display_decimals),
  };

  function handleSubmit(v: InstrumentFormValues) {
    const dd = v.display_decimals.trim();
    const trimmedTicker = v.ticker_override.trim();
    updateMut.mutate(
      {
        id: instrument.id,
        input: {
          // Edit-locked — re-send unchanged because PUT body is the full
          // InstrumentCreate schema, not a partial.
          symbol: instrument.symbol,
          name: v.name.trim(),
          instrument_type: v.instrument_type,
          base_currency: v.base_currency,
          price_source: v.price_source,
          risk_level: v.risk_level,
          ticker_override: trimmedTicker === "" ? undefined : trimmedTicker,
          display_decimals: dd === "" ? null : Number(dd),
        },
      },
      {
        onSuccess: (inst) => {
          toast.success(`Instrument "${inst.name}" updated.`);
          onOpenChange(false);
        },
        onError: (err) => {
          toast.error(`Could not update instrument. ${err.message}`, {
            duration: 6000,
          });
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit instrument</DialogTitle>
          <DialogDescription>
            Update this instrument&rsquo;s metadata. Symbol and ID are read-only.
          </DialogDescription>
        </DialogHeader>
        <InstrumentForm
          defaultValues={defaultValues}
          readOnly={{ id: instrument.id, symbol: instrument.symbol }}
          submitLabel="Save changes"
          isPending={updateMut.isPending}
          onSubmit={handleSubmit}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
