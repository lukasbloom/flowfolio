/**
 * Eligibility predicates for instrument detail tabs.
 *
 * Used by `frontend/app/instruments/[id]/InstrumentTabs.tsx` to gate
 * which TabsTrigger + TabsContent slots render based on the instrument's
 * `price_source` and `instrument_type`. Centralized here so future callers
 * (instrument list badges, admin audit views) can reuse the same rules.
 *
 * Backend remains permissive — these predicates exist purely to remove
 * affordances that would otherwise encourage data corruption (e.g. saving
 * a manual NAV row against a Finnhub-priced stock would silently override
 * the live price; configuring APY against a non-yield-eligible instrument
 * is a no-op the user shouldn't see).
 */

// Canonical tuples — kept in sync with frontend/components/instruments/CreateInstrumentForm.tsx
// Duplicated intentionally to avoid a circular import; the form may
// import from here in a follow-up.
export const INSTRUMENT_TYPES = [
  "stock",
  "etf",
  "fund",
  "crypto",
  "stablecoin",
  "cash",
  "metal",
] as const;

export const PRICE_SOURCES = [
  "finnhub",
  "coingecko",
  "ft",
  "manual",
  "na",
] as const;

export type InstrumentType = (typeof INSTRUMENT_TYPES)[number];
export type PriceSource = (typeof PRICE_SOURCES)[number];

/**
 * Loose shape — accepts the raw API payload without forcing callers to
 * narrow `instrument_type` / `price_source` to the canonical unions first.
 */
export interface InstrumentLike {
  instrument_type: string;
  price_source: string;
}

/**
 * NAV history is meaningful only when the price source is human-curated
 * (`manual`) or scraped via FT.com (`ft`, where a manual NAV row is the
 * documented fallback when scraping fails). Live API sources (`finnhub`,
 * `coingecko`) must NOT accept manual NAV rows because they would silently
 * override the scheduler-fetched price. `na` instruments have no price at
 * all — manual NAV doesn't help them either.
 */
export function canHaveManualNav(instrument: InstrumentLike): boolean {
  return instrument.price_source === "manual" || instrument.price_source === "ft";
}

/**
 * APY config is meaningful only for instruments with a yield-bearing
 * mechanism that matches the daily-accrual model: cash earns interest,
 * stablecoins typically yield, crypto may be staked. Stocks/ETFs/funds/
 * metals do not.
 */
export function canHaveApy(instrument: InstrumentLike): boolean {
  return (
    instrument.instrument_type === "cash" ||
    instrument.instrument_type === "stablecoin" ||
    instrument.instrument_type === "crypto"
  );
}

/**
 * Mirror of backend AUTOMATIC_SOURCE_BY_TYPE
 * (`backend/app/services/instrument_pricing.py`). The create form derives
 * `price_source` from (type, mode) using this table so the user never
 * picks an incompatible (type, source) combo. Keep the two in sync —
 * the backend's `model_validator` will reject any drift at the API
 * boundary as a 422.
 */
export const AUTOMATIC_SOURCE_BY_TYPE: Record<InstrumentType, PriceSource | null> = {
  stock: "finnhub",
  etf: "finnhub",
  fund: "ft",
  crypto: "coingecko",
  stablecoin: "coingecko",
  cash: "na", // cash has no manual mode
  metal: null, // metal has no automatic mode
};

export function automaticSourceFor(type: InstrumentType): PriceSource | null {
  return AUTOMATIC_SOURCE_BY_TYPE[type] ?? null;
}

/**
 * What the create-form pricing toggle should expose for a given type:
 *   - stock/etf/fund/crypto/stablecoin → both options visible
 *   - cash → toggle hidden, source forced to "na"
 *   - metal → toggle hidden, source forced to "manual"
 */
export function priceModeOptionsFor(type: InstrumentType): {
  automatic: boolean;
  manual: boolean;
} {
  if (type === "cash") return { automatic: true, manual: false };
  if (type === "metal") return { automatic: false, manual: true };
  return { automatic: true, manual: true };
}

/**
 * Frontend mirror of backend `resolve_price_source`. Used on create-form
 * submit to convert (type, mode) into the wire-format `price_source`
 * string the backend persists. Throws on (cash, manual) and
 * (metal, automatic) — both of which are also rejected by the toggle UI.
 */
export function resolvePriceSource(
  type: InstrumentType,
  mode: "automatic" | "manual",
): PriceSource {
  if (mode === "automatic") {
    const auto = AUTOMATIC_SOURCE_BY_TYPE[type];
    if (auto === null) {
      throw new Error(`${type} has no automatic price source`);
    }
    return auto;
  }
  if (type === "cash") {
    throw new Error("cash has no manual mode");
  }
  return "manual";
}
