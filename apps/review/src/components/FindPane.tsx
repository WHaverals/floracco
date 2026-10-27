import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import CopyLinkButton from "./CopyLinkButton";
import { AddMenu, ConditionChip, contractsWord, freshCondition, useCounts } from "./FindChips";
import { CountView, PartnersView, TimeView } from "./FindViews";
import { SavedQuestionBar, recordFindHistory } from "./FindQuestions";
import { downloadFindCsv, findHow, loadFindRegistry, runFind } from "../api";
import { useLatest } from "../utils/latest";
import type { FindHow, FindKind, FindRegistry, FindResult } from "../types";
import {
  EMPTY_FIND,
  findSearch,
  fromSpec,
  incompleteReason,
  isYears,
  kindsFrom,
  lineOpenings,
  parseFind,
  placeCondition,
  placementSpecs,
  toSpec,
  type Condition,
  type FindState,
  type Kinds,
  type Quantifier,
} from "../utils/findSpec";
import { DEFAULT_VIEW, VIEW_KEYS, joinSearch, parseView, viewSearch, type FindView, type ViewKind } from "../utils/findView";

const VIEWS: { key: ViewKind; label: string }[] = [
  { key: "list", label: "List" },
  { key: "count", label: "Count" },
  { key: "time", label: "Over time" },
  { key: "compare", label: "Compare" },
  { key: "partners", label: "See their partners" },
];

/* Find, steps 1 and 2 (review 2026-10-27, sections 11-12, build path 12.8): the
 * question as a sentence of chips, the answer line, the List of every matching
 * contract with a CSV of all of it, and How this was found beside it. Every
 * word of a chip is taken from the server's registry (find_registry), which also
 * writes the sentence, the answer, the steps and the download's opening lines,
 * so the screen and the file read the same. Each picker shows, for every value,
 * the contracts this question would find with it and the contracts it finds on
 * its own. The page sends keys and values only; the server builds the SQL. */

