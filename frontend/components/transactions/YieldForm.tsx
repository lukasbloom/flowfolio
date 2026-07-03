"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DialogFormFooter } from "@/components/transactions/DialogFormFooter";
import { LockedFieldHint } from "@/components/transactions/LockedFieldHint";
import { CreateInstrumentDialog } from "@/components/instruments/CreateInstrumentDialog";
import { CREATE_NEW_INSTRUMENT } from "@/components/instruments/sentinel";
import { apiFetch } from "@/lib/api-client";
import { decimalField } from "@/lib/decimal-strings";
import { formatQuantity } from "@/lib/format";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";

interface Account {
  id: string;
  name: string;
}
interface Instrument {
  id: string;
  symbol: string;
  name: string;
}

// Strict subset of TxnForm schema — drops txn_type, unit_price, price_currency, fx_rate_to_eur, fee_eur.
// Friendly error messages.
const schema = z.object({
  account_id: z.string().min(1, "Pick an account"),
  instrument_id: z.string().min(1, "Pick an instrument"),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Invalid date"),
  quantity: decimalField({ positive: true }),
  notes: z.string().max(500, "Max 500 characters").optional(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  txnId?: string;                    // present → edit mode (PUT); absent → create mode (POST)
  initialValues?: Partial<FormValues>;
  hideTypeSelect?: boolean;          // no-op for yield (always single type) — accepted for API parity with TxnForm
  suppressInnerToast?: boolean;      // when true, skip inner toast; wrapper (AddTxnFormSheet) owns the canonical toast
  onSuccess?: (payload: { qty: string; symbol: string }) => void;
  onCancel?: () => void;
}

export function YieldForm({
  txnId,
  initialValues,
  hideTypeSelect: _hideTypeSelect,
  suppressInnerToast,
  onSuccess,
  onCancel,
}: Props) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });
  const { data: instruments = [] } = useQuery({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments"),
  });

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      account_id: initialValues?.account_id ?? "",
      instrument_id: initialValues?.instrument_id ?? "",
      date: initialValues?.date ?? today,
      quantity: initialValues?.quantity
        ? formatQuantity(initialValues.quantity)
        : "",
      notes: initialValues?.notes ?? "",
    },
  });

  const [createOpen, setCreateOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const body = {
        account_id: values.account_id,
        instrument_id: values.instrument_id,
        txn_type: "yield",
        date: values.date,
        quantity: values.quantity,
        notes: values.notes || null,
      };
      if (txnId) {
        return apiFetch(`/api/transactions/${txnId}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      }
      return apiFetch("/api/transactions", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: (_data, values) => {
      // Toast template.
      // Literal em-dash U+2014 with spaces. Verb: 'added' for create, 'updated' for edit.
      const instrument = instruments.find((i) => i.id === values.instrument_id);
      const symbol = instrument?.symbol ?? "yield";
      if (!suppressInnerToast) {
        toast.success(
          txnId
            ? `Yield updated — ${values.quantity} ${symbol}`
            : `Yield added — ${values.quantity} ${symbol}`,
        );
      }
      // Full 9-key cache invalidation + holdings — replicate verbatim from EditTxnDrawer.tsx:51-63 + holdings.
      invalidatePortfolioCache(qc);
      onSuccess?.({ qty: values.quantity, symbol });
    },
    onError: (err: Error) => toast.error(`Could not save. ${err.message}`),
  });

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
        className="grid grid-cols-1 items-start gap-4 md:grid-cols-2"
      >
        {/* Field order: Account → Instrument → Date → Quantity → Notes */}
        <FormField
          control={form.control}
          name="account_id"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center gap-1.5">
                <FormLabel>Account</FormLabel>
                {txnId && <LockedFieldHint text="Account can't be changed — delete and re-create to switch." />}
              </div>
              <Select onValueChange={field.onChange} value={field.value} disabled={!!txnId}>
                <FormControl>
                  <SelectTrigger ref={field.ref}><SelectValue placeholder="Select account" /></SelectTrigger>
                </FormControl>
                <SelectContent>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="instrument_id"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center gap-1.5">
                <FormLabel>Instrument</FormLabel>
                {txnId && <LockedFieldHint text="Instrument can't be changed — delete and re-create to switch." />}
              </div>
              <Select
                onValueChange={(v) => {
                  if (v === CREATE_NEW_INSTRUMENT) {
                    setCreateOpen(true);
                    return;
                  }
                  field.onChange(v);
                }}
                value={field.value}
                disabled={!!txnId}
              >
                <FormControl>
                  <SelectTrigger ref={field.ref}><SelectValue placeholder="Select instrument" /></SelectTrigger>
                </FormControl>
                <SelectContent>
                  {instruments.map((i) => (
                    <SelectItem key={i.id} value={i.id}>{i.symbol} — {i.name}</SelectItem>
                  ))}
                  <SelectItem value={CREATE_NEW_INSTRUMENT}>
                    <span className="flex items-center gap-2 text-muted-foreground">
                      <Plus className="size-3.5" aria-hidden /> New instrument…
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="date"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Date</FormLabel>
              <FormControl><Input type="date" {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="quantity"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Quantity</FormLabel>
              <FormControl>
                <Input
                  inputMode="decimal"
                  className="tabular-nums"
                  {...field}
                  onBlur={(e) => {
                    field.onBlur();
                    const raw = e.target.value.trim();
                    if (raw !== "") {
                      form.setValue("quantity", formatQuantity(raw), {
                        shouldDirty: true,
                      });
                    }
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="notes"
          render={({ field }) => (
            <FormItem className="md:col-span-2">
              <FormLabel>Notes</FormLabel>
              <FormControl>
                <Textarea maxLength={500} {...field} value={field.value ?? ""} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <DialogFormFooter className="md:col-span-2">
          {onCancel && (
            <Button type="button" variant="outline" onClick={onCancel}>
              Cancel
            </Button>
          )}
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending
              ? "Saving..."
              : txnId
                ? "Save changes"
                : "Save transaction"}
          </Button>
        </DialogFormFooter>
      </form>
      <CreateInstrumentDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(instrument) => {
          form.setValue("instrument_id", instrument.id, {
            shouldDirty: true,
            shouldValidate: true,
          });
        }}
      />
    </Form>
  );
}
