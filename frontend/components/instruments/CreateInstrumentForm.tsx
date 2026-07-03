"use client";

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";

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
import { apiFetch } from "@/lib/api-client";
import { extractApiErrorMessage } from "@/lib/api-error-message";
import { decimalField } from "@/lib/decimal-strings";
import { instrumentTypeLabel } from "@/lib/format";
import { invalidatePortfolioCache } from "@/lib/invalidate-cache";
import {
  INSTRUMENT_TYPES,
  type InstrumentType,
  type PriceSource,
  automaticSourceFor,
  priceModeOptionsFor,
  resolvePriceSource,
} from "@/lib/instrument-eligibility";
import {
  useCreateInstrument,
  type InstrumentResponse,
} from "./useCreateInstrument";

// Type labels reuse lib/format.instrumentTypeLabel (identical map).
//
// PROVIDER_LABEL is kept local — it is NOT identical to
// lib/format.priceSourceLabel: this map renders `na` as "N/A" whereas
// priceSourceLabel renders it as "None". Unifying would change the displayed
// string, so the local map stays (behavior-preserving).
const PROVIDER_LABEL: Record<PriceSource, string> = {
  finnhub: "Finnhub",
  coingecko: "CoinGecko",
  ft: "FT.com",
  manual: "Manual",
  na: "N/A",
};

function providerLabel(source: PriceSource | null): string {
  if (source === null) return "Manual";
  return PROVIDER_LABEL[source];
}

