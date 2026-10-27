import { useEffect, useMemo, useState } from "react";
import { countFind, findValues } from "../api";
import { useLatest } from "../utils/latest";
import type { FindCount, FindKind, FindValue } from "../types";
import { WORD, optionSpecs, type Condition, type FindSpec, type FindState, type Target, type Value } from "../utils/findSpec";

/* The parts a Find question is built from, shared by the sentence (FindPane)
 * and by Compare (FindViews): a chip for one condition, its choice and verb
 * menus and its value picker, each choice and value with the contracts this
 * question would find with it and on its own (12.2), and the small add menu.
 * Every word shown is the registry's (find_registry). */

const COUNT_BATCH = 40; // the server counts up to 40 questions at once

export function contractsWord(n: number): string {
  return `${n.toLocaleString()} contract${n === 1 ? "" : "s"}`;
}

/** The counts of several questions, asked once the list is open; null while
 * they load. A question that does not fit counts as null. */
export function useCounts(specs: FindSpec[] | null): FindCount[] | null {
  const key = specs && specs.length ? JSON.stringify(specs) : "";
  const [done, setDone] = useState<{ key: string; counts: FindCount[] } | null>(null);
  const begin = useLatest();
  useEffect(() => {
    if (!key) return;
    const fresh = begin();
    const handle = window.setTimeout(() => {
      const all: FindSpec[] = JSON.parse(key);
      const batches: FindSpec[][] = [];
      for (let i = 0; i < all.length; i += COUNT_BATCH) batches.push(all.slice(i, i + COUNT_BATCH));
      Promise.all(batches.map((batch) => countFind(batch)))
        .then((replies) => fresh() && setDone({ key, counts: replies.flatMap((r) => r.counts) }))
        .catch(() => fresh() && setDone({ key, counts: all.map(() => null) }));
    }, 200);
    return () => window.clearTimeout(handle);
  }, [key, begin]);
  return done && done.key === key ? done.counts : null;
}

function countText(count: FindCount | undefined, loading: boolean): string {
  if (loading) return "…";
  return count ? count.total.toLocaleString() : "–";
}

/** Two counts beside a choice: in this question, and on its own. */
export function CountCells({ here, alone, loading }: { here?: FindCount; alone?: FindCount; loading: boolean }) {
  return (
    <>
      <span className="find-count">{countText(here, loading)}</span>
      <span className="find-count">{countText(alone, loading)}</span>
    </>
  );
}

export function CountHead() {
  return (
    <span className="find-count-head">
      <span />
      <span>this question</span>
      <span>on its own</span>
    </span>
  );
}

export type Option = { key: string; label: string; condition: Condition };

/** A choice inside a chip (a choice, or "is" against "is not"), each option
 * with the contracts this question would find with it and on its own. */
