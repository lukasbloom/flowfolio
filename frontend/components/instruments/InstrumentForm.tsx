"use client";

/**
 * InstrumentForm — presentation-only edit form for an Instrument.
 *
 * Mirrors `components/settings/AccountForm.tsx`: controlled useState (no
 * react-hook-form), no mutation, no toast. The owning dialog
 * (`InstrumentFormDialog`) handles the PUT call, success toast, and
 * close-on-success.
 *
 * Cross-field rule: when the user changes `instrument_type`, the
 * `price_source` Select is narrowed to `allowedSourcesFor(type)` and the
 * current `price_source` is reset to the first allowed token if it falls
 * outside the new set. This mirrors the backend's
 * `model_validator(_validate_type_source_combo)` so a UI submit never
 * trips a 422.
 *
 * Edit-locked: `id` and `symbol` are rendered as muted read-only text —
 * NOT Input-bound — because changing them affects price-provider
 * resolution (Finnhub/CoinGecko ticker mapping) and requires a SQL
 * migration, not a form submit.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AUTOMATIC_SOURCE_BY_TYPE,
  INSTRUMENT_TYPES,
  type InstrumentType,
  type PriceSource,
} from "@/lib/instrument-eligibility";
import { instrumentTypeLabel, priceSourceLabel } from "@/lib/format";

const RISK_LEVELS = ["High", "Medium", "Low", "Liquid"] as const;

/**
 * Mirror of backend `allowed_sources_for(instrument_type)` in
 * `backend/app/services/instrument_pricing.py`. Kept inline (no import
 * from there obviously, and we don't add another shared helper for a
 * three-branch rule) — `AUTOMATIC_SOURCE_BY_TYPE` is the single source
 * of truth for the per-type automatic provider; the manual rule layers
 * on top: cash → {na}, metal → {manual}, else {auto, manual}.
 */
function allowedSourcesFor(type: string): PriceSource[] {
  if (type === "cash") return ["na"];
  if (type === "metal") return ["manual"];
  const auto = AUTOMATIC_SOURCE_BY_TYPE[type as InstrumentType] ?? null;
  return auto ? [auto, "manual"] : ["manual"];
}

export interface InstrumentFormValues {
  name: string;
  instrument_type: string;
  base_currency: string;
  price_source: string;
  risk_level: string;
  ticker_override: string;
  display_decimals: string;
}

export interface InstrumentFormProps {
  defaultValues: InstrumentFormValues;
  readOnly: { id: string; symbol: string };
  submitLabel: string;
  pendingLabel?: string;
  isPending: boolean;
  onSubmit: (values: InstrumentFormValues) => void;
  onCancel: () => void;
}

export function InstrumentForm({
  defaultValues,
  readOnly,
  submitLabel,
  pendingLabel = "Saving…",
  isPending,
  onSubmit,
  onCancel,
}: InstrumentFormProps) {
  const [values, setValues] = useState<InstrumentFormValues>(defaultValues);
  const [touched, setTouched] = useState<{
    name: boolean;
    display_decimals: boolean;
  }>({ name: false, display_decimals: false });

  const allowedSources = allowedSourcesFor(values.instrument_type);
  const typeChanged = values.instrument_type !== defaultValues.instrument_type;

  const nameTrimmed = values.name.trim();
  const nameError =
    touched.name && nameTrimmed === "" ? "Name is required." : null;

  // display_decimals: empty → inherit; otherwise integer in [0, 12].
  const ddRaw = values.display_decimals.trim();
  const ddIsValid =
    ddRaw === "" ||
    (/^\d+$/.test(ddRaw) &&
      Number.parseInt(ddRaw, 10) >= 0 &&
      Number.parseInt(ddRaw, 10) <= 12);
  const ddError =
    touched.display_decimals && !ddIsValid
      ? "Must be a whole number between 0 and 12 (or blank to inherit)."
      : null;

  const canSubmit = nameTrimmed !== "" && ddIsValid && !isPending;

  function handleTypeChange(nextType: string) {
    setValues((v) => {
      const nextAllowed = allowedSourcesFor(nextType);
      const nextSource = nextAllowed.includes(v.price_source as PriceSource)
        ? v.price_source
        : nextAllowed[0];
      return { ...v, instrument_type: nextType, price_source: nextSource };
    });
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched({ name: true, display_decimals: true });
        if (!canSubmit) return;
        onSubmit(values);
      }}
    >
      {/* Edit-locked: id + symbol shown as read-only text. */}
      <div className="space-y-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">
            Symbol
          </span>
          <span className="font-mono">{readOnly.symbol}</span>
          <span className="text-xs uppercase tracking-wide text-muted-foreground">
            ID
          </span>
          <span className="font-mono text-xs">{readOnly.id}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Read-only — symbol changes affect price provider mapping
          (Finnhub/CoinGecko ticker resolution); ask the dev to migrate via
          SQL if you really need to change it.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="instr-name">Name</Label>
        <Input
          id="instr-name"
          value={values.name}
          onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
          onBlur={() => setTouched((t) => ({ ...t, name: true }))}
          placeholder="Display name"
          autoFocus
        />
        {nameError && <p className="text-xs text-destructive">{nameError}</p>}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="instr-type">Type</Label>
          <Select
            value={values.instrument_type}
            onValueChange={handleTypeChange}
          >
            <SelectTrigger id="instr-type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {INSTRUMENT_TYPES.map((t) => (
                <SelectItem key={t} value={t}>
                  {instrumentTypeLabel(t)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="instr-source">Price source</Label>
          <Select
            value={values.price_source}
            onValueChange={(v) =>
              setValues((vv) => ({ ...vv, price_source: v }))
            }
          >
            <SelectTrigger id="instr-source">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {allowedSources.map((src) => (
                <SelectItem key={src} value={src}>
                  {priceSourceLabel(src)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="instr-currency">Base currency</Label>
          <Select
            value={values.base_currency}
            onValueChange={(v) =>
              setValues((vv) => ({ ...vv, base_currency: v }))
            }
          >
            <SelectTrigger id="instr-currency">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="EUR">EUR</SelectItem>
              <SelectItem value="USD">USD</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="instr-risk">Risk level</Label>
          <Select
            value={values.risk_level}
            onValueChange={(v) =>
              setValues((vv) => ({ ...vv, risk_level: v }))
            }
          >
            <SelectTrigger id="instr-risk">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RISK_LEVELS.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {typeChanged ? (
        <p className="text-xs text-muted-foreground">
          Changing the type may have reset your price source. Confirm before
          saving.
        </p>
      ) : null}

      <div className="space-y-2">
        <Label htmlFor="instr-ticker">Ticker override</Label>
        <Input
          id="instr-ticker"
          value={values.ticker_override}
          onChange={(e) =>
            setValues((v) => ({ ...v, ticker_override: e.target.value }))
          }
          placeholder="Optional — used when the symbol doesn't match the provider"
          maxLength={64}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="instr-decimals">Display decimals</Label>
        <Input
          id="instr-decimals"
          type="number"
          min={0}
          max={12}
          step={1}
          value={values.display_decimals}
          onChange={(e) =>
            setValues((v) => ({ ...v, display_decimals: e.target.value }))
          }
          onBlur={() => setTouched((t) => ({ ...t, display_decimals: true }))}
          placeholder="Inherit (default for type)"
        />
        {ddError && <p className="text-xs text-destructive">{ddError}</p>}
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isPending}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={!canSubmit}>
          {isPending ? pendingLabel : submitLabel}
        </Button>
      </div>
    </form>
  );
}
