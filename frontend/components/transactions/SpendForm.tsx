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
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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

const schema = z.object({
  account_id: z.string().min(1, "Required"),
  instrument_id: z.string().min(1, "Required"),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Invalid date"),
  quantity: decimalField({ positive: true }),
  unit_price: decimalField({ positive: true }),
  price_currency: z.enum(["EUR", "USD"]),
  fx_rate_to_eur: z.string().optional(),
  notes: z.string().max(500).optional(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  open: boolean;
  onClose: () => void;
  /** When set, the form runs in edit mode (PUT) against this txn id. */
  txnId?: string;
  /** Default values merged over the form's create-mode defaults. */
  initialValues?: Partial<FormValues>;
  /**
   * "sheet" (default) wraps the form in its own Sheet — used when SpendForm
   * is the sole drawer (e.g., the create entry point).
   * "inline" renders only the form so a parent can host it inside its own Sheet
   * (e.g., EditTxnDrawer hosting a spend edit, or AddTxnFormSheet).
   */
  chrome?: "sheet" | "inline";
  /**
   * Payload-rich so AddTxnFormSheet can render the Spend toast
   * `Spend added — ${amount} ${currency}`
   * (Spend has no instrument identity in the toast template — amount + currency is the
   * Spend-equivalent surface). `description` is forwarded for forward-compat.
   * Existing call sites that pass a no-arg callback continue to compile (TS allows ignoring args).
   */
  onSuccess?: (payload: { amount: string; currency: string; description?: string }) => void;
  /** When true, the inner `toast.success(...)` is skipped (wrapper owns the canonical toast). */
  suppressInnerToast?: boolean;
}

export function SpendForm({
  open,
  onClose,
  txnId,
  initialValues,
  chrome = "sheet",
  onSuccess,
  suppressInnerToast,
}: Props) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const isEdit = !!txnId;

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
      unit_price: initialValues?.unit_price ?? "",
      price_currency: initialValues?.price_currency ?? "EUR",
      fx_rate_to_eur: initialValues?.fx_rate_to_eur ?? "",
      notes: initialValues?.notes ?? "",
    },
  });

  const currency = form.watch("price_currency");
  const [createOpen, setCreateOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      const body = JSON.stringify({
        ...values,
        // Pydantic v2 cannot coerce "" into Optional[Decimal]; drop empties.
        fx_rate_to_eur:
          values.fx_rate_to_eur === "" ? undefined : values.fx_rate_to_eur,
        txn_type: "spend",
      });
      if (isEdit) {
        return apiFetch(`/api/transactions/${txnId}`, {
          method: "PUT",
          body,
        });
      }
      return apiFetch("/api/transactions", {
        method: "POST",
        body,
      });
    },
    onSuccess: (_data, values) => {
      const instrument = instruments.find(
        (i) => i.id === values.instrument_id
      );
      // Fall back to a generic word ("spend") instead of the raw UUID
      // when the instruments query is cold. The wrapper's canonical spend toast uses
      // amount + currency rather than symbol, so this only affects the suppressed
      // inner toast (Spend recorded/updated) for non-wrapper callers, but kept
      // consistent across the three form sites.
      const symbol = instrument?.symbol ?? "spend";
      if (!suppressInnerToast) {
        toast.success(
          isEdit
            ? `Spend updated — ${values.quantity} ${symbol}.`
            : `Spend recorded — ${values.quantity} ${symbol}.`
        );
      }
      invalidatePortfolioCache(qc);
      form.reset();
      onSuccess?.({
        amount: values.quantity,
        currency: values.price_currency,
        description: values.notes && values.notes.length > 0 ? values.notes : undefined,
      });
      onClose();
    },
    onError: (err: Error) =>
      toast.error(
        isEdit
          ? `Could not update spend. ${err.message}.`
          : `Could not record spend. ${err.message}.`,
        { duration: 6000 }
      ),
  });

  const formBody = (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
        className={
          chrome === "sheet"
            ? "mt-6 px-4 pb-4 grid grid-cols-1 items-start gap-4 md:grid-cols-2"
            : "grid grid-cols-1 items-start gap-4 md:grid-cols-2"
        }
      >
        <FormField
          control={form.control}
          name="account_id"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center gap-1.5">
                <FormLabel>Account</FormLabel>
                {isEdit && <LockedFieldHint text="Account can't be changed — delete and re-create to switch." />}
              </div>
              <Select onValueChange={field.onChange} value={field.value} disabled={isEdit}>
                <FormControl>
                  <SelectTrigger ref={field.ref}>
                    <SelectValue placeholder="Select account" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
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
                {isEdit && <LockedFieldHint text="Instrument can't be changed — delete and re-create to switch." />}
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
                disabled={isEdit}
              >
                <FormControl>
                  <SelectTrigger ref={field.ref}>
                    <SelectValue placeholder="Select instrument" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {instruments.map((i) => (
                    <SelectItem key={i.id} value={i.id}>
                      {i.symbol} — {i.name}
                    </SelectItem>
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
              <FormControl>
                <Input type="date" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="quantity"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Quantity spent</FormLabel>
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
              <FormLabel>Market price at spend</FormLabel>
              <FormControl>
                <Input inputMode="decimal" className="tabular-nums" {...field} />
              </FormControl>
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
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
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
          <FormField
            control={form.control}
            name="fx_rate_to_eur"
            render={({ field }) => (
              <FormItem className="md:col-span-2">
                <FormLabel>FX rate (USD per 1 EUR)</FormLabel>
                <FormControl>
                  <Input inputMode="decimal" className="tabular-nums" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        <FormField
          control={form.control}
          name="notes"
          render={({ field }) => (
            <FormItem className="md:col-span-2">
              <FormLabel>Notes</FormLabel>
              <FormControl>
                <Textarea maxLength={500} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <DialogFormFooter className="md:col-span-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending
              ? "Saving..."
              : isEdit
                ? "Save changes"
                : "Save spend"}
          </Button>
        </DialogFormFooter>
      </form>
    </Form>
  );

  const createInstrumentDialog = (
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
  );

  if (chrome === "inline") {
    // Parent owns the Sheet (and its title/description). Just render the form.
    if (!open) return null;
    return (
      <>
        {formBody}
        {createInstrumentDialog}
      </>
    );
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-[480px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{isEdit ? "Edit spend" : "Add spend"}</SheetTitle>
          <SheetDescription>
            Use this when you spend a holding (e.g., paying with crypto or
            stablecoins). Cost basis is consumed; no proceeds are tracked.
          </SheetDescription>
        </SheetHeader>

        {formBody}
        {createInstrumentDialog}
      </SheetContent>
    </Sheet>
  );
}