export function OptionMenu({
  options,
  current,
  state,
  target,
  counted,
  onPick,
}: {
  options: Option[];
  current: string;
  state: FindState;
  target: Target | null;
  counted: boolean;
  onPick: (condition: Condition) => void;
}) {
  const [open, setOpen] = useState(false);
  const pairs = useMemo(
    () => (open && counted ? optionSpecs(state, target, options.map((o) => o.condition)) : null),
    [open, counted, state, target, options],
  );
  const counts = useCounts(pairs ? pairs.flatMap((p) => [p.here, p.alone]) : null);
  const shown = options.find((o) => o.key === current) ?? options[0];
  return (
    <span className="find-menu-wrap" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOpen(false)}>
      <button type="button" className="find-choice" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {shown?.label} <span className="find-caret">▾</span>
      </button>
      {open && (
        <span className="find-menu find-counted">
          {counted && <CountHead />}
          {options.map((option, i) => (
            <button
              type="button"
              key={option.key}
              className={option.key === current ? "find-menu-item find-counted-row is-current" : "find-menu-item find-counted-row"}
              onClick={() => {
                setOpen(false);
                onPick(option.condition);
              }}
            >
              <span>{option.label}</span>
              {counted && <CountCells here={counts?.[2 * i]} alone={counts?.[2 * i + 1]} loading={!counts} />}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

/** Typeahead over a condition's values: the stored spelling, how many of its
 * contracts the data-entry team marked as inferred, and both counts. For a
 * word, the word as typed with the stored spellings it matches. */
function ValuePicker({
  entry,
  condition,
  state,
  target,
  autoFocus,
  onPick,
}: {
  entry: FindKind;
  condition: Condition;
  state: FindState;
  target: Target | null;
  autoFocus?: boolean;
  onPick: (value: FindValue) => void;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(Boolean(autoFocus));
  const [values, setValues] = useState<FindValue[] | null>(null);
  const [failed, setFailed] = useState(false);
  const begin = useLatest();
  const isWord = entry.values === "word";
  const chosen = condition.values ?? [];
  useEffect(() => {
    if (!open) return;
    const typed = text.trim();
    if (isWord && !WORD.test(typed)) {
      begin();
      setValues([]);
      return;
    }
    const handle = window.setTimeout(() => {
      const fresh = begin();
      setFailed(false);
      findValues(entry.kind, typed)
        .then((r) => fresh() && setValues(r.values))
        .catch(() => fresh() && setFailed(true));
    }, 200);
    return () => window.clearTimeout(handle);
  }, [text, open, begin, entry.kind, isWord]);
  const offered = useMemo(
    () => (values ?? []).filter((v) => !chosen.some((c) => c === v.value || (isWord && String(c).toLowerCase() === String(v.value).toLowerCase()))),
    [values, chosen, isWord],
  );
  const pairs = useMemo(
    () =>
      open && offered.length
        ? optionSpecs(
            state,
            target,
            offered.map((v) => ({ kind: condition.kind, not: condition.not, values: [v.value] })),
          )
        : null,
    [open, offered, state, target, condition.kind, condition.not],
  );
  const counts = useCounts(pairs ? pairs.flatMap((p) => [p.here, p.alone]) : null);
  const pick = (value: FindValue) => {
    onPick(value);
    setText("");
  };
  const placeholder = isWord ? (chosen.length ? "or another word" : "type a word") : chosen.length ? "or another" : `type to choose`;
  return (
    <span className="find-picker">
      <input
        type="text"
        value={text}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && offered[0]) pick(offered[0]);
        }}
      />
      {open && (
        <span className="find-suggest find-counted">
          {failed ? (
            <span className="find-suggest-note error-text">The list could not be loaded. Type again to retry.</span>
          ) : values === null ? (
            <span className="find-suggest-note muted">Loading…</span>
          ) : offered.length === 0 ? (
            <span className="find-suggest-note muted">
              {isWord ? (text.trim() ? "One word, in letters, such as mercante." : "Type a word, such as mercante.") : "Nothing matches."}
            </span>
          ) : (
            <>
              <CountHead />
              {offered.map((v, i) => (
                <button
                  type="button"
                  key={String(v.value)}
                  className="find-suggest-item find-counted-row"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(v)}
                >
                  <span>
                    {isWord ? `“${v.name}”` : v.name}
                    {v.inferred > 0 && <span className="muted"> · {v.inferred.toLocaleString()} marked as inferred</span>}
                    {isWord && (
                      <span className="muted find-spellings">
                        {[...(v.titles ?? []), ...(v.professions ?? [])].length
                          ? `matches ${[...(v.titles ?? []), ...(v.professions ?? [])].slice(0, 6).join(", ")}${
                              [...(v.titles ?? []), ...(v.professions ?? [])].length > 6 ? ", …" : ""
                            }`
                          : "matches no stored title or profession"}
                      </span>
                    )}
                  </span>
                  <CountCells here={counts?.[2 * i]} alone={counts?.[2 * i + 1]} loading={!counts} />
                </button>
              ))}
            </>
          )}
        </span>
      )}
    </span>
  );
}

export type MenuItem = { key: string; label: string; onPick: () => void };

/** A small menu under a button; it closes on a pick or on leaving it. */
export function AddMenu({ label, sections, className }: { label: string; sections: { title: string; items: MenuItem[] }[]; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="find-menu-wrap" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOpen(false)}>
      <button type="button" className={className ?? "an-add-filter"} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {label}
      </button>
      {open && (
        <span className="find-menu">
          {sections.map((section) => (
            <span key={section.title} className="find-menu-section">
              <span className="find-menu-title">{section.title}</span>
              {section.items.map((item) => (
                <button
                  type="button"
                  key={item.key}
                  className="find-menu-item"
                  onClick={() => {
                    setOpen(false);
                    item.onPick();
                  }}
                >
                  {item.label}
                </button>
              ))}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

/** A new condition of a kind, before she chooses its value. */
export function freshCondition(entry: FindKind): Condition {
  return entry.choices ? { kind: entry.kind, not: false, choice: entry.choices[0].key } : { kind: entry.kind, not: false, values: [] };
}

/** One condition as a chip: its choice and verb, or its verb, values and picker. */
export function ConditionChip({
  entry,
  condition,
  state,
  target,
  names,
  autoFocus,
  partner,
  onChange,
  onRemove,
  onName,
}: {
  entry: FindKind;
  condition: Condition;
  state: FindState;
  target: Target | null;
  names: Record<string, string>;
  autoFocus?: boolean;
  partner?: boolean;
  onChange: (next: Condition) => void;
  onRemove: () => void;
  onName: (key: string, name: string) => void;
}) {
  const className = partner ? "an-filter find-chip find-chip-partner" : "an-filter find-chip";
  const remove = (
    <button type="button" className="an-filter-x" aria-label="Remove this condition" onClick={onRemove}>
      ✕
    </button>
  );
  if (entry.choices) {
    const options: Option[] = [false, true].flatMap((not) =>
      entry.choices!.map((c) => ({
        key: `${not ? "not-" : ""}${c.key}`,
        label: not ? c.reading_not : c.reading,
        condition: { kind: entry.kind, not, choice: c.key },
      })),
    );
    return (
      <span className={className}>
        <OptionMenu
          options={options}
          current={`${condition.not ? "not-" : ""}${condition.choice}`}
          state={state}
          target={target}
          counted
          onPick={onChange}
        />
        {remove}
      </span>
    );
  }
  const values = condition.values ?? [];
  const verbs: Option[] = [
    { key: "is", label: entry.label ?? "", condition: { ...condition, not: false } },
    { key: "not", label: entry.label_not ?? "", condition: { ...condition, not: true } },
  ];
  const nameOf = (v: Value) =>
    v === null ? "not recorded" : entry.values === "word" ? `“${v}”` : (names[`${entry.values}:${v}`] ?? `${entry.values} ${v}`);
  return (
    <span className={className}>
      <OptionMenu
        options={verbs}
        current={condition.not ? "not" : "is"}
        state={state}
        target={target}
        counted={values.length > 0}
        onPick={onChange}
      />
      {values.map((v, k) => (
        <span key={String(v)} className="find-token">
          {k > 0 && <span className="find-or">{k === values.length - 1 ? "or" : ","}</span>}
          {nameOf(v)}
          <button
            type="button"
            className="an-filter-x"
            aria-label={`Remove ${nameOf(v)}`}
            onClick={() => (values.length > 1 ? onChange({ ...condition, values: values.filter((x) => x !== v) }) : onRemove())}
          >
            ✕
          </button>
        </span>
      ))}
      <ValuePicker
        entry={entry}
        condition={condition}
        state={state}
        target={target}
        autoFocus={autoFocus}
        onPick={(picked) => {
          if (picked.value !== null && entry.values !== "word") onName(`${entry.values}:${picked.value}`, picked.name);
          onChange({ ...condition, values: [...values, picked.value] });
        }}
      />
      {values.length === 0 && remove}
    </span>
  );
}

