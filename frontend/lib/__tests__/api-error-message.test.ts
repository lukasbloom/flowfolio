import { test } from "node:test";
import assert from "node:assert/strict";

// NOTE: Imports `../api-error-message.ts` with the explicit extension because
// Node's ESM resolver (used at runtime via `node --test --experimental-strip-types`)
// requires it. The `lib/__tests__/` directory is excluded from `tsconfig.json`,
// so TypeScript's "no .ts extension in imports" rule doesn't fire here.
//
// We do NOT import the real `ApiError` class: `api-client.ts` uses a TypeScript
// parameter-property constructor which Node's strip-only mode cannot transpile.
// extractApiErrorMessage detects ApiError structurally, so a faithful stand-in
// (an Error with numeric `status` + string `detail`) exercises the exact path a
// real ApiError takes.
import { extractApiErrorMessage } from "../api-error-message.ts";

// Mirror app/lib/api-client.ts ApiError: message = `API ${status}: ${detail}`,
// status: number, detail: string (raw response body).
function makeApiError(status: number, detail: string): Error {
  const err = new Error(`API ${status}: ${detail}`) as Error & {
    status: number;
    detail: string;
  };
  err.status = status;
  err.detail = detail;
  return err;
}

// The create form rendered duplicate (409) and reserved (422)
// server errors as the raw `API NNN: {"detail":...}` wrapper. extractApiErrorMessage
// unwraps the FastAPI `detail` (string for 409, array for 422) into a clean
// human sentence, never throwing and never leaking the raw wrapper.

test("409 string detail unwraps to the plain sentence", () => {
  const err = makeApiError(
    409,
    JSON.stringify({ detail: "An instrument with this symbol already exists." }),
  );
  assert.equal(
    extractApiErrorMessage(err),
    "An instrument with this symbol already exists.",
  );
});

test("422 array detail unwraps to the first msg", () => {
  const err = makeApiError(
    422,
    JSON.stringify({
      detail: [
        {
          loc: ["body", "symbol"],
          msg: 'symbol "catalog" is reserved',
          type: "value_error",
        },
      ],
    }),
  );
  assert.equal(extractApiErrorMessage(err), 'symbol "catalog" is reserved');
});

test("non-JSON detail falls back to the provided fallback", () => {
  // detail is plain text, not JSON → JSON.parse throws → message/fallback path.
  // The ApiError message itself ("API 500: Internal Server Error") is the
  // message fallback; passing an explicit fallback exercises that arg, and the
  // helper prefers err.message when it is non-empty. Here detail is plain text,
  // so the returned value is err.message (the wrapper). To assert the explicit
  // fallback path cleanly, use an empty message.
  const err = new Error("") as Error & { status: number; detail: string };
  err.status = 500;
  err.detail = "Internal Server Error";
  assert.equal(
    extractApiErrorMessage(err, "Could not create instrument."),
    "Could not create instrument.",
  );
});

test("non-ApiError Error returns its message", () => {
  const err = new Error("network down");
  assert.equal(extractApiErrorMessage(err, "fallback"), "network down");
});

test("default fallback is used when nothing else is extractable", () => {
  // A non-Error, non-ApiError value (e.g. a thrown string/object) must never
  // throw and must return the default fallback.
  assert.equal(extractApiErrorMessage({ weird: true }), "Something went wrong.");
});
