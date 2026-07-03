import { format } from "date-fns";
import { enGB } from "date-fns/locale";

const FORMATTERS: Record<string, Intl.NumberFormat> = {};

/**
 * Format a [from, to] date pair as a single subtitle string for the chart
 * headings. En-dash separator with surrounding spaces.
 *
 * Example: formatDateRange(new Date("2026-01-15"), new Date("2026-02-14"))
 *          → "15 Jan 2026 – 14 Feb 2026"
 */
export function formatDateRange(from: Date, to: Date): string {
  const fmt = (d: Date) => format(d, "dd MMM yyyy", { locale: enGB });
  return `${fmt(from)} – ${fmt(to)}`;
}

/**
 * Format an ISO date string as "dd MMM yyyy" (e.g. "05 Jan 2026"), en-GB locale.
 *
 * Canonical date-only formatter. NOTE: the three pre-existing hand-rolled date
 * formatters (AuditHistoryModal, LastClosePriceCell, NavHistoryTab) were
 * intentionally NOT migrated onto this helper — their outputs differ from each
 * other and from this format (AuditHistoryModal uses an UNPADDED day via
 * Intl `day:"numeric"`; NavHistoryTab uses option-less `toLocaleString`), so
 * folding them in would change rendered strings byte-for-byte. Use this helper
 * for NEW date renders.
 */
export function formatDate(iso: string): string {
  return format(new Date(iso), "dd MMM yyyy", { locale: enGB });
}

/**
 * Format an ISO datetime string as "dd MMM yyyy, HH:mm", en-GB locale.
 * Companion to {@link formatDate}; same byte-identity caveat applies to the
 * pre-existing copies (see formatDate note).
 */
export function formatDateTime(iso: string): string {
  return format(new Date(iso), "dd MMM yyyy, HH:mm", { locale: enGB });
}

