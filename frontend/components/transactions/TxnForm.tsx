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
import { FxRateField } from "@/components/transactions/FxRateField";
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

const schema = z.object({
  account_id: z.string().min(1, "Pick an account"),
  instrument_id: z.string().min(1, "Pick an instrument"),
  txn_type: z.enum(["buy", "sell"]),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Invalid date"),
  quantity: decimalField({ positive: true }),
  unit_price: decimalField({ positive: true }),
  price_currency: z.enum(["EUR", "USD"]),
  fx_rate_to_eur: z.string().optional(),
  fee_eur: z.string().optional(),
  notes: z.string().max(500, "Max 500 characters").optional(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  initialValues?: Partial<FormValues>;
  txnId?: string; // present → edit mode (PUT)
  // Widened from () => void to payload-bearing so AddTxnFormSheet can render the
  // rich toast template `${TypeLabel} added — ${qty} ${symbol}`.
  // Existing EditTxnDrawer call site uses a no-arg arrow — TS allows ignoring args.
  onSuccess?: (payload: { qty: string; symbol: string }) => void;
  hideTypeSelect?: boolean;       // wrapper sets true to hide the txn_type FormField
  onCancel?: () => void;           // when set, render Cancel button (variant='outline') alongside Save
  suppressInnerToast?: boolean;    // when true, skip the inner toast.success(); wrapper owns the canonical toast
}

export function TxnForm({ initialValues, txnId, onSuccess, hideTypeSelect, onCancel, suppressInnerToast }: Props) {
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
      txn_type: initialValues?.txn_type ?? "buy",
      date: initialValues?.date ?? today,
      quantity: initialValues?.quantity
        ? formatQuantity(initialValues.quantity)
        : "",
      unit_price: initialValues?.unit_price ?? "",
      price_currency: initialValues?.price_currency ?? "EUR",
      fx_rate_to_eur: initialValues?.fx_rate_to_eur ?? "",
      fee_eur: initialValues?.fee_eur ?? "0",
      notes: initialValues?.notes ?? "",
    },
  });

  const currency = form.watch("price_currency");
  const txnDate = form.watch("date");
  const [createOpen, setCreateOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const body: Record<string, unknown> = {
        account_id: values.account_id,
        instrument_id: values.instrument_id,
        txn_type: values.txn_type,
        date: values.date,
        quantity: values.quantity,
        unit_price: values.unit_price,
        price_currency: values.price_currency,
        fee_eur: values.fee_eur || "0",
        notes: values.notes || null,
      };
      if (values.price_currency === "USD" && values.fx_rate_to_eur) {
        // EUR-base rate (USD per 1 EUR) — backend cost basis: price_USD / fx_rate_to_eur
        body.fx_rate_to_eur = values.fx_rate_to_eur;
      }
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
      if (!suppressInnerToast) {
        toast.success(txnId ? "Changes saved." : "Transaction added.");
      }
      // Match the invalidation surface used by SpendForm / YieldForm /
      // TradeForm. A buy or sell created here can close a holding (`closed`),
      // changes realized totals (`realized`), and may breach the concentration
      // threshold (`concentration`); the perf/networth/allocation/contributions
      // caches also depend on this data.
      invalidatePortfolioCache(qc);
      // Resolve symbol from instruments cache; fall back to a generic word
      // ("transaction") instead of the raw UUID when the instruments query is cold
      // (slow network, fresh tab, or post-eviction race during submit). A 36-char UUID
      // in the canonical toast is the worst possible UX defense — a short generic word
      // preserves the rich-toast format and reads
      // coherently. The Select itself blocks picking an unknown instrument, so this
      // path only fires on submit-time races.
      const instrument = instruments.find((i) => i.id === values.instrument_id);
      const symbol = instrument?.symbol ?? "transaction";
      // Blank the form after a successful create so the universal +Add
      // picker → BuyForm path doesn't retain stale values across submissions.
      // Edit-mode keeps the values intentionally — `txnId` truthy means edit.
      if (!txnId) form.reset();
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
        {!hideTypeSelect && (
          <FormField
            control={form.control}
            name="txn_type"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-center gap-1.5">
                  <FormLabel>Type</FormLabel>
                  {txnId && <LockedFieldHint text="Type can't be changed — delete and re-create to switch." />}
                </div>
                <Select onValueChange={field.onChange} value={field.value} disabled={!!txnId}>
                  <FormControl>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectItem value="buy">Buy</SelectItem>
                    <SelectItem value="sell">Sell</SelectItem>
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
        )}
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
                  {accounts.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
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
          name="unit_price"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Unit price</FormLabel>
              <FormControl><Input inputMode="decimal" className="tabular-nums" {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="price_currency"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Currency</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="EUR">EUR</SelectItem>
                  <SelectItem value="USD">USD</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        {currency === "USD" && (
          <div className="md:col-span-2">
            <FxRateField
              txnDate={txnDate}
              value={form.watch("fx_rate_to_eur") ?? ""}
              onChange={(rate) => form.setValue("fx_rate_to_eur", rate, { shouldDirty: true })}
            />
          </div>
        )}
        <FormField
          control={form.control}
          name="fee_eur"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Fee (EUR)</FormLabel>
              <FormControl><Input inputMode="decimal" className="tabular-nums" {...field} /></FormControl>
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
              <FormControl><Textarea maxLength={500} {...field} /></FormControl>
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
