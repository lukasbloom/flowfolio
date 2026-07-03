/**
 * Unwrap a FastAPI error response into a clean, human-readable sentence.
 *
 * An `ApiError` (from `./api-client`) carries `status: number` and `detail: string`,
 * where `detail` is the raw response body — a JSON string FastAPI shapes two ways:
 *   - 409 (and most HTTPExceptions): `{"detail": "An instrument ... exists."}`
 *   - 422 (request validation):      `{"detail": [{"loc": [...], "msg": "...", ...}]}`
 *
 * Without unwrapping, callers render `err.message`, which is the
 * `API NNN: {"detail":...}` wrapper from the ApiError constructor — leaking the
 * status code and raw JSON into the UI.
 *
 * We detect ApiError structurally (a numeric `status` + string `detail` + an
 * Error `message`) rather than via `instanceof`. The structural check matches a
 * real ApiError exactly, avoids a hard import of the `api-client` module (whose
 * TypeScript parameter-property constructor is not consumable by Node's
 * strip-only test runtime used for `lib/__tests__`), and is robust across module
 * boundaries.
 *
 * Returns:
 *   - the string `detail` for 409-style responses,
 *   - the first element's `msg` for 422 array-style responses,
 *   - `err.message` then `fallback` when the body is not JSON / shape misses,
 *   - the generic Error `message` (or `fallback`) for non-ApiError errors,
 *   - the `fallback` for anything else.
 *
 * Never throws; never returns the raw `API NNN: {...}` wrapper when a `detail`
 * is extractable.
 */
export function extractApiErrorMessage(
  err: unknown,
  fallback = "Something went wrong.",
): string {
  if (isApiErrorLike(err)) {
    try {
      const parsed = JSON.parse(err.detail) as { detail?: unknown };
      const detail = parsed?.detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail;
      }
      if (Array.isArray(detail) && detail.length > 0) {
        const msg = (detail[0] as { msg?: unknown })?.msg;
        if (typeof msg === "string" && msg.trim()) {
          return msg;
        }
      }
    } catch {
      // Body was not JSON — fall through to the message/fallback below.
    }
    return err.message || fallback;
  }
  if (err instanceof Error) {
    return err.message || fallback;
  }
  return fallback;
}

interface ApiErrorLike {
  status: number;
  detail: string;
  message: string;
}

function isApiErrorLike(err: unknown): err is ApiErrorLike {
  return (
    err instanceof Error &&
    typeof (err as { status?: unknown }).status === "number" &&
    typeof (err as { detail?: unknown }).detail === "string"
  );
}