export function formatMoney(value: string | number, currency: "EUR" | "USD"): string {
  // When the value arrives as a Decimal string from
  // the API, hand the STRING straight to Intl.NumberFormat (which formats
  // string inputs without an IEEE-754 round-trip) so magnitudes beyond
  // Number.MAX_SAFE_INTEGER survive verbatim — e.g. "9007199254740993.99"
  // renders ".99", not the ".00" that `Number(value)` would round it to.
  // Numbers pass through unchanged. This extends the same sign-from-the-string
  // discipline already used by formatSignedMoney.
  //
  // The cast to `Intl.StringNumericLiteral` is the sanctioned way to feed a
  // numeric string to Intl.NumberFormat.format: the runtime accepts decimal
  // strings (that's exactly what preserves precision), but TS's lib types only
  // expose the branded StringNumericLiteral overload for string inputs. A
  // plain `number` also satisfies that overload, so the union is safe.
  const input = value as Intl.StringNumericLiteral;
  if (currency === "USD") {
    // Bare "$" PREFIX with en-GB separators, matches EUR
    // (which already prefixes via Intl style:"currency" → "€1,234.56") and
    // every USD UI surface (KPI strip, charts, transaction list). We still
    // avoid Intl.NumberFormat's "US$1,234.56" output because readers parse
    // it as the ISO currency code rather than the dollar symbol.
    // Cache the USD formatter under a sentinel key (style is decimal, not the
    // EUR "currency" style) so it is not rebuilt on every call. Output unchanged.
    if (!FORMATTERS.__usd_plain) {
      FORMATTERS.__usd_plain = new Intl.NumberFormat("en-GB", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    }
    return `$${FORMATTERS.__usd_plain.format(input)}`;
  }
  if (!FORMATTERS[currency]) {
    FORMATTERS[currency] = new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  return FORMATTERS[currency].format(input);
}

const QUANTITY_FORMATTERS: Record<number, Intl.NumberFormat> = {};

export function formatQuantity(value: string | number, maxDigits = 8): string {
  const num = typeof value === "string" ? Number(value) : value;
  // Non-numeric input (e.g. the user typed letters, or a partial "1,5") parses
  // to NaN, which Intl would render as the literal string "NaN". Return the raw
  // input untouched instead so form validation surfaces the real error rather
  // than the field silently becoming "NaN".
  if (!Number.isFinite(num)) return typeof value === "string" ? value : "";
  // Cache the Intl.NumberFormat per maxDigits (0-8). Output is unchanged — only
  // the formatter instance is memoized to avoid rebuilding it on every call.
  let fmt = QUANTITY_FORMATTERS[maxDigits];
  if (!fmt) {
    fmt = new Intl.NumberFormat("en-GB", {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxDigits,
    });
    QUANTITY_FORMATTERS[maxDigits] = fmt;
  }
  return fmt.format(num);
}

/**
 * Format a signed money value: "+ €1.23" for positive, "− €1.23" for negative
 * (figure-dash, matching en-GB signDisplay), and the bare formatted value for
 * zero. The leading minus is stripped from the *string* form (NOT via
 * Math.abs) so Decimal precision from the API survives for large values.
 *
 * Consolidates KpiStrip.formatRealizedValue, RealizedCell's inline signing, and
 * InstrumentKpiBlock.SignedMoney. Callers handle the null case themselves (each
 * renders its own "—" placeholder).
 */
export function formatSignedMoney(
  value: string | number,
  currency: "EUR" | "USD"
): string {
  const num = typeof value === "string" ? Number(value) : value;
  const s = String(value);
  const isNeg = num < 0;
  const absStr = isNeg ? s.replace(/^-/, "") : s;
  const formatted = formatMoney(absStr, currency);
  if (num > 0) return `+ ${formatted}`;
  if (isNeg) return `− ${formatted}`;
  return formatted;
}

/**
 * Directional Tailwind color token for a signed value:
 *   > 0 → "text-positive"; < 0 → "text-negative"; 0 / null → "text-muted-foreground".
 *
 * Consolidates the inline ternaries across KpiStrip, PercentCell, RealizedCell,
 * and InstrumentKpiBlock. NOTE: RealizedCell previously used "text-destructive"
 * for negatives — unified here on "text-negative" (the established token used by
 * every other site), the one intentional visual change.
 */
export function directionalColor(value: string | number | null): string {
  if (value === null) return "text-muted-foreground";
  const num = typeof value === "string" ? Number(value) : value;
  if (num > 0) return "text-positive";
  if (num < 0) return "text-negative";
  return "text-muted-foreground";
}

/**
 * Per-type quantity-decimal defaults — chosen to match how the holding
 * is naturally read:
 *   - stocks/ETFs/funds: 4dp (brokers issue fractional shares)
 *   - crypto: 8dp (BTC sats)
 *   - stablecoin / cash: 2dp (currency-like)
 *   - metal: 4dp (grams / ounces)
 *
 * Per-instrument override (Instrument.display_decimals, backend column
 * added in Alembic 0005) beats this table; this table beats the legacy
 * 8-decimal fallback that formatQuantity uses when no context is given.
 */
export const DEFAULT_DECIMALS_BY_TYPE: Record<string, number> = {
  stock: 4,
  etf: 4,
  fund: 4,
  crypto: 8,
  stablecoin: 2,
  cash: 2,
  metal: 4,
};

/**
 * Resolve the maximum-fraction-digits for a quantity render.
 *
 * Precedence: explicit per-instrument override → per-type default → 8 fallback.
 */
export function decimalsFor(opts: {
  instrumentType?: string | null;
  displayDecimals?: number | null;
}): number {
  if (opts.displayDecimals != null) return opts.displayDecimals;
  if (
    opts.instrumentType &&
    DEFAULT_DECIMALS_BY_TYPE[opts.instrumentType] != null
  ) {
    return DEFAULT_DECIMALS_BY_TYPE[opts.instrumentType];
  }
  return 8; // legacy fallback — preserves today's behavior for callers without context
}

export function formatPercent(value: string | number, opts?: { signed?: boolean }): string {
  const num = typeof value === "string" ? Number(value) : value;
  const signed = opts?.signed ?? false;
  return new Intl.NumberFormat("en-GB", {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: signed ? "exceptZero" : "auto",
  })
    .format(num)
    .replace(/\u00a0%$/, " %")
    .replace(/\u202f%$/, " %");
}

/**
 * Format the age of a fetched_at timestamp as "{H}h {M}m".
 * Pass an explicit `now` (epoch ms) so callers stay pure during render.
 * If `now` is omitted, uses Date.now() for non-render contexts only.
 */
export function formatRelativeHours(fetchedAt: string, now?: number): string {
  const reference = now ?? Date.now();
  const ms = reference - new Date(fetchedAt).getTime();
  const hours = Math.floor(ms / (3600 * 1000));
  const minutes = Math.floor((ms % (3600 * 1000)) / 60000);
  return `${hours}h ${minutes}m`;
}

export const STALE_MS = 48 * 3600 * 1000;

function formatNumberEs(n: number, maxDigits: number): string {
  return new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxDigits,
  }).format(n);
}