const MAX_LINK_LENGTH = 6000;
function saveFile(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const UNDATED = "0000-00-00";

/** "About which partner?": the same partner as each partner line, or another
 * partner (a further line), each with the contracts it would find (12.2). */
function Placements({
  registry,
  state,
  condition,
  onPlace,
}: {
  registry: FindRegistry;
  state: FindState;
  condition: Condition;
  onPlace: (line: number | null) => void;
}) {
  const ready = !condition.values || condition.values.length > 0;
  const placed = useMemo(() => (ready ? placementSpecs(state, condition) : null), [ready, state, condition]);
  const counts = useCounts(placed ? placed.map((p) => p.spec) : null);
  const lines = (placed ?? [...state.partners.map((_l, i) => ({ line: i })), { line: null }]) as { line: number | null }[];
  return (
    <span className="find-placements">
      {lines.map(({ line }, i) => {
        const count = counts?.[i];
        const parts = count?.parts ?? [];
        // the line this choice writes, as the sentence would read it
        const reading = parts.length ? parts[line === null ? parts.length - 1 : parts.length - state.partners.length + line] : "";
        return (
          <button type="button" key={String(line)} className="find-placement" disabled={!ready} onClick={() => onPlace(line)}>
            <strong>{line === null ? registry.placements.another : registry.placements.same}</strong>
            {reading && <span className="muted">{reading}</span>}
            <span className="find-count">{ready ? (counts ? (count ? contractsWord(count.total) : "–") : "…") : ""}</span>
          </button>
        );
      })}
    </span>
  );
}

/** Cell text on screen; the CSV keeps the stored values. */
function cellText(column: string, cell: string | number | null): string {
  if (cell == null || cell === "") return "";
  if (column === "registration_date" && cell === UNDATED) return "not recorded";
  return String(cell);
}

function compareCells(column: string, a: string | number | null, b: string | number | null, desc: boolean): number {
  // Empty cells, and undated contracts, stay last whichever way the column runs.
  const emptyA = a == null || a === "" || (column === "registration_date" && a === UNDATED);
  const emptyB = b == null || b === "" || (column === "registration_date" && b === UNDATED);
  if (emptyA || emptyB) return emptyA === emptyB ? 0 : emptyA ? 1 : -1;
  const order =
    typeof a === "number" && typeof b === "number"
      ? a - b
      : String(a).trim().localeCompare(String(b).trim(), "it", { sensitivity: "base", numeric: true });
  return desc ? -order : order;
}

/** The List narrowed to the contracts of one Count cell. */
function openedRows(result: FindResult, ids: Set<number>): FindResult {
  const at = result.columns.indexOf("contract_id");
  const rows = result.rows.filter((row) => ids.has(Number(row[at])));
  return { ...result, rows, row_count: rows.length, truncated: false };
}

function FindList({ result }: { result: FindResult }) {
  const [sort, setSort] = useState<{ col: number; desc: boolean } | null>(null);
  const rows = useMemo(() => {
    if (!sort) return result.rows;
    const column = result.columns[sort.col];
    return [...result.rows].sort((x, y) => compareCells(column, x[sort.col], y[sort.col], sort.desc));
  }, [result, sort]);
  const idCol = result.columns.indexOf("contract_id");
  return (
    <div className="an-table-wrap">
      <table className="an-table find-table">
        <thead>
          <tr>
            {result.labels.map((label, ci) => (
              <th key={ci}>
                <button
                  type="button"
                  className="find-sort"
                  onClick={() => setSort((s) => (s?.col === ci ? (s.desc ? null : { col: ci, desc: true }) : { col: ci, desc: false }))}
                  title="Sort by this column; click again to reverse, a third time for date order"
                >
                  {label}
                  {sort?.col === ci ? (sort.desc ? " ▾" : " ▴") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row[idCol])}>
              {row.map((cell, ci) => (
                <td key={ci}>
                  {ci === idCol ? <Link to={`/database/contract/${cell}`}>{String(cell)}</Link> : cellText(result.columns[ci], cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.truncated && (
        <p className="muted an-table-note">
          The list shows the first {result.row_count.toLocaleString()} rows; the download holds all of them.
        </p>
      )}
    </div>
  );
}

/** Two earlier sets combined (the set history): each with its sentence and
 * count, and a link that opens it on its own. The words are the registry's. */
function CombinedRow({
  registry,
  kinds,
  combine,
  onRemove,
}: {
  registry: FindRegistry;
  kinds: Kinds;
  combine: NonNullable<FindState["combine"]>;
  onRemove: () => void;
}) {
  const specs = useMemo(() => combine.sets.map((set) => toSpec(set)), [combine]);
  const counts = useCounts(specs);
  const words = registry.combine.find((c) => c.key === combine.op);
  return (
    <div className="find-row find-combined">
      <span className="find-unit">{registry.unit}</span>
      <span>{words?.opening}</span>
      {combine.sets.map((set, k) => (
        <span key={k} className="find-token-group">
          {k > 0 && <span>{words?.between}</span>}
          <Link to={`/analysis/find?${findSearch(set, kinds)}`} className="find-set-link">
            “{counts?.[k]?.sentence ?? "…"}”
          </Link>
          {counts?.[k] && <span className="muted">({counts[k]!.total.toLocaleString()})</span>}
        </span>
      ))}
      <button type="button" className="an-filter-x" aria-label="Remove the combination" onClick={onRemove}>
        ✕
      </button>
    </div>
  );
}

/** How this was found (12.4), beside the List: what is counted, the steps with
 * their counts, other spellings left out, near misses as questions of their
 * own, what rests on attribution or on a mark, the spellings a word matched,
 * and the query that ran. Every sentence is the server's. */
function HowPanel({ specKey, kinds }: { specKey: string; kinds: Kinds }) {
  const [found, setFound] = useState<{ key: string; how: FindHow } | null>(null);
  const [error, setError] = useState("");
  const begin = useLatest();
  useEffect(() => {
    const fresh = begin();
    const handle = window.setTimeout(() => {
      setError("");
      findHow(JSON.parse(specKey))
        .then((how) => fresh() && setFound({ key: specKey, how }))
        .catch((e: Error) => fresh() && setError(e.message));
    }, 450);
    return () => window.clearTimeout(handle);
  }, [specKey, begin]);
  if (error) {
    return (
      <aside className="find-how">
        <p className="error-text">{error}</p>
      </aside>
    );
  }
  if (!found) {
    return (
      <aside className="find-how">
        <p className="muted">Loading…</p>
      </aside>
    );
  }
  const how = found.how;
  return (
    <aside className={found.key === specKey ? "find-how" : "find-how is-updating"}>
      <h3>How this was found</h3>
      <section>
        <h4>What is counted</h4>
        {how.counted.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </section>
      {how.sets.length > 0 && (
        <section>
          <h4>The two sets</h4>
          {how.sets.map((set, k) => (
            <p key={k}>
              Set {k + 1}: {set.sentence}: <strong>{contractsWord(set.count)}</strong>.
            </p>
          ))}
        </section>
      )}
      <section>
        <h4>Steps</h4>
        <ol className="find-steps">
          {how.steps.map((step, i) => (
            <li key={i}>
              <span>{step.reading}</span>
              <strong>{step.count.toLocaleString()}</strong>
              {step.alone !== undefined && i > 1 && <span className="muted">on its own: {step.alone.toLocaleString()}</span>}
            </li>
          ))}
        </ol>
      </section>
      {how.inference.length > 0 && (
        <section>
          <h4>What rests on attribution or a mark</h4>
          {how.inference.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </section>
      )}
      {how.near_misses.length > 0 && (
        <section>
          <h4>Near misses</h4>
          {how.near_misses.map((near) => {
            const search = findSearch(fromSpec(near.spec), kinds);
            return (
              <p key={near.sentence}>
                {near.sentence}: <strong>{contractsWord(near.count)}</strong>.{" "}
                <Link to={`/analysis/find?${search}`}>Open as its own question</Link>
              </p>
            );
          })}
        </section>
      )}
      {how.left_out.length > 0 && (
        <section>
          <h4>Other spellings, left out</h4>
          {how.left_out.map((entry) => (
            <p key={`${entry.chosen}:${entry.name}`}>
              {entry.name}, on {contractsWord(entry.contracts)}; with “{entry.chosen}” it would add{" "}
              {entry.adds === 0 ? "none" : contractsWord(entry.adds)} here.
            </p>
          ))}
        </section>
      )}
      {how.searched.length > 0 && (
        <section>
          <h4>Stored spellings each word matched</h4>
          {how.searched.map((found) => (
            <p key={found.word}>
              “{found.word}”: {found.titles.length ? `titles ${found.titles.join(", ")}` : "no title"};{" "}
              {found.professions.length ? `professions ${found.professions.join(", ")}` : "no profession"}.
            </p>
          ))}
        </section>
      )}
      <section>
        <h4>As of</h4>
        <p>{how.as_of}</p>
      </section>
      <details className="find-query">
        <summary>The database query</summary>
        <pre className="an-sql">
          {how.sql}
          {how.params.length > 0 && `\n\nValues, in order: ${how.params.join(", ")}`}
        </pre>
      </details>
    </aside>
  );
}

export default function FindPane({ onAddress }: { onAddress: (search: string) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [registry, setRegistry] = useState<FindRegistry | null>(null);
  const [registryFailed, setRegistryFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<FindState>(EMPTY_FIND);
  const [view, setViewState] = useState<FindView>(DEFAULT_VIEW);
  const setView = useCallback((next: Partial<FindView>) => setViewState((v) => ({ ...v, ...next })), []);
  // a Count cell opened in the List: its contracts, and their row and column in words
  const [opened, setOpened] = useState<{ ids: Set<number>; label: string } | null>(null);
  const [dropped, setDropped] = useState<string[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [focus, setFocus] = useState<string | null>(null); // a value chip just added
  const [staged, setStaged] = useState<Condition | null>(null); // a condition about a partner, before "which partner?"
  const [result, setResult] = useState<FindResult | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const beginRun = useLatest();
  const beginExport = useLatest();
  const written = useRef<string | null>(null);

  useEffect(() => {
    setRegistryFailed(false);
    loadFindRegistry()
      .then(setRegistry)
      .catch(() => setRegistryFailed(true));
  }, [attempt]);

  const kinds: Kinds = useMemo(
    () => (registry ? kindsFrom(registry) : { contract: {}, partner: {} }),
    [registry],
  );
  const quantifierWords = useMemo(
    () =>
      Object.fromEntries((registry?.quantifiers ?? []).map((q) => [q.key, { reading: q.reading, later: q.later }])) as Record<
        Quantifier,
        { reading: string; later: string }
      >,
    [registry],
  );

  // Read a link (or Back/Forward) into the question; our own writes never re-read.
  const search = location.search.replace(/^\?/, "");
  useEffect(() => {
    if (!registry || search === written.current) return;
    const parsed = parseFind(search, kinds, VIEW_KEYS);
    const parsedView = parseView(search);
    setState(parsed.state);
    setViewState(parsedView);
    setStaged(null);
    setDropped(parsed.dropped);
    written.current = joinSearch(findSearch(parsed.state, kinds), viewSearch(parsedView));
    onAddress(written.current);
  }, [registry, search, kinds, onAddress]);

  // The address follows the question and its view.
  const current = registry ? joinSearch(findSearch(state, kinds), viewSearch(view)) : null;
  useEffect(() => {
    if (current === null || current === written.current) return;
    const handle = window.setTimeout(() => {
      written.current = current;
      onAddress(current);
      setDropped([]);
      navigate(`/analysis/find${current ? `?${current}` : ""}`, { replace: true });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [current, navigate, onAddress]);

  const incomplete = registry ? incompleteReason(state, kinds) : "";
  const spec = useMemo(() => toSpec(state), [state]);
  const specKey = JSON.stringify(spec);
  const questionSearch = registry ? findSearch(state, kinds) : "";
  useEffect(() => {
    if (!registry || incomplete) return;
    setRunning(true);
    const handle = window.setTimeout(() => {
      const fresh = beginRun();
      setError("");
      runFind(JSON.parse(specKey))
        .then((r) => {
          if (!fresh()) return;
          setResult(r);
          recordFindHistory(questionSearch, r.sentence, r.total);
          setNames((n) => ({
            ...n,
            ...Object.fromEntries(
              Object.entries(r.names).flatMap(([kind, byValue]) => Object.entries(byValue).map(([v, name]) => [`${kind}:${v}`, name])),
            ),
          }));
        })
        .catch((e: Error) => {
          if (!fresh()) return;
          setError(e.message);
          setResult(null);
        })
        .finally(() => fresh() && setRunning(false));
    }, 350);
    return () => window.clearTimeout(handle);
  }, [registry, specKey, incomplete, beginRun, questionSearch]);

  // A new question closes an opened cell.
  useEffect(() => setOpened(null), [specKey]);

  // A new question: a download still under way for the old one saves nothing.
  useEffect(() => {
    beginExport();
    setExporting(false);
    setExportError("");
  }, [specKey, beginExport]);

  if (!registry) {
    return (
      <div className="an-result">
        <p className="eyebrow">Find</p>
        {registryFailed ? (
          <p className="error-text">
            The conditions could not be loaded.{" "}
            <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </div>
    );
  }

  const contractKinds = registry.contract_conditions;
  const partnerKinds = registry.partner_conditions;
  const contractEntry = (kind: string) => contractKinds.find((k) => k.kind === kind);
  const partnerEntry = (kind: string) => partnerKinds.find((k) => k.kind === kind);
  const onName = (key: string, name: string) => setNames((n) => (n[key] === name ? n : { ...n, [key]: name }));

  const setContract = (i: number, next: FindState["contract"][number] | null) =>
    setState((s) => ({
      ...s,
      contract: next ? s.contract.map((c, j) => (j === i ? next : c)) : s.contract.filter((_, j) => j !== i),
    }));
  const setCondition = (line: number, k: number, next: Condition | null) =>
    setState((s) => ({
      ...s,
      partners: s.partners
        .map((l, j) =>
          j === line
            ? { ...l, conditions: next ? l.conditions.map((c, m) => (m === k ? next : c)) : l.conditions.filter((_, m) => m !== k) }
            : l,
        )
        .filter((l) => l.conditions.length > 0),
    }));
  const addContract = (entry: FindKind) => {
    if (entry.kind === "years") {
      setState((s) => ({ ...s, contract: [...s.contract, { kind: "years", from: "", to: "" }] }));
      return;
    }
    if (entry.values) setFocus(`contract:${state.contract.length}`);
    setState((s) => ({ ...s, contract: [...s.contract, freshCondition(entry)] }));
  };
  const addPartner = (entry: FindKind) => {
    const condition = freshCondition(entry);
    if (state.partners.length === 0) {
      if (entry.values) setFocus("partner:0:0");
      setState((s) => placeCondition(s, condition, null));
    } else {
      if (entry.values) setFocus("staged");
      setStaged(condition);
    }
  };
  const place = (line: number | null) => {
    if (!staged) return;
    setState((s) => placeCondition(s, staged, line));
    setStaged(null);
    setFocus(null);
  };
  const openings = lineOpenings(state.partners, quantifierWords, registry.same_or_another);
  const conditions = [...state.contract, ...state.partners.flatMap((l) => l.conditions)];
  const several = conditions.length > 1 || conditions.some((c) => !isYears(c) && (c.values?.length ?? 0) > 1);
  const link = `/analysis/find${current ? `?${current}` : ""}`;

  const exportCsv = () => {
    const fresh = beginExport();
    setExportError("");
    setExporting(true);
    downloadFindCsv(spec, `${window.location.origin}${link}`)
      .then((blob) => fresh() && saveFile("find.csv", blob))
      .catch((e: Error) => fresh() && setExportError(e.message))
      .finally(() => fresh() && setExporting(false));
  };

  return (
    <div className="an-result find-pane">
      <div className="page-head">
        <p className="eyebrow">Find</p>
        <CopyLinkButton
          path={link}
          title="Copy a link to this question. It runs on the current data when opened."
          disabledReason={link.length > MAX_LINK_LENGTH ? "Too long to share as a link" : undefined}
        />
      </div>
      {dropped.length > 0 && (
        <p className="muted an-note">Part of this link no longer matches the conditions and was left out: {dropped.join(", ")}.</p>
      )}

      <div className="an-builder find-sentence">
        {state.combine && (
          <CombinedRow
            registry={registry}
            kinds={kinds}
            combine={state.combine}
            onRemove={() => setState((s) => ({ contract: s.contract, partners: s.partners }))}
          />
        )}
        <div className="find-row">
          {!state.combine && <span className="find-unit">{registry.unit}</span>}
          {state.contract.map((c, i) => {
            if (isYears(c)) {
              return (
                <span className="an-filter find-chip" key={i}>
                  <span>{contractEntry("years")?.label} from</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={c.from}
                    placeholder="first year"
                    onChange={(e) => setContract(i, { ...c, from: e.target.value })}
                  />
                  <span>to</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={c.to}
                    placeholder="last year"
                    onChange={(e) => setContract(i, { ...c, to: e.target.value })}
                  />
                  <button type="button" className="an-filter-x" aria-label="Remove this condition" onClick={() => setContract(i, null)}>
                    ✕
                  </button>
                </span>
              );
            }
            const entry = contractEntry(c.kind);
            if (!entry) return null;
            return (
              <ConditionChip
                key={i}
                entry={entry}
                condition={c}
                state={state}
                target={{ level: "contract", index: i }}
                names={names}
                autoFocus={focus === `contract:${i}`}
                onChange={(next) => setContract(i, next)}
                onRemove={() => setContract(i, null)}
                onName={onName}
              />
            );
          })}
        </div>

        {state.partners.map((line, i) => (
          <div className="find-row find-line" key={i}>
            <select
              value={line.quantifier}
              onChange={(e) =>
                setState((s) => ({
                  ...s,
                  partners: s.partners.map((l, j) => (j === i ? { ...l, quantifier: e.target.value as Quantifier } : l)),
                }))
              }
            >
              {registry.quantifiers.map((q) => {
                const lines = state.partners.map((l, j) => (j === i ? { quantifier: q.key } : l));
                return (
                  <option key={q.key} value={q.key}>
                    {lineOpenings(lines, quantifierWords, registry.same_or_another)[i]}
                  </option>
                );
              })}
            </select>
            {line.conditions.map((cond, k) => {
              const entry = partnerEntry(cond.kind);
              if (!entry) return null;
              return (
                <span key={k} className="find-token-group">
                  {k > 0 && <span className="find-or">and</span>}
                  <ConditionChip
                    entry={entry}
                    condition={cond}
                    state={state}
                    target={{ level: "partner", line: i, index: k }}
                    names={names}
                    partner
                    autoFocus={focus === `partner:${i}:${k}`}
                    onChange={(next) => setCondition(i, k, next)}
                    onRemove={() => setCondition(i, k, null)}
                    onName={onName}
                  />
                </span>
              );
            })}
          </div>
        ))}

        {staged && partnerEntry(staged.kind) && (
          <div className="find-staged">
            <div className="find-row">
              <span className="find-staged-title">A partner who</span>
              <ConditionChip
                entry={partnerEntry(staged.kind)!}
                condition={staged}
                state={state}
                target={null}
                names={names}
                partner
                autoFocus={focus === "staged"}
                onChange={setStaged}
                onRemove={() => setStaged(null)}
                onName={onName}
              />
            </div>
            <div className="find-row">
              <span className="find-staged-title">About which partner?</span>
              <Placements registry={registry} state={state} condition={staged} onPlace={place} />
              <button type="button" className="link-like" onClick={() => setStaged(null)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="find-row find-add-row">
          <AddMenu
            label="+ condition"
            sections={[
              {
                title: "The contract",
                items: contractKinds.map((entry) => ({ key: entry.kind, label: entry.menu, onPick: () => addContract(entry) })),
              },
              {
                title: "A partner who",
                items: partnerKinds.map((entry) => ({ key: entry.kind, label: entry.menu, onPick: () => addPartner(entry) })),
              },
            ]}
          />
          {several && <span className="muted find-rule">{registry.rule}</span>}
        </div>
      </div>

      {incomplete ? (
        <p className="muted an-note">{incomplete}</p>
      ) : error ? (
        <p className="error-text">{error}</p>
      ) : result ? (
        <div className={running ? "find-answer-wrap is-updating" : "find-answer-wrap"}>
          <p className="find-answer">
            <strong>{result.answer[0]}</strong> {result.answer.slice(1).join(" ")}
            {running && <span className="muted find-updating"> Updating…</span>}
          </p>
          <div className="an-toolbar find-tabs-row">
            <span className="find-tabs" role="tablist" aria-label="Views of this set">
              {VIEWS.map((v) => (
                <button
                  key={v.key}
                  type="button"
                  role="tab"
                  aria-selected={view.view === v.key}
                  className={view.view === v.key ? "find-tab is-active" : "find-tab"}
                  onClick={() => setView({ view: v.key })}
                  disabled={result.total === 0 && v.key !== "list"}
                >
                  {v.label}
                </button>
              ))}
            </span>
            <button type="button" className="pill-button" onClick={exportCsv} disabled={exporting || result.total === 0}>
              {exporting ? "Preparing CSV…" : "Download CSV"}
            </button>
          </div>
          {exportError && <p className="error-text">{exportError}</p>}
          <SavedQuestionBar
            questionSearch={questionSearch}
            saveSearch={joinSearch(questionSearch, viewSearch({ ...view, saved: "" }))}
            spec={spec}
            savedId={view.saved}
            onSaved={(id) => setView({ saved: id })}
          />
          <div className="find-body">
            <div className="find-body-list">
              {result.total === 0 ? null : view.view === "count" ? (
                <CountView
                  specKey={specKey}
                  groupings={result.groupings}
                  view={view}
                  setView={setView}
                  onOpen={(ids, label) => {
                    setOpened({ ids: new Set(ids), label });
                    setView({ view: "list" });
                  }}
                />
              ) : view.view === "time" || view.view === "compare" ? (
                <TimeView
                  compare={view.view === "compare"}
                  spec={spec}
                  specKey={specKey}
                  state={state}
                  registry={registry}
                  kinds={kinds}
                  groupings={result.groupings}
                  names={names}
                  view={view}
                  setView={setView}
                  sentence={result.sentence}
                  link={link}
                  onName={onName}
                />
              ) : view.view === "partners" ? (
                <PartnersView specKey={specKey} view={view} setView={setView} link={link} />
              ) : opened ? (
                <>
                  <p className="find-opened">
                    The {contractsWord(opened.ids.size)} of “{opened.label}”, from the Count.{" "}
                    <button type="button" className="link-like" onClick={() => setOpened(null)}>
                      Show all {contractsWord(result.total)}
                    </button>
                  </p>
                  <FindList result={openedRows(result, opened.ids)} />
                </>
              ) : (
                <FindList result={result} />
              )}
            </div>
            <HowPanel specKey={specKey} kinds={kinds} />
          </div>
        </div>
      ) : (
        <p className="muted">Loading…</p>
      )}
    </div>
  );
}
