// Run via: npx tsx frontend/tests/e2e/helpers/sanitizeHtml.test.ts
import { sanitizeHtml } from "./sanitizeHtml";
import assert from "node:assert";

const samples: Array<[string, (out: string) => void]> = [
  // 1. Radix ID stripped
  [`<div id="radix-:r3:" data-state="open">hi</div>`,
    out => { assert.ok(!out.includes("radix-:r3:"), "radix id leaked"); assert.ok(out.includes(`data-state="open"`), "data-state=open dropped"); }],
  // 2. React auto-id stripped
  [`<span id=":r4:">x</span>`,
    out => assert.ok(!out.includes(":r4:"), "react auto-id leaked")],
  // 3. aria-controls stripped (ID ref)
  [`<button aria-controls="radix-:r5:" aria-label="Close">X</button>`,
    out => { assert.ok(!out.includes("aria-controls"), "aria-controls leaked"); assert.ok(out.includes(`aria-label="Close"`), "aria-label dropped"); }],
  // 4. role preserved
  [`<div role="dialog">...</div>`,
    out => assert.ok(out.includes(`role="dialog"`), "role dropped")],
  // 5. mid-animation data-state stripped (keep open/closed)
  [`<div data-state="opening"></div><div data-state="closed"></div>`,
    out => { assert.ok(!out.includes(`data-state="opening"`), "mid-animation leaked"); assert.ok(out.includes(`data-state="closed"`), "steady state dropped"); }],
  // 6. idempotent
  [`<div id="radix-:r3:">hi</div>`,
    out => assert.strictEqual(sanitizeHtml(out), out, "not idempotent")],
];

for (const [input, check] of samples) {
  const out = sanitizeHtml(input);
  check(out);
}
console.log("PASS: sanitizeHtml unit checks");