/**
 * Format a money value as a compact string for chart axis labels.
 *
 * Uses locale-neutral K/M abbreviations to avoid the Spanish "mil" / English "million"
 * 1000x misread risk. Number separators stay en-GB (period decimal,
 * comma thousands) to match the rest of the app.
 *
 * Currency symbol PREFIXES the number so chart-tick labels
 * read the same way as headline KPIs ("€12.5K", "$1.2M") rather than the
 * previous "12.5K €" / "1.2M $" suffix layout.
 *
 * Examples:
 *   425       -> "€425"
 *   1234      -> "€1,234"    (en-GB thousand separator)
 *   12500     -> "€12.5K"    (en-GB decimal point)
 *   1234567   -> "€1.2M"
 */
export function formatCompactMoney(
  value: string | number,
  currency: "EUR" | "USD"
): string {
  const num = typeof value === "string" ? Number(value) : value;
  // Bare "$" (not "US$") for chart-tick brevity.
  const symbol = currency === "EUR" ? "€" : "$";
  const abs = Math.abs(num);
  if (abs >= 1_000_000) {
    return `${symbol}${formatNumberEs(num / 1_000_000, 1)}M`;
  }
  if (abs >= 1_000) {
    return `${symbol}${formatNumberEs(num / 1_000, 1)}K`;
  }
  return `${symbol}${formatNumberEs(num, 0)}`;
}

/**
 * Humanize a raw price-source enum value for display (UX-C3 / UX-M3).
 *
 * Backend column `instruments.price_source` stores lowercase enum values
 * (`finnhub`, `coingecko`, `ft`, `manual`, `na`). Rendering them with CSS
 * `capitalize` produces "Na" / "Ft" / "Coingecko" — meaningless or wrong.
 * Mirrors the SOURCE_LABEL pattern at FxHistoryTable.tsx:30-33.
 * Unknown values pass through unchanged.
 */
const PRICE_SOURCE_LABEL: Record<string, string> = {
  finnhub: "Finnhub",
  coingecko: "CoinGecko",
  ft: "FT.com",
  manual: "Manual",
  na: "None",
  // FX source label, folded in so FxHistoryTable reuses this map (its former
  // local SOURCE_LABEL mapped frankfurter→"Frankfurter", manual→"Manual" —
  // both identical here).
  frankfurter: "Frankfurter",
};
export function priceSourceLabel(src: string): string {
  return PRICE_SOURCE_LABEL[src] ?? src;
}

/**
 * Humanize a raw instrument-type enum value for display (UX-M3).
 *
 * Backend column `instruments.instrument_type` stores lowercase enum values
 * (`stock`, `etf`, `fund`, `crypto`, `stablecoin`, `cash`, `metal`). CSS
 * `capitalize` produces "Etf" — wrong for an acronym. Mirrors the
 * priceSourceLabel pattern. Unknown values pass through capitalized.
 */
const INSTRUMENT_TYPE_LABEL: Record<string, string> = {
  stock: "Stock",
  etf: "ETF",
  fund: "Fund",
  crypto: "Crypto",
  stablecoin: "Stablecoin",
  cash: "Cash",
  metal: "Metal",
};
export function instrumentTypeLabel(t: string): string {
  return (
    INSTRUMENT_TYPE_LABEL[t] ?? t.charAt(0).toUpperCase() + t.slice(1)
  );
}
