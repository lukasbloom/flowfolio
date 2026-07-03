"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, type UseFormReturn } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus } from "lucide-react";
import { toast } from "sonner";

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
import { Textarea } from "@/components/ui/textarea";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DialogFormFooter } from "@/components/transactions/DialogFormFooter";
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

const legSchema = z.object({
  account_id: z.string().min(1, "Required"),
  instrument_id: z.string().min(1, "Required"),
  quantity: decimalField({ positive: true }),
  unit_price: decimalField({ positive: true }),
  price_currency: z.enum(["EUR", "USD"]),
  fx_rate_to_eur: z.string().optional(),
  // fee_eur REMOVED — now top-level on the outer schema
});

const schema = z
  .object({
    sold: legSchema,
    received: legSchema,
    date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Invalid date"),
    fee_eur: z.string().optional(), // NEW — top-level
    notes: z.string().max(500).optional(),
  })
  .refine(
    (data) => data.sold.instrument_id !== data.received.instrument_id,
    {
      message: "Sold and received instruments must differ.",
      path: ["received", "instrument_id"],
    }
  );

type FormValues = z.infer<typeof schema>;

// ---------------------------------------------------------------------------
// Edit-mode discriminated union for the dual-PUT outcome.
// ---------------------------------------------------------------------------

type LegPayload = {
  date: string;
  quantity: string;
  unit_price: string;
  price_currency: "EUR" | "USD";
  fx_rate_to_eur: string | null;
  fee_eur: string;
  notes: string | null;
};

type EditMutationResult =
  | { kind: "both_ok"; sold: unknown; received: unknown }
  | { kind: "sold_ok_received_failed"; sold: unknown; receivedError: Error }
  | { kind: "sold_failed_received_ok"; received: unknown; soldError: Error }
  | { kind: "both_failed"; soldError: Error; receivedError: Error };

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  open: boolean;
  onClose: () => void;
  /**
   * "sheet" (default) wraps the form in its own Sheet — used when TradeForm
   * is the sole drawer (current edit/create entry points).
   * "inline" renders only the form body so a parent can host it inside its
   * own Dialog (used by AddTxnFormSheet in the universal +Add flow and by
   * EditTxnDialog in edit mode).
   */
  chrome?: "sheet" | "inline";
  // Payload-rich so AddTxnFormSheet can render the Trade toast template
  // `Trade added — ${sold_qty} ${sold_symbol} → ${received_qty} ${received_symbol}`.
  onSuccess?: (payload: {
    sold_qty: string;
    sold_symbol: string;
    received_qty: string;
    received_symbol: string;
  }) => void;
  suppressInnerToast?: boolean;

  // Edit mode.
  // txnId is the SOLD-leg transaction id; the received-leg id is carried in
  // initialValues.receivedLegId. When txnId is set, the form hydrates from
  // initialValues and submits via two PUT /api/transactions/:id calls
  // (one per leg) so per-leg failures surface individually.
  txnId?: string;
  initialValues?: {
    sold: {
      account_id: string;
      instrument_id: string;
      quantity: string;
      unit_price: string;
      price_currency: "EUR" | "USD";
      fx_rate_to_eur: string;
    };
    received: {
      account_id: string;
      instrument_id: string;
      quantity: string;
      unit_price: string;
      price_currency: "EUR" | "USD";
      fx_rate_to_eur: string;
    };
    date: string;
    fee_eur: string;
    notes: string;
    soldLegId: string;
    receivedLegId: string;
  };
}

// ---------------------------------------------------------------------------
// LegFields sub-component
// ---------------------------------------------------------------------------

