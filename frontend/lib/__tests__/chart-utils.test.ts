import { test } from "node:test";
import assert from "node:assert/strict";

// Mirrors lib/__tests__/holdings-aggregation.test.ts: explicit .ts extension
// (lib/__tests__ is excluded from tsconfig.json), run via
// `node --test --experimental-strip-types`.
import {
  CHART_FONT_FAMILY,
  mutedAxisLabel,
  timeXAxis,
  tooltipShell,
  valueYAxis,
} from "../chart-utils.ts";

// Literal values mirror components/charts/palette.ts (ACCENT/MUTED/BORDER)
// and the CHART_FONT_FAMILY stack in chart-utils.ts. Freezing them here as
// plain strings (rather than importing the source constants) pins the actual
// rendered values the four charts share.
const ACCENT = "#262626";
const MUTED = "#737373";
const BORDER = "#E5E5E5";
const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

test("CHART_FONT_FAMILY pins the shared chart font stack", () => {
  assert.equal(CHART_FONT_FAMILY, FONT);
});

test("tooltipShell(axis) matches the shared tooltip container shape", () => {
  assert.deepEqual(tooltipShell("axis"), {
    trigger: "axis",
    backgroundColor: "#FFFFFF",
    borderColor: BORDER,
    borderWidth: 1,
    textStyle: { color: ACCENT, fontSize: 14, fontFamily: FONT },
    padding: [8, 12],
  });
});

test("tooltipShell(item) only differs in trigger", () => {
  assert.deepEqual(tooltipShell("item"), {
    trigger: "item",
    backgroundColor: "#FFFFFF",
    borderColor: BORDER,
    borderWidth: 1,
    textStyle: { color: ACCENT, fontSize: 14, fontFamily: FONT },
    padding: [8, 12],
  });
});

test("mutedAxisLabel() with no formatter omits the formatter key entirely", () => {
  const label = mutedAxisLabel();
  assert.deepEqual(label, { color: MUTED, fontSize: 12 });
  assert.ok(!("formatter" in label));
});

test("mutedAxisLabel(formatter) carries the same formatter reference", () => {
  const fmt = (v: number) => String(v);
  const label = mutedAxisLabel(fmt);
  assert.deepEqual(label, { color: MUTED, fontSize: 12, formatter: fmt });
});

test("valueYAxis(formatter) matches the shared value-yAxis shape", () => {
  const fmt = (v: number) => `${v}`;
  assert.deepEqual(valueYAxis(fmt), {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: MUTED, fontSize: 12, formatter: fmt },
    splitLine: { lineStyle: { color: BORDER, type: "dashed" } },
  });
});

test("timeXAxis matches the shared time-xAxis shape", () => {
  const fmt = (v: number) => `${v}`;
  assert.deepEqual(timeXAxis({ minInterval: 2419200000, formatter: fmt }), {
    type: "time",
    minInterval: 2419200000,
    axisLine: { lineStyle: { color: BORDER } },
    axisTick: { show: false },
    axisLabel: { color: MUTED, fontSize: 12, formatter: fmt, hideOverlap: true, interval: "auto" },
    splitLine: { show: false },
  });
});

test("timeXAxis omits minInterval when not given (single tick density for shorter timeframes)", () => {
  const fmt = (v: number) => `${v}`;
  const axis = timeXAxis({ formatter: fmt });
  assert.equal(axis.minInterval, undefined);
});
