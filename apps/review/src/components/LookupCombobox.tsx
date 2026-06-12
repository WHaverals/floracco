import { useEffect, useRef, useState } from "react";
import { lookupValues } from "../api";
import type { LookupValue } from "../types";

/* Typeahead over a lookup list (economic activity, place, currency, title).
 *
 * These values are raw, interpretive phrases entered "exactly as in the
 * document" — 73% of activities are used once, and `negozio mercantile` vs
 * `negozi mercantili` are legitimately distinct entries. So this control:
 *   - suggests existing phrases (diacritic-insensitive matching, usage counts)
 *     so the encoder can REUSE an identical phrase when that is what they mean;
 *   - never auto-selects, never normalizes, never merges: free text is stored
 *     exactly as typed, and creating a new phrase is a normal outcome, not a
 *     failure.
 */
export default function LookupCombobox({
  kind,
  label,
  value,
  onChange,
  placeholder,
}: {
  kind: "economic_activity" | "place" | "currency" | "title";
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  const [suggestions, setSuggestions] = useState<LookupValue[]>([]);
  const [exact, setExact] = useState<LookupValue | null>(null);
  const [open, setOpen] = useState(false);
  const debounce = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (!value.trim()) {
      setSuggestions([]);
      setExact(null);
      return;
    }
    debounce.current = window.setTimeout(() => {
      lookupValues(kind, value.trim())
        .then((response) => {
          setSuggestions(response.values);
          setExact(response.exact);
        })
        .catch(() => setSuggestions([]));
    }, 200);
    return () => window.clearTimeout(debounce.current);
  }, [kind, value]);

  const trimmed = value.trim();
  const reusing = exact && exact.value.trim() === trimmed;

  return (
    <label className="create-field lookup-combobox">
      <span className="create-label">{label}</span>
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        autoComplete="off"
      />
      {open && suggestions.length > 0 && (
        <ul className="lookup-suggestions" role="listbox">
          {suggestions.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(item.value);
                  setOpen(false);
                }}
              >
                <span className="lookup-value">{item.value}</span>
                <span className="lookup-used muted">used {item.used}×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {trimmed &&
        (reusing ? (
          <span className="lookup-status is-reuse">
            Reusing the existing phrase (used {exact!.used}×).
          </span>
        ) : (
          <span className="lookup-status is-new">
            New phrase — stored exactly as typed (interpretive; never normalized).
          </span>
        ))}
    </label>
  );
}
