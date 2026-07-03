/**
 * Shared sentinel value used to inject a "+ New instrument…" item into a
 * controlled shadcn <Select>. shadcn Select disallows non-string values, so
 * each consumer's onValueChange handler intercepts this exact string and
 * opens the CreateInstrumentDialog instead of propagating to field.onChange.
 *
 * Real instrument UUIDs cannot collide with this string.
 */
export const CREATE_NEW_INSTRUMENT = "__create_new_instrument__";