function LegFields({
  prefix,
  accounts,
  instruments,
  form,
  onRequestCreate,
  disabled,
}: {
  prefix: "sold" | "received";
  accounts: Account[];
  instruments: Instrument[];
  form: UseFormReturn<FormValues>;
  onRequestCreate: () => void;
  disabled?: boolean;
}) {
  const currency = form.watch(`${prefix}.price_currency`);

  return (
    <div className="space-y-4">
      <FormField
        control={form.control}
        name={`${prefix}.account_id`}
        render={({ field }) => (
          <FormItem>
            <FormLabel>Account</FormLabel>
            <Select
              onValueChange={field.onChange}
              value={field.value}
              disabled={disabled}
            >
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
        name={`${prefix}.instrument_id`}
        render={({ field }) => (
          <FormItem>
            <FormLabel>Instrument</FormLabel>
            <Select
              onValueChange={(v) => {
                if (disabled) return;
                if (v === CREATE_NEW_INSTRUMENT) {
                  onRequestCreate();
                  return;
                }
                field.onChange(v);
              }}
              value={field.value}
              disabled={disabled}
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
                {!disabled && (
                  <SelectItem value={CREATE_NEW_INSTRUMENT}>
                    <span className="flex items-center gap-2 text-muted-foreground">
                      <Plus className="size-3.5" aria-hidden /> New instrument…
                    </span>
                  </SelectItem>
                )}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )}
      />

      <FormField
        control={form.control}
        name={`${prefix}.quantity`}
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
                    form.setValue(`${prefix}.quantity`, formatQuantity(raw), {
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
        name={`${prefix}.unit_price`}
        render={({ field }) => (
          <FormItem>
            <FormLabel>Unit price</FormLabel>
            <FormControl>
              <Input inputMode="decimal" className="tabular-nums" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      <FormField
        control={form.control}
        name={`${prefix}.price_currency`}
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
          name={`${prefix}.fx_rate_to_eur`}
          render={({ field }) => (
            <FormItem>
              <FormLabel>FX rate (USD per 1 EUR)</FormLabel>
              <FormControl>
                <Input inputMode="decimal" className="tabular-nums" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TradeForm({
  open,
  onClose,
  chrome = "sheet",
  onSuccess,
  suppressInnerToast,
  txnId,
  initialValues,
}: Props) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const [createOpenFor, setCreateOpenFor] = useState<"sold" | "received" | null>(null);

  const isEditMode = !!txnId && !!initialValues;

  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/api/accounts"),
  });
  const { data: instruments = [] } = useQuery({
    queryKey: ["instruments"],
    queryFn: () => apiFetch<Instrument[]>("/api/instruments"),
  });

  // Hydrate from initialValues when in edit mode; blank defaults for create mode.
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: initialValues
      ? {
          sold: {
            account_id: initialValues.sold.account_id,
            instrument_id: initialValues.sold.instrument_id,
            quantity: formatQuantity(initialValues.sold.quantity),
            unit_price: initialValues.sold.unit_price,
            price_currency: initialValues.sold.price_currency,
            fx_rate_to_eur: initialValues.sold.fx_rate_to_eur,
          },
          received: {
            account_id: initialValues.received.account_id,
            instrument_id: initialValues.received.instrument_id,
            quantity: formatQuantity(initialValues.received.quantity),
            unit_price: initialValues.received.unit_price,
            price_currency: initialValues.received.price_currency,
            fx_rate_to_eur: initialValues.received.fx_rate_to_eur,
          },
          date: initialValues.date,
          fee_eur: initialValues.fee_eur,
          notes: initialValues.notes,
        }
      : {
          sold: {
            account_id: "",
            instrument_id: "",
            quantity: "",
            unit_price: "",
            price_currency: "EUR",
            fx_rate_to_eur: "",
          },
          received: {
            account_id: "",
            instrument_id: "",
            quantity: "",
            unit_price: "",
            price_currency: "EUR",
            fx_rate_to_eur: "",
          },
          date: today,
          fee_eur: "",
          notes: "",
        },
  });

  const mutation = useMutation<EditMutationResult | unknown, Error, FormValues>({
    mutationFn: async (values: FormValues) => {
      if (isEditMode) {
        // EDIT MODE — two parallel PUTs against the two existing leg IDs.
        // Per-leg outcomes observable (not coalesced) — one failure doesn't cancel the other.
        const soldBody: LegPayload = {
          date: values.date,
          quantity: values.sold.quantity,
          unit_price: values.sold.unit_price,
          price_currency: values.sold.price_currency,
          fx_rate_to_eur: values.sold.fx_rate_to_eur || null,
          fee_eur: values.fee_eur || "0", // collapsed fee lands on the sold leg
          notes: values.notes || null,
        };
        const receivedBody: LegPayload = {
          date: values.date,
          quantity: values.received.quantity,
          unit_price: values.received.unit_price,
          price_currency: values.received.price_currency,
          fx_rate_to_eur: values.received.fx_rate_to_eur || null,
          fee_eur: "0", // received leg gets zero fee
          notes: values.notes || null,
        };

        const [soldResult, receivedResult] = await Promise.allSettled([
          apiFetch(`/api/transactions/${initialValues.soldLegId}`, {
            method: "PUT",
            body: JSON.stringify(soldBody),
          }),
          apiFetch(`/api/transactions/${initialValues.receivedLegId}`, {
            method: "PUT",
            body: JSON.stringify(receivedBody),
          }),
        ]);

        const soldOk = soldResult.status === "fulfilled";
        const receivedOk = receivedResult.status === "fulfilled";

        if (soldOk && receivedOk) {
          return {
            kind: "both_ok",
            sold: soldResult.value,
            received: receivedResult.value,
          } as EditMutationResult;
        }
        if (soldOk && !receivedOk) {
          return {
            kind: "sold_ok_received_failed",
            sold: soldResult.value,
            receivedError: receivedResult.reason as Error,
          } as EditMutationResult;
        }
        if (!soldOk && receivedOk) {
          return {
            kind: "sold_failed_received_ok",
            received: receivedResult.value,
            soldError: soldResult.reason as Error,
          } as EditMutationResult;
        }
        return {
          kind: "both_failed",
          soldError: (soldResult as PromiseRejectedResult).reason as Error,
          receivedError: (receivedResult as PromiseRejectedResult).reason as Error,
        } as EditMutationResult;
      }

      // CREATE MODE — existing POST /api/trades path, unchanged.
      return apiFetch("/api/trades", {
        method: "POST",
        body: JSON.stringify({
          sold: {
            account_id: values.sold.account_id,
            instrument_id: values.sold.instrument_id,
            quantity: values.sold.quantity,
            unit_price: values.sold.unit_price,
            price_currency: values.sold.price_currency,
            fx_rate_to_eur: values.sold.fx_rate_to_eur || null,
            fee_eur: values.fee_eur || null, // top-level fee lands on the sold leg
          },
          received: {
            account_id: values.received.account_id,
            instrument_id: values.received.instrument_id,
            quantity: values.received.quantity,
            unit_price: values.received.unit_price,
            price_currency: values.received.price_currency,
            fx_rate_to_eur: values.received.fx_rate_to_eur || null,
            fee_eur: null, // receiver leg gets null
          },
          date: values.date,
          notes: values.notes || null,
        }),
      });
    },
    onSuccess: (data, values) => {
      const soldInstrument = instruments.find((i) => i.id === values.sold.instrument_id);
      const receivedInstrument = instruments.find((i) => i.id === values.received.instrument_id);

      // Fall back to a contextual word ("sold"/"received") instead of the
      // raw UUID when the instruments query is cold.
      const soldSymbol = soldInstrument?.symbol ?? "sold";
      const receivedSymbol = receivedInstrument?.symbol ?? "received";

      // Detect edit-mode result (only the edit path returns the discriminated union;
      // create path returns the raw /api/trades response).
      const isEditResult =
        typeof data === "object" &&
        data !== null &&
        "kind" in (data as Record<string, unknown>);

      if (isEditResult) {
        // EDIT MODE — emit per-leg outcome toasts.
        // Gate success side-effects on actual outcome. On both_failed,
        // no server state changed, so skip invalidation and keep the drawer open
        // so the user can retry. On partial-success, server state DID change for
        // the saved leg, so invalidate caches — but still keep the drawer open
        // so the user can retry the failed leg without losing context.
        const result = data as EditMutationResult;

        if (result.kind === "both_failed") {
          // Surface BOTH leg errors (previously only soldError was shown).
          toast.error(
            `Could not save trade. Sold: ${result.soldError.message}. ` +
              `Received: ${result.receivedError.message}.`,
          );
          // Do NOT invalidate, do NOT call onSuccess, do NOT close — let user retry.
          return;
        }

        if (result.kind === "sold_ok_received_failed") {
          toast.error("Sold leg saved; received leg failed — retry the received leg.");
        } else if (result.kind === "sold_failed_received_ok") {
          toast.error("Received leg saved; sold leg failed — retry the sold leg.");
        } else if (result.kind === "both_ok") {
          if (!suppressInnerToast) {
            toast.success(
              `Trade updated — ${values.sold.quantity} ${soldSymbol} → ${values.received.quantity} ${receivedSymbol}`,
            );
          }
        }

        // Partial-success and both_ok: invalidate caches because at least one
        // leg's server state changed.
        invalidatePortfolioCache(qc);

        if (result.kind === "both_ok") {
          // Only full success closes the drawer and notifies the parent.
          // Partial-success keeps the drawer open so the user can retry the
          // failing leg without losing field context.
          onSuccess?.({
            sold_qty: values.sold.quantity,
            sold_symbol: soldSymbol,
            received_qty: values.received.quantity,
            received_symbol: receivedSymbol,
          });
          onClose();
        }
        return;
      }

      // CREATE MODE — original behavior.
      if (!suppressInnerToast) {
        toast.success(`Trade recorded — ${soldSymbol} → ${receivedSymbol}.`);
      }
      form.reset();

      invalidatePortfolioCache(qc);

      onSuccess?.({
        sold_qty: values.sold.quantity,
        sold_symbol: soldSymbol,
        received_qty: values.received.quantity,
        received_symbol: receivedSymbol,
      });
      onClose();
    },
    onError: (err: Error) => {
      // This path is hit only when the mutationFn itself throws (network error
      // before either PUT reaches the server). The dual-PUT edit path never throws —
      // it converts rejections into the discriminated union above.
      toast.error(`Could not save. ${err.message}`);
    },
  });

  const formBody = (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((v) => mutation.mutate(v))}
        className={
          chrome === "sheet"
            ? "mt-6 px-4 pb-4 space-y-4"
            : "space-y-4"
        }
      >
        <div className="space-y-4">
          {/* Row 1: Date (full-width, above the two-leg grid) */}
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

          {/* Row 2: Two-leg grid */}
          <div className="grid grid-cols-1 items-start md:grid-cols-2 gap-4">
            <fieldset className="space-y-3">
              <legend className="text-sm font-medium">Sold</legend>
              <LegFields
                prefix="sold"
                accounts={accounts}
                instruments={instruments}
                form={form}
                onRequestCreate={() => setCreateOpenFor("sold")}
                disabled={isEditMode}
              />
            </fieldset>
            <fieldset className="space-y-3">
              <legend className="text-sm font-medium">Received</legend>
              <LegFields
                prefix="received"
                accounts={accounts}
                instruments={instruments}
                form={form}
                onRequestCreate={() => setCreateOpenFor("received")}
                disabled={isEditMode}
              />
            </fieldset>
          </div>

          {/* Row 3: Single EUR-denominated fee (below the two-leg grid, full-width) */}
          <FormField
            control={form.control}
            name="fee_eur"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Fee (EUR)</FormLabel>
                <FormControl>
                  <Input {...field} inputMode="decimal" className="tabular-nums" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          {/* Row 4: Notes (full-width) */}
          <FormField
            control={form.control}
            name="notes"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Notes</FormLabel>
                <FormControl>
                  <Textarea {...field} maxLength={500} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>

        <DialogFormFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending
              ? "Saving..."
              : isEditMode
                ? "Save changes"
                : "Save trade"}
          </Button>
        </DialogFormFooter>
      </form>
    </Form>
  );

  const createInstrumentDialog = (
    <CreateInstrumentDialog
      open={createOpenFor !== null}
      onOpenChange={(o) => {
        if (!o) setCreateOpenFor(null);
      }}
      onCreated={(instrument) => {
        if (createOpenFor === "sold") {
          form.setValue("sold.instrument_id", instrument.id, {
            shouldDirty: true,
            shouldValidate: true,
          });
        } else if (createOpenFor === "received") {
          form.setValue("received.instrument_id", instrument.id, {
            shouldDirty: true,
            shouldValidate: true,
          });
        }
      }}
    />
  );

  if (chrome === "inline") {
    // Parent owns the Dialog (and its title/description). Just render the form.
    if (!open) return null;
    return (
      <>
        {formBody}
        {createInstrumentDialog}
      </>
    );
  }

  return (
    <TooltipProvider>
      <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-[640px] overflow-y-auto"
        >
          <SheetHeader>
            <SheetTitle>Add trade</SheetTitle>
            <SheetDescription>
              Sells must be paired with what you received. The cost basis flows
              from the asset sold to the asset received.
            </SheetDescription>
          </SheetHeader>

          {formBody}
          {createInstrumentDialog}
        </SheetContent>
      </Sheet>
    </TooltipProvider>
  );
}
