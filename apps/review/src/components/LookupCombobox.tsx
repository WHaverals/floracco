import { useEffect, useRef, useState } from "react";
import { lookupValues } from "../api";
import { useLatest } from "../utils/latest";
import type { LookupValue } from "../types";

/** Where the search for existing phrases stands for the current text. */
export type LookupCheck = "idle" | "pending" | "ok" | "failed";

/** Why a save must wait for the phrase search, or null when it may go ahead.
 * `field` names the box in the reviewer's words ("the residence"). A save while
 * the search is running or after it failed could store a new phrase where an
 * existing one was meant, so both hold the save. */
export function lookupCheckBlock(check: LookupCheck, field = "this field"): string | null {
  if (check === "pending") return `Still searching the existing phrases for ${field}. One moment.`;
  if (check === "failed")
    return `The search of existing phrases for ${field} did not run. Click “Try again” under it before saving.`;
  return null;
}

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
/** "used on 1 contract" / "used on 12 contracts": the Reference page's number. */
function usedOn(n: number): string {
  if (n === 0) return "not used on any contract";
  return `used on ${n.toLocaleString()} contract${n === 1 ? "" : "s"}`;
}

export default function LookupCombobox({
  kind,
  label,
  value,
  onChange,
  placeholder,
  onCheckChange,
}: {
  kind: "economic_activity" | "place" | "currency" | "title";
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  /** Reports the search state so the parent can hold its Save while it is pending or failed. */
  onCheckChange?: (check: LookupCheck) => void;
}) {
  const [suggestions, setSuggestions] = useState<LookupValue[]>([]);
  const [exact, setExact] = useState<LookupValue | null>(null);
  // The last answer, tagged with the text it answers: a reply for an earlier text never counts.
  const [answered, setAnswered] = useState<{ q: string; ok: boolean } | null>(null);
  const [retry, setRetry] = useState(0);
  const [open, setOpen] = useState(false);
  const debounce = useRef<number | undefined>(undefined);
  const picked = useRef<LookupValue | null>(null);
  const beginLookup = useLatest();

  useEffect(() => {
    window.clearTimeout(debounce.current);
    beginLookup(); // a reply still in flight answers an earlier text: drop it
    const q = value.trim();
    if (!q) {
      setSuggestions([]);
      setExact(null);
      setAnswered(null);
      return;
    }
    // A phrase picked from the list is known to exist: no second round trip.
    if (picked.current && picked.current.value.trim() === q) {
      setExact(picked.current);
      setAnswered({ q, ok: true });
      return;
    }
    debounce.current = window.setTimeout(() => {
      const fresh = beginLookup();
      lookupValues(kind, q)
        .then((response) => {
          if (!fresh()) return; // a stale prefix must not flip the reuse-vs-new badge
          setSuggestions(response.values);
          setExact(response.exact);
          setAnswered({ q, ok: true });
        })
        .catch(() => {
          if (!fresh()) return;
          setSuggestions([]);
          setExact(null);
          setAnswered({ q, ok: false }); // a failed search is not "new phrase"
        });
    }, 200);
    return () => window.clearTimeout(debounce.current);
  }, [kind, value, retry, beginLookup]);

  const trimmed = value.trim();
  const check: LookupCheck = !trimmed
    ? "idle"
    : answered?.q !== trimmed
      ? "pending"
      : answered.ok
        ? "ok"
        : "failed";
  // Report through a ref so an inline parent callback does not re-run the effect.
  const report = useRef(onCheckChange);
  report.current = onCheckChange;
  useEffect(() => {
    report.current?.(check);
  }, [check]);

  // Reuse only on an identical phrase, the same rule the server applies on save.
  const reusing = check === "ok" && exact && exact.value.trim() === trimmed;

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
                  picked.current = item;
                  onChange(item.value);
                  setOpen(false);
                }}
              >
                <span className="lookup-value">{item.value}</span>
                <span className="lookup-used muted">{usedOn(item.used)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {check === "pending" && <span className="lookup-status muted">Searching the existing phrases…</span>}
      {check === "failed" && (
        <span className="lookup-status error-text">
          The search of existing phrases did not run, so it is not known whether this phrase is already recorded.{" "}
          <button
            type="button"
            className="link-like"
            onClick={() => {
              setAnswered(null);
              setRetry((n) => n + 1);
            }}
          >
            Try again
          </button>
        </span>
      )}
      {check === "ok" &&
        (reusing ? (
          <span className="lookup-status is-reuse">
            An existing phrase, {usedOn(exact!.used)}. The record will point to it.
          </span>
        ) : exact ? (
          <span className="lookup-status is-new">
            A new phrase, stored exactly as typed. A recorded phrase differs from it only in capitals, accents or
            spacing: “{exact.value}” ({usedOn(exact.used)}).
          </span>
        ) : (
          <span className="lookup-status is-new">A new phrase. It will be stored exactly as typed.</span>
        ))}
    </label>
  );
}
