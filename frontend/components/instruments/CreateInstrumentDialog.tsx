"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CreateInstrumentForm } from "./CreateInstrumentForm";
import type { InstrumentResponse } from "./useCreateInstrument";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (instrument: InstrumentResponse) => void;
}

export function CreateInstrumentDialog({ open, onOpenChange, onCreated }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add instrument</DialogTitle>
          <DialogDescription>
            Add a stock, ETF, fund, crypto, or stablecoin you hold. You can edit
            details later from the instrument detail page.
          </DialogDescription>
        </DialogHeader>
        <CreateInstrumentForm
          onSuccess={(instrument) => {
            onCreated(instrument);
            onOpenChange(false);
          }}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
