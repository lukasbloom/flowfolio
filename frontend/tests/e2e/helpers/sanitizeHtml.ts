import { html_beautify } from "js-beautify";

/**
 * Normalize transient/auto-generated markup so HTML snapshots are stable across runs.
 *
 * Strips:
 *  - Radix-generated IDs (e.g., id="radix-:r3:", id="radix-_r_25_")
 *  - React colon-format auto-generated IDs (id=":rN:")
 *  - React 19 / react-hook-form useId() IDs in _r_N_ format (id="_r_71_-form-item")
 *  - Combined Radix+React19 IDs (id="radix-_r_N_"), added after
 *    counter values drifted when a new fixture row increased the React component tree.
 *  - Corresponding `for` attributes referencing _r_N_ IDs (label for="...")
 *  - aria-controls / aria-labelledby / aria-describedby ID *references* (entire attr)
 *  - Mid-animation data-state values (anything other than steady "open" / "closed")
 *
 * Preserves (a11y contracts):
 *  - role attributes
 *  - aria-label text values
 *  - The text CONTENT of nodes that other nodes reference via aria-describedby/labelledby
 *
 * Output is pretty-printed via js-beautify for stable, line-diffable storage.
 */
export function sanitizeHtml(rawInnerHtml: string): string {
  let cleaned = rawInnerHtml;

  // 1. Strip Radix-generated IDs: id="radix-:r3:" or id="radix-:rxyz:"
  //    Also handles combined Radix+React19 format: id="radix-_r_25_".
  //    The regex covers both the colon-prefix and the underscore-prefix variants.
  cleaned = cleaned.replace(/\s+id="radix-:[^"]+"/g, "");
  cleaned = cleaned.replace(/\s+id="radix-_r_[^"]+"/g, "");

  // 2. Strip React colon-format auto-generated IDs: id=":r3:" (concurrent mode)
  cleaned = cleaned.replace(/\s+id=":r[^"]+"/g, "");

  // 3. Strip React 19 / react-hook-form useId() IDs: id="_r_N_-form-item" or id="_r_abc_"
  //    These are React's server-safe useId() format; the counter changes across renders.
  cleaned = cleaned.replace(/\s+id="_r_[^"]+"/g, "");

  // 4. Strip `for` attributes referencing _r_N_ or :rN: IDs (label htmlFor links).
  cleaned = cleaned.replace(/\s+for="(_r_[^"]+|:r[^"]+)"/g, "");

  // 5. Strip aria-controls / labelledby / describedby ID REFERENCES (entire attr)
  cleaned = cleaned.replace(/\s+aria-(controls|labelledby|describedby)="[^"]*"/g, "");

  // 6. Strip contents of Radix hidden-select accessibility shims.
  //    Radix combobox renders a `<select aria-hidden="true">` that mirrors visible
  //    options as native `<option>` elements (for AT compatibility). The options
  //    populate asynchronously via API calls and cause timing-dependent diffs.
  //    We keep the `<select>` element itself but clear its children — the visible
  //    combobox trigger button is what the snapshot should verify.
  cleaned = cleaned.replace(
    /(<select\s[^>]*aria-hidden="true"[^>]*>)\s*<\/select>/g,
    "$1</select>"
  );
  cleaned = cleaned.replace(
    /(<select\s[^>]*aria-hidden="true"[^>]*>)[\s\S]*?(<\/select>)/g,
    "$1$2"
  );

  // 7. Strip mid-animation data-state values (keep steady open/closed)
  cleaned = cleaned.replace(/\s+data-state="(?!open"|closed")[^"]*"/g, "");

  // 8. Strip ECharts / echarts-for-react auto-generated IDs.
  //    _echarts_instance_ is a timestamp-derived attribute injected by ECharts on the
  //    chart root div (e.g., _echarts_instance_="ec_1747123456789") — non-deterministic.
  //    data-zr-dom-id is injected on canvas elements by ZRender (ECharts' rendering engine).
  //    size-sensor-id is a sequential counter injected by echarts-for-react's ResizeObserver
  //    helper — non-deterministic across test runs depending on chart mount order.
  cleaned = cleaned.replace(/\s+_echarts_instance_="[^"]+"/g, "");
  cleaned = cleaned.replace(/\s+data-zr-dom-id="[^"]+"/g, "");
  cleaned = cleaned.replace(/\s+size-sensor-id="[^"]+"/g, "");

  // 9. Pretty-print for stable diffs
  return html_beautify(cleaned, {
    indent_size: 2,
    indent_char: " ",
    wrap_line_length: 0,
    preserve_newlines: false,
    end_with_newline: true,
    indent_inner_html: false,
    unformatted: [],
    inline: [],
  });
}