const schema = z.object({
  symbol: z.string().trim().min(1, "Required").max(32),
  name: z.string().trim().min(1, "Required").max(120),
  instrument_type: z.enum(INSTRUMENT_TYPES),
  base_currency: z.string().trim().min(1, "Required").max(8),
  price_mode: z.enum(["automatic", "manual"]),
  // Optional inline first NAV/price for manual-priced instruments.
  // Empty ("") means "create the instrument without writing a price" —
  // preserving the prior single-step behavior. A filled value is validated
  // as a Decimal string via the same `decimalField` the ManualNavForm uses
  // (never Number()-coerced), then written through POST /api/prices/manual
  // on create success.
  initial_price: decimalField({
    positive: true,
    message: "Use a positive number, e.g. 13.00",
  })
    .optional()
    .or(z.literal("")),
  ticker_override: z.string().trim().max(64).optional().or(z.literal("")),
  // Held as a string in form state ("" means "inherit per-type default")
  // and coerced to a Number on submit; the inline refine bounds the
  // numeric value to [0, 12] without forcing zod's coerce union into
  // the resolver type signature.
  display_decimals: z
    .string()
    .trim()
    .max(3)
    .refine(
      (v) => {
        if (v === "") return true;
        const n = Number(v);
        return Number.isInteger(n) && n >= 0 && n <= 12;
      },
      { message: "Must be an integer between 0 and 12" },
    )
    .optional(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  onSuccess: (instrument: InstrumentResponse) => void;
  onCancel: () => void;
}

export function CreateInstrumentForm({ onSuccess, onCancel }: Props) {
  const [serverError, setServerError] = useState<string | null>(null);
  const mutation = useCreateInstrument();
  const qc = useQueryClient();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      symbol: "",
      name: "",
      instrument_type: "stock",
      base_currency: "EUR",
      price_mode: "automatic",
      initial_price: "",
      ticker_override: "",
      display_decimals: "",
    },
  });

  // Normalise the price_mode whenever the type switches. cash → forced
  // automatic ("na"); metal → forced manual; everything else snaps back
  // to "automatic" so the user never carries a stale "manual" pick into
  // a type that might be priced live (e.g. switching crypto → stock
  // shouldn't silently keep "manual" selected).
  const selectedType = form.watch("instrument_type");
  useEffect(() => {
    const opts = priceModeOptionsFor(selectedType as InstrumentType);
    if (opts.automatic) {
      form.setValue("price_mode", "automatic");
    } else if (opts.manual) {
      form.setValue("price_mode", "manual");
    }
  }, [selectedType, form]);

  function handleSubmit(values: FormValues) {
    setServerError(null);
    let price_source: PriceSource;
    try {
      price_source = resolvePriceSource(
        values.instrument_type as InstrumentType,
        values.price_mode,
      );
    } catch (e) {
      // Defensive — the toggle should make this unreachable. Surface
      // inline rather than silently swallowing.
      setServerError(
        e instanceof Error ? e.message : "Could not resolve price source.",
      );
      return;
    }
    const ddRaw = values.display_decimals;
    const display_decimals =
      ddRaw === "" || ddRaw == null ? undefined : Number(ddRaw);
    const body = {
      symbol: values.symbol,
      name: values.name,
      instrument_type: values.instrument_type,
      base_currency: values.base_currency,
      price_source,
      ticker_override:
        values.ticker_override && values.ticker_override !== ""
          ? values.ticker_override
          : undefined,
      display_decimals,
    };
    // When the instrument is manual-priced AND the inline NAV field
    // is filled, write the first price in the same submit — reusing the exact
    // ManualNavForm path (POST /api/prices/manual). The price write fires
    // BEFORE the parent onSuccess(data) so the new NAV is persisted (and the
    // portfolio cache invalidated) before the dialog closes / auto-selects.
    const initialPrice =
      values.price_mode === "manual" && values.initial_price
        ? values.initial_price
        : null;

    mutation.mutate(body, {
      onSuccess: async (data) => {
        if (initialPrice !== null) {
          try {
            await apiFetch("/api/prices/manual", {
              method: "POST",
              body: JSON.stringify({
                instrument_id: data.id,
                date: new Date().toISOString().slice(0, 10),
                price: initialPrice,
                currency: values.base_currency,
                note: null,
              }),
            });
            // Mirror ManualNavForm's post-write invalidation so the new NAV
            // is reflected: portfolio superset + instrument-scoped history.
            qc.invalidateQueries({ queryKey: ["nav-history", data.id] });
            invalidatePortfolioCache(qc);
          } catch (err) {
            // The instrument WAS created; surface the price-write failure
            // inline via the same path used for the create call. Do not
            // close the dialog — the user can retry the NAV.
            setServerError(
              err instanceof Error
                ? err.message
                : "Instrument created, but the price could not be saved.",
            );
            return;
          }
        }
        onSuccess(data);
      },
      onError: (err) => {
        // Unwrap the FastAPI detail (string for 409 duplicate, array for 422
        // reserved-keyword) into a clean inline sentence instead of the raw
        // `API NNN: {"detail":...}` wrapper. Covers the
        // 5xx/network path too — extractApiErrorMessage falls back to the
        // Error message / the provided fallback.
        setServerError(extractApiErrorMessage(err, "Could not create instrument."));
      },
    });
  }

  // Pre-compute the toggle state for the currently selected type so the
  // render block stays focused on layout.
  const typeForToggle = (selectedType ?? "stock") as InstrumentType;
  const modeOptions = priceModeOptionsFor(typeForToggle);
  const autoSource = automaticSourceFor(typeForToggle);
  const showToggle = modeOptions.automatic && modeOptions.manual;
  // The inline NAV field shows only for manual-priced instruments
  // (EU funds / metal / any manual source). Watch price_mode so the field
  // appears/disappears as the user toggles pricing.
  const priceMode = form.watch("price_mode");
  const baseCurrencyValue = form.watch("base_currency");
  const showInlineNav = priceMode === "manual";

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleSubmit)}
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
      >
        <FormField
          control={form.control}
          name="symbol"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Symbol</FormLabel>
              <FormControl>
                <Input autoFocus maxLength={32} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input maxLength={120} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="instrument_type"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Type</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {INSTRUMENT_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {instrumentTypeLabel(t)}
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
          name="base_currency"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Base currency</FormLabel>
              <FormControl>
                <Input maxLength={8} placeholder="EUR" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="price_mode"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Pricing</FormLabel>
              {showToggle ? (
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={field.value === "automatic" ? "default" : "outline"}
                    onClick={() => field.onChange("automatic")}
                  >
                    Automatic ({providerLabel(autoSource)})
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={field.value === "manual" ? "default" : "outline"}
                    onClick={() => field.onChange("manual")}
                  >
                    Manual
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {typeForToggle === "cash"
                    ? "Cash holdings have no price (quantity in base currency)."
                    : "Metal prices must be entered manually."}
                </p>
              )}
              <FormMessage />
            </FormItem>
          )}
        />
        {showInlineNav ? (
          <FormField
            control={form.control}
            name="initial_price"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  Initial NAV / price (optional)
                  {baseCurrencyValue ? ` (${baseCurrencyValue})` : ""}
                </FormLabel>
                <FormControl>
                  <Input
                    inputMode="decimal"
                    placeholder="13.00"
                    className="tabular-nums"
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ) : null}
        <FormField
          control={form.control}
          name="ticker_override"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Ticker override (optional)</FormLabel>
              <FormControl>
                <Input
                  maxLength={64}
                  placeholder="Override symbol used by price provider (rare)"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="display_decimals"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Display decimals (optional)</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  min={0}
                  max={12}
                  step={1}
                  placeholder="Inherit (default for type)"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {serverError !== null ? (
          <p className="md:col-span-2 text-xs text-destructive">{serverError}</p>
        ) : null}

        <div className="md:col-span-2 flex gap-2">
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving..." : "Save instrument"}
          </Button>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Form>
  );
}
