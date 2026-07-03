import { test, expect } from "@playwright/test";
import { sanitizeHtml } from "../helpers/sanitizeHtml";
import { resetGoldenDb } from "../helpers/dbReset";

const FIXED_INSTANT = "2026-04-30T12:00:00Z";

test.describe("Pilot: snapshot harness pipeline proof", () => {
  test.beforeEach(async ({ page }) => {
    await resetGoldenDb();
    await page.clock.install({ time: FIXED_INSTANT });
  });

  test("Add Transaction picker renders a stable, sanitized snapshot", async ({ page }) => {
    // storageState provides auth; we go to /track and open the Add Transaction picker.
    await page.goto("/track");
    await expect(page).toHaveURL(/\/track/);

    // Click the Add Transaction CTA (aria-label="Add transaction" in the header).
    // The AddButton opens the AddTxnPicker which renders synchronously from props —
    // no API roundtrip, no chart-skeleton race. This is the
    // pipeline-proof anchor of choice.
    await page.getByRole("button", { name: "Add transaction" }).click();

    // Picker root carries role="menu" per AddTxnPicker.tsx — stable synchronous locator.
    const picker = page.getByRole("menu");
    await expect(picker).toBeVisible({ timeout: 2_000 });

    const raw = await picker.innerHTML();
    const cleaned = sanitizeHtml(raw);

    // Baseline file: tests/e2e/snapshots/__baselines__/pilot.snapshot.spec.ts/pilot-add-dialog.html
    expect(cleaned).toMatchSnapshot("pilot-add-dialog.html");
  });

  test("[bench] resetGoldenDb completes <= 200ms median over 10 trials", async () => {
    const samples: number[] = [];
    for (let i = 0; i < 10; i++) {
      const start = performance.now();
      await resetGoldenDb();
      samples.push(performance.now() - start);
    }
    samples.sort((a, b) => a - b);
    const median = samples[5];
    console.log(`[bench] resetGoldenDb samples (ms): ${samples.map(s => s.toFixed(1)).join(", ")} - median=${median.toFixed(1)}`);
    if (median > 200) {
      console.log("[bench] median exceeds 200ms — re-run with FLOWFOLIO_TEST_DB_RESET_DIRECT=1 to use the docker-exec fallback (see dbReset.ts).");
    }
    expect(median).toBeLessThanOrEqual(200);
  });
});
