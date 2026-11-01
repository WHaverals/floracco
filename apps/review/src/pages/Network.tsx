import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { loadNetworkCircle, searchPersonsRich } from "../api";
import ContractDrawer, { ContractLink, drawerOrder, drawerRow } from "../components/ContractDrawer";
import NetworkFigure, { KIND_WORDS, NetworkKey, appearanceNotes, roleWords } from "../components/NetworkFigure";
import NetworkStrip from "../components/NetworkStrip";
import type { NetworkAct, NetworkAppearance, NetworkCircle, NetworkNode, NetworkTie, PersonHit } from "../types";
import { useEscapeLayer } from "../utils/escapeLayers";
import { useLatest } from "../utils/latest";
import { ringOrder, windowTies } from "../utils/networkLayout";
import { actRowLine, careerLine, gapLine, jewishWords, roleLine, sortCosigners, type CosignerOrder } from "../utils/networkWords";
import {
  DEFAULT_NETWORK,
  HUB_WORDS,
  OTHERS_PHRASE,
  circleKey,
  duplicateNames,
  networkSearch,
  nodeLabel,
  parseNetworkSearch,
  type NetworkHubs,
  type NetworkSelect,
  type NetworkState,
} from "../utils/networkSpec";
import { roleLabel } from "../utils/roles";

/* The Network view, step 1 (docs/network/scope_2026-10-29.md §4.8; the
 * contract in docs/network/step1_contract.md; the review of 29 October in
 * docs/network/step1_review_2026-10-29.md and its build contract
 * step1b_contract.md): the circle of one person entry as entered, two steps
 * out, from GET /api/network/circle/{id}. The page follows the Database page:
 * the dark rail on the left (the search, the sentence and the answer line,
 * the controls), the figure in the middle, and the side column on the right,
 * never empty: the co-signers at rest, the selected entry, act or pair with
 * its rows, and "How this was drawn" at the column's foot. The state lives in
 * the address (utils/networkSpec.ts). In development, `?fixture=1` draws the
 * hand-written fixture instead of asking the server.
 *
 * Step 2a (docs/network/next_2026-11-01.md §4.2, step2a_contract.md §4): the
 * time strip under the stage (components/NetworkStrip.tsx) and the window it
 * drags, `years` in the address; the mask (windowTies) is applied in the
 * client, nothing is refetched. While a window is set the rail carries the
 * window line with "All years", and the How card two more lines. */

const FIXTURE_KEY = "fixture";
const STANDING_LINE = "Person entries are counted as entered; one person may have several.";
const DROPPED_LINE = "Part of this link asks for a setting this page does not offer; it was left out.";
const OPEN_TITLE = "The full record, where it can be edited, in a new tab";
const ALL_YEARS = "All years";
/** The How card's lines while a window is set (next_2026-11-01.md §4.2). */
const HOW_WINDOW = (window: string) =>
  `Only acts registered ${window} make ties, and a tie's width counts those acts. An entry with no act in those years is faded and keeps its place.`;
const HOW_DRAG = "Drag the end earlier to see the circle as it stood in that year.";
/** Under 1400 px the side column stands under the figure and the How card folds. */
const NARROW = "(max-width: 1399px)";

function withFixture(search: string, fixture: boolean): string {
  if (!fixture) return search;
  return search ? `${search}&${FIXTURE_KEY}=1` : `${FIXTURE_KEY}=1`;
}

/** A stored value as the side column writes it: blanks read "not recorded". */
function shown(value: string | null | undefined): string {
  const text = String(value ?? "").trim();
  return text && text !== "—" ? text : "not recorded";
}

function dateWords(act: NetworkAct): string {
  return !act.date || act.date.startsWith("0000") ? "no registration date" : act.date;
}

function placeWords(act: NetworkAct): string {
  if (act.places.length === 0) return "not recorded";
  return `${act.places.join("; ")}${act.places_inferred ? " (marked as inferred)" : ""}`;
}

function cashWords(appearance: NetworkAppearance, act: NetworkAct | undefined): string {
  const parts: string[] = [];
  if (appearance.cash !== null) parts.push(`${appearance.cash.toLocaleString("en-US")}${act?.currency ? ` ${act.currency}` : ""}`);
  if (appearance.non_cash) parts.push(`non-cash: ${appearance.non_cash}`);
  return parts.length ? parts.join("; ") : "not recorded";
}

/** The role on the stake with its qualifiers: "general partner, on a joint stake". */
function roleCell(appearance: NetworkAppearance): string {
  const role = appearance.role ? roleWords(appearance.role) : "no role recorded";
  const notes = appearanceNotes(appearance);
  return notes.length ? `${role}, ${notes.join(", ")}` : role;
}

function yearsWords(node: NetworkNode): string {
  const span =
    node.first_year !== null && node.last_year !== null
      ? node.first_year === node.last_year
        ? String(node.first_year)
        : `${node.first_year}–${node.last_year}`
      : "";
  const undated = node.undated_acts > 0 ? `${node.undated_acts} with no registration date` : "";
  return [span, undated].filter(Boolean).join("; ");
}

function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** "1680–1689", or "1687" for a window of one year. */
function windowWords(years: [number, number]): string {
  return years[0] === years[1] ? String(years[0]) : `${years[0]}–${years[1]}`;
}

/** "3 acts together, 1669–1690" for a tie: the acts shared, and the span of
 * the dated ones among them. */
function tieWords(tie: NetworkTie | undefined, actsById: Map<number, NetworkAct>): string {
  if (!tie) return "";
  const years = tie.acts.map((id) => actsById.get(id)?.year ?? null).filter((y): y is number => y !== null);
  const span = years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "";
  return `${count(tie.acts.length, "act", "acts")} together${span ? `, ${span}` : ""}`;
}

function pairKey(a: number, b: number): string {
  return a < b ? `${a}.${b}` : `${b}.${a}`;
}

/** A selection in words, for the line when it is outside the circle. */
function outsideWords(select: NetworkSelect): string {
  if (select.kind === "pair") return `Entries ${select.a} and ${select.b} are outside this circle.`;
  return `${select.kind === "person" ? "Entry" : "Act"} ${select.id} is outside this circle.`;
}

function sortActs(ids: Iterable<number>, actsById: Map<number, NetworkAct>): NetworkAct[] {
  const date = (act: NetworkAct) => (!act.date || act.date.startsWith("0000") ? "9999-99-99" : act.date);
  return [...new Set(ids)]
    .map((id) => actsById.get(id))
    .filter((act): act is NetworkAct => Boolean(act))
    .sort((p, q) => (date(p) < date(q) ? -1 : date(p) > date(q) ? 1 : p.id - q.id));
}

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    list.addEventListener("change", onChange);
    setMatches(list.matches);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

export default function Network() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id } = useParams<{ id?: string }>();
  const personId = id !== undefined && /^\d{1,10}$/.test(id) ? Number(id) : null;
  const badId = id !== undefined && personId === null;

  const [state, setState] = useState<NetworkState>(DEFAULT_NETWORK);
  const [dropped, setDropped] = useState<string[]>([]);
  const [fixture, setFixture] = useState(false);
  const [labelAll, setLabelAll] = useState(false);
  const [circle, setCircle] = useState<NetworkCircle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [drawer, setDrawer] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  /** an entry lit from a side-column row, or the set of a year's signers lit from the strip */
  const [cosignerOrder, setCosignerOrder] = useState<CosignerOrder>("first");
  const [highlight, setHighlight] = useState<number | Set<number> | null>(null);
  /** the entry an act was opened from, for "‹ Back to {name}" */
  const [backTo, setBackTo] = useState<{ act: number; entry: number } | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<PersonHit[] | null>(null);
  const [searchFailed, setSearchFailed] = useState(false);
  const written = useRef<string | null>(null);
  const loadedFor = useRef<number | null>(null);
  const side = useRef<HTMLElement | null>(null);
  const beginLoad = useLatest();
  const beginSearch = useLatest();
  const narrow = useMedia(NARROW);

  // Read a link (or Back/Forward) into the state; our own writes never re-read.
  const search = location.search.replace(/^\?/, "");
  useEffect(() => {
    if (search === written.current) return;
    const parsed = parseNetworkSearch(search, [FIXTURE_KEY]);
    const useFixture = Boolean(import.meta.env.DEV) && new URLSearchParams(search).get(FIXTURE_KEY) === "1";
    if (!import.meta.env.DEV && new URLSearchParams(search).has(FIXTURE_KEY)) parsed.dropped.push(`“${FIXTURE_KEY}”`);
    setState(parsed.state);
    setDropped(parsed.dropped);
    setFixture(useFixture);
    written.current = withFixture(networkSearch(parsed.state), useFixture);
  }, [search]);

  // The address follows the state, 350 ms after a change, in place.
  const current = withFixture(networkSearch(state), fixture);
  const pathname = personId !== null ? `/network/person/${personId}` : "/network";
  useEffect(() => {
    if (current === written.current) return;
    const handle = window.setTimeout(() => {
      written.current = current;
      setDropped([]);
      navigate(`${pathname}${current ? `?${current}` : ""}`, { replace: true });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [current, pathname, navigate]);

  // The circle, from the server (or the fixture in development). Another
  // entry's circle starts from nothing; a control change dims the one drawn.
  const key = personId !== null ? circleKey(personId, state) : null;
  useEffect(() => {
    if (personId === null || key === null) {
      setCircle(null);
      setError("");
      loadedFor.current = null;
      return;
    }
    if (loadedFor.current !== personId) {
      setCircle(null);
      loadedFor.current = personId;
    }
    const fresh = beginLoad();
    setLoading(true);
    setError("");
    const request = fixture
      ? import("../dev/network-fixture.json").then((module) => module.default as unknown as NetworkCircle)
      : loadNetworkCircle(personId, { steps: state.steps, cap: state.cap, hubs: state.hubs, others: state.others, widen: state.widen });
    request
      .then((data) => {
        if (!fresh()) return;
        setCircle(data);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (!fresh()) return;
        setError(e.message);
        setCircle(null);
        setLoading(false);
      });
  }, [personId, key, fixture, attempt, beginLoad]);

  // The person search in the rail.
  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setHits(null);
      setSearchFailed(false);
      beginSearch();
      return;
    }
    const fresh = beginSearch();
    const handle = window.setTimeout(() => {
      searchPersonsRich(term, 12)
        .then((res) => fresh() && setHits(res.results))
        .catch(() => fresh() && setSearchFailed(true));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [query, beginSearch]);

  const actsById = useMemo(() => new Map((circle?.acts ?? []).map((act) => [act.id, act])), [circle]);
  const nodesById = useMemo(() => new Map((circle?.nodes ?? []).map((node) => [node.id, node])), [circle]);
  const tiesByPair = useMemo(() => new Map((circle?.ties ?? []).map((tie) => [pairKey(tie.a, tie.b), tie])), [circle]);
  const duplicates = useMemo(() => duplicateNames(circle?.nodes ?? []), [circle]);
  const order = useMemo(() => (circle ? ringOrder(circle) : []), [circle]);
  const label = (node: NetworkNode) => nodeLabel(node, duplicates);
  /** the window's counts for the rail line; the figure applies the same mask */
  const mask = useMemo(() => (circle && state.years ? windowTies(circle, state.years) : null), [circle, state.years]);
  // A circle drawn afresh carries no lit entries over.
  useEffect(() => setHighlight(null), [circle]);

  const setSelect = (select: NetworkSelect | null) => {
    setBackTo(null);
    setState((s) => ({ ...s, select }));
  };
  const update = (next: Partial<NetworkState>) => setState((s) => ({ ...s, ...next }));
  useEscapeLayer(state.select !== null && drawer === null, () => setSelect(null));

  // Opening another entry's circle keeps the controls and drops the selection;
  // it is pushed, so Back returns to this circle.
  const openEntry = (entryId: number) => {
    const controls = withFixture(networkSearch({ ...state, select: null }), fixture);
    setQuery("");
    setHits(null);
    navigate(`/network/person/${entryId}${controls ? `?${controls}` : ""}`);
  };

  // What the side column shows.
  const selectedNode = state.select?.kind === "person" ? nodesById.get(state.select.id) ?? null : null;
  const selectedAct = state.select?.kind === "contract" ? actsById.get(state.select.id) ?? null : null;
  const selectedPair =
    state.select?.kind === "pair" && nodesById.has(state.select.a) && nodesById.has(state.select.b)
      ? { a: nodesById.get(state.select.a)!, b: nodesById.get(state.select.b)!, tie: tiesByPair.get(pairKey(state.select.a, state.select.b)) }
      : null;
  const drawn = selectedNode !== null || selectedAct !== null || selectedPair !== null;

  const cosigners = useMemo(() => (circle ? sortCosigners(circle, cosignerOrder, order).map((row) => row.node) : []), [circle, cosignerOrder, order]);
  // The sentences over the loaded data (utils/networkWords.ts): who recurs, the
  // silence, the centre's two roles; null when there is nothing to say.
  const centreNode = circle ? nodesById.get(circle.centre) ?? null : null;
  const centreName = centreNode ? nodeLabel(centreNode, duplicates) : "";
  const careerWords = circle ? careerLine(circle, centreName) : null;
  const gapWords = circle ? gapLine(circle) : null;
  const roleWordsLine = circle ? roleLine(circle, centreName) : null;
  const nodeAppearances = useMemo(() => {
    if (!circle || !selectedNode) return [];
    const byAct = new Map(circle.appearances.filter((a) => a.person === selectedNode.id).map((a) => [a.act, a]));
    return sortActs(byAct.keys(), actsById).map((act) => ({ act, appearance: byAct.get(act.id)! }));
  }, [circle, selectedNode, actsById]);
  const actSigners = useMemo(() => {
    if (!circle || !selectedAct) return [];
    return circle.appearances
      .filter((a) => a.act === selectedAct.id)
      .map((appearance) => ({ appearance, node: nodesById.get(appearance.person) }))
      .filter((row): row is { appearance: NetworkAppearance; node: NetworkNode } => Boolean(row.node))
      .sort((p, q) => {
        const rank = (role: string | null) => (role === "gp" ? 0 : role === "both" ? 1 : role === "lp" ? 2 : 3);
        return rank(p.appearance.role) - rank(q.appearance.role) || p.node.name.localeCompare(q.node.name) || p.node.id - q.node.id;
      });
  }, [circle, selectedAct, nodesById]);
  const pairActs = useMemo(() => {
    if (!circle || !selectedPair?.tie) return [];
    const roleOf = new Map(circle.appearances.map((a) => [`${a.person}.${a.act}`, a]));
    return sortActs(selectedPair.tie.acts, actsById).map((act) => ({
      act,
      a: roleOf.get(`${selectedPair.a.id}.${act.id}`),
      b: roleOf.get(`${selectedPair.b.id}.${act.id}`),
    }));
  }, [circle, selectedPair, actsById]);
  const listOrder = useMemo(
    () => drawerOrder(selectedAct ? [selectedAct.id] : selectedPair ? pairActs.map((row) => row.act.id) : nodeAppearances.map((row) => row.act.id)),
    [selectedAct, selectedPair, pairActs, nodeAppearances],
  );

  // Previous / Next through the drawn entries in ring order.
  const at = selectedNode ? order.indexOf(selectedNode.id) : -1;
  const previous = at > 0 ? order[at - 1] : null;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : null;

  // A new selection shows from the column's top; a hovered mark lights its row.
  useEffect(() => {
    side.current?.scrollTo({ top: 0 });
  }, [state.select]);
  useEffect(() => {
    if (hovered === null || !side.current) return;
    side.current.querySelector<HTMLElement>(`[data-entry="${hovered}"]`)?.scrollIntoView({ block: "nearest" });
  }, [hovered]);

  const rowProps = (entryId: number) => ({
    "data-entry": entryId,
    className: hovered === entryId ? "is-lit" : undefined,
    onMouseEnter: () => setHighlight(entryId),
    onMouseLeave: () => setHighlight(null),
  });

  const hubChoices: { value: NetworkHubs; label: string; active: boolean }[] =
    state.steps === 1
      ? [
          { value: state.hubs === "off" ? "stop" : state.hubs, label: "drawn", active: state.hubs !== "off" },
          { value: "off", label: HUB_WORDS.off, active: state.hubs === "off" },
        ]
      : (["stop", "through", "off"] as NetworkHubs[]).map((value) => ({ value, label: HUB_WORDS[value], active: state.hubs === value }));

  // The two refusals the rail's controls answer: a pill sets the control
  // (the first words are fixed by step1b_contract.md §2; the server before
  // 1 November left out "itself").
  const refusal = /^This entry is (itself )?on more than/.test(error)
    ? { label: "drawn", apply: () => update({ hubs: "stop" }) }
    : /^This entry is (itself )?an institution/.test(error)
      ? { label: "Draw it", apply: () => update({ others: "on" }) }
      : null;

  const howLines = circle ? (state.years ? [...circle.how, HOW_WINDOW(windowWords(state.years)), HOW_DRAG] : circle.how) : [];
  const howCard = circle && (
    narrow ? (
      <details className="find-how nw-how">
        <summary>How this was drawn</summary>
        {howLines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </details>
    ) : (
      <aside className="find-how nw-how">
        <h3>How this was drawn</h3>
        {howLines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </aside>
    )
  );

  // The rail's window line: the counts from the mask, "of the 300 drawn" when
  // the cap cut the circle, and "All years" to clear.
  const windowLine = circle && state.years && mask && (
    <p className="nw-window">
      Acts of {windowWords(state.years)} only: {count(mask.cosigners, "co-signer", "co-signers")} on {count(mask.acts, "act", "acts")}
      {circle.cut ? `, of the ${circle.cut.drawn.toLocaleString("en-US")} drawn` : ""}.{" "}
      <button type="button" className="link-like" onClick={() => update({ years: null })}>
        {ALL_YEARS}
      </button>
    </p>
  );

  const clearButton = (
    <button type="button" className="drawer-close nw-side-close" onClick={() => setSelect(null)} aria-label="Clear the selection">
      ×
    </button>
  );

  const actRow = (act: NetworkAct, line2: string, from: number | null) => (
    <li key={act.id} {...drawerRow(act.id, drawer)}>
      <div className="nw-act-main">
        <p className="nw-act-line">
          <ContractLink id={act.id} onOpen={setDrawer} />
          {" · "}
          <span className={dateWords(act) === "no registration date" ? "muted" : undefined}>{dateWords(act)}</span>
          {" · "}
          <span className={act.firm_name ? undefined : "muted"}>{shown(act.firm_name)}</span>
        </p>
        <p className="nw-act-line muted">{line2}</p>
      </div>
      <button
        type="button"
        className="link-like nw-act-signers"
        onClick={() => {
          setState((s) => ({ ...s, select: { kind: "contract", id: act.id } }));
          setBackTo(from !== null ? { act: act.id, entry: from } : null);
        }}
      >
        {count(act.signers_live, "signer", "signers")}
      </button>
    </li>
  );

  const browserClass = ["db-browser", "nw-browser", circle ? "" : "is-bare", drawn ? "has-selection" : ""].filter(Boolean).join(" ");

  return (
    <div className={browserClass}>
      <aside className="db-rail nw-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Network</p>
          <input
            className="db-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="A person entry: a name or an entry number"
            aria-label="Search person entries"
          />
          {searchFailed && <p className="db-count error-text">The search did not run.</p>}
          {hits && (
            <ul className="db-results nw-hits">
              {hits.map((hit) => (
                <li key={hit.person_id}>
                  <button type="button" className="db-result" onClick={() => openEntry(Number(hit.person_id))}>
                    <span className="db-result-title">
                      {hit.display_name}
                      {hit.father_mother ? ` di ${hit.father_mother}` : ""}
                    </span>
                    <span className="db-result-meta">
                      entry {hit.person_id} · on {hit.appearances.toLocaleString("en-US")} act{hit.appearances === 1 ? "" : "s"}
                      {hit.residences ? ` · ${hit.residences}` : ""}
                    </span>
                  </button>
                </li>
              ))}
              {hits.length === 0 && <li className="db-empty muted">No person entry matches.</li>}
            </ul>
          )}
        </div>
        {personId !== null && (
          <div className="nw-rail-body">
            {circle ? (
              <>
                <p className="nw-sentence">{circle.sentence}</p>
                <p className="nw-answer">{circle.answer}</p>
                {windowLine}
              </>
            ) : loading ? (
              <p className="nw-answer">Loading…</p>
            ) : null}
            <div className="nw-control">
              <div className="db-tabs">
                <button type="button" className={state.steps === 1 ? "db-tab is-active" : "db-tab"} onClick={() => update({ steps: 1 })}>
                  One step
                </button>
                <button type="button" className={state.steps === 2 ? "db-tab is-active" : "db-tab"} onClick={() => update({ steps: 2 })}>
                  Two steps
                </button>
              </div>
            </div>
            <div className="nw-control">
              <p className="nw-control-label">Entries on more than 20 acts</p>
              <div className="db-tabs nw-tabs-col">
                {hubChoices.map((choice) => (
                  <button
                    key={choice.label}
                    type="button"
                    className={choice.active ? "db-tab is-active" : "db-tab"}
                    onClick={() => update({ hubs: choice.value })}
                  >
                    {choice.label}
                  </button>
                ))}
              </div>
            </div>
            <label className="nw-check">
              <input type="checkbox" checked={state.others === "off"} onChange={(event) => update({ others: event.target.checked ? "off" : "on" })} />
              <span>Leave out {OTHERS_PHRASE}</span>
            </label>
            {state.widen.length > 0 && (
              <p className="muted nw-widened">
                Widened from {state.widen.map((w) => `entry ${w}`).join(", ")}.{" "}
                <button type="button" className="link-like" onClick={() => update({ widen: [] })}>
                  Undo
                </button>
              </p>
            )}
            {dropped.length > 0 && (
              <p className="nw-dropped" title={dropped.join(", ")}>
                {DROPPED_LINE}
              </p>
            )}
          </div>
        )}
      </aside>

      <section className={loading && circle ? "nw-main is-updating" : "nw-main"}>
        {badId ? (
          <div className="nw-landing">
            <p className="eyebrow">Network</p>
            <h2>There is no entry at this address.</h2>
            <p className="muted">Search for a person entry to draw its circle.</p>
          </div>
        ) : personId === null ? (
          <div className="nw-landing">
            <p className="eyebrow">Network</p>
            <h2>The circle of one person entry</h2>
            <p>
              Search for a person entry to draw its circle: the entries that signed acts with it, and the entries that signed
              with those, two steps out.
            </p>
            <p>{STANDING_LINE}</p>
            {dropped.length > 0 && (
              <p className="muted" title={dropped.join(", ")}>
                {DROPPED_LINE}
              </p>
            )}
          </div>
        ) : error ? (
          <p className="error-text nw-error">
            {error}{" "}
            {refusal ? (
              <button type="button" className="pill-button" onClick={refusal.apply}>
                {refusal.label}
              </button>
            ) : (
              <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            )}
          </p>
        ) : !circle ? (
          <p className="muted nw-loading">Loading…</p>
        ) : (
          <>
            <NetworkFigure
              data={circle}
              hubs={state.hubs}
              selected={state.select}
              highlight={highlight}
              years={state.years}
              labelAll={labelAll}
              onSelect={setSelect}
              onHover={setHovered}
            />
            <NetworkStrip
              data={circle}
              centreName={nodesById.has(circle.centre) ? label(nodesById.get(circle.centre)!) : `entry ${circle.centre}`}
              years={state.years}
              onYears={(years) => update({ years })}
              onHoverYear={setHighlight}
            />
            <NetworkKey data={circle} labelAll={labelAll} onLabelAll={setLabelAll} />
          </>
        )}
      </section>

      {circle && (
        <aside className="nw-side" ref={side}>
          {!state.select ? (
            <section className="nw-side-block">
              <div className="nw-side-head-row">
                <p className="eyebrow">Co-signers</p>
                {cosigners.length > 1 && (
                  <div className="db-tabs nw-sort">
                    <button type="button" className={cosignerOrder === "first" ? "db-tab is-active" : "db-tab"} onClick={() => setCosignerOrder("first")}>
                      First act
                    </button>
                    <button type="button" className={cosignerOrder === "together" ? "db-tab is-active" : "db-tab"} onClick={() => setCosignerOrder("together")}>
                      Acts together
                    </button>
                  </div>
                )}
              </div>
              {(careerWords || gapWords || roleWordsLine) && (
                <p className="nw-side-facts">
                  {careerWords && <span>{careerWords} </span>}
                  {gapWords && <span>{gapWords} </span>}
                  {roleWordsLine && <span>{roleWordsLine}</span>}
                </p>
              )}
              {cosigners.length === 0 ? (
                <p className="muted nw-side-note">No other entry on its acts is drawn.</p>
              ) : (
                <ul className="nw-cosigners">
                  {cosigners.map((node) => (
                    <li key={node.id} {...rowProps(node.id)}>
                      <button type="button" className="nw-cosigner" onClick={() => setSelect({ kind: "person", id: node.id })}>
                        <span className="nw-row-name">
                          {label(node)} <span className="nw-side-id">entry {node.id}</span>
                        </span>
                        <span className="nw-row-meta">{tieWords(tiesByPair.get(pairKey(circle.centre, node.id)), actsById)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ) : selectedNode ? (
            <section className="nw-side-block">
              <header className="nw-side-head">
                <div className="nw-side-head-row">
                  <p className="eyebrow">Person entry</p>
                  {clearButton}
                </div>
                <h3>
                  {label(selectedNode)} <span className="nw-side-id">entry {selectedNode.id}</span>
                </h3>
                <p className="nw-side-facts">
                  {selectedNode.kind !== "person" && (
                    <span>
                      Recorded as {selectedNode.kind === "institution" || selectedNode.kind === "estate" ? "an" : "a"} {KIND_WORDS[selectedNode.kind]}.{" "}
                    </span>
                  )}
                  {selectedNode.woman && <span>Recorded as woman. </span>}
                  <span>
                    {selectedNode.acts_in_view === 1 ? "One act" : `${selectedNode.acts_in_view.toLocaleString("en-US")} acts`} in view
                    {yearsWords(selectedNode) ? `, ${yearsWords(selectedNode)}` : ""}
                    {selectedNode.roles.length ? `; ${selectedNode.roles.map((r) => roleLabel(r).toLowerCase()).join(" and ")}` : ""}.
                  </span>
                  {selectedNode.acts_total !== selectedNode.acts_in_view && (
                    <span>
                      {" "}
                      On {selectedNode.acts_total.toLocaleString("en-US")} acts in the corpus, {selectedNode.acts_in_view.toLocaleString("en-US")} here.
                    </span>
                  )}
                  {duplicates.has(selectedNode.name) && (
                    <span>
                      {" "}
                      Same name as{" "}
                      {circle.nodes
                        .filter((node) => node.name === selectedNode.name && node.id !== selectedNode.id)
                        .map((node) => `entry ${node.id}`)
                        .join(", ")}
                      , as entered.
                    </span>
                  )}
                </p>
                <div className="nw-side-actions">
                  {selectedNode.id !== circle.centre && (
                    <button type="button" className="pill-button" onClick={() => openEntry(selectedNode.id)}>
                      Draw its circle
                    </button>
                  )}
                  {selectedNode.hub &&
                    state.steps === 2 &&
                    state.hubs !== "through" &&
                    selectedNode.id !== circle.centre &&
                    !selectedNode.widened &&
                    !state.widen.includes(selectedNode.id) && (
                      <button type="button" className="pill-button" onClick={() => update({ widen: [...state.widen, selectedNode.id] })}>
                        Widen from this entry
                      </button>
                    )}
                  <button type="button" className="pill-button" disabled={previous === null} onClick={() => previous !== null && setSelect({ kind: "person", id: previous })}>
                    ‹ Previous
                  </button>
                  <button type="button" className="pill-button" disabled={next === null} onClick={() => next !== null && setSelect({ kind: "person", id: next })}>
                    Next ›
                  </button>
                  <Link className="nw-side-open" to={`/database/person/${selectedNode.id}`} target="_blank" rel="noopener" title={OPEN_TITLE}>
                    Open in the Database
                  </Link>
                </div>
              </header>
              <ul className="nw-acts">
                {nodeAppearances.map(({ act, appearance }) =>
                  actRow(
                    act,
                    [
                      selectedNode.id === circle.centre ? actRowLine(circle, act.id) : null,
                      roleCell(appearance),
                      jewishWords(appearance),
                      cashWords(appearance, act),
                      placeWords(act),
                      shown(act.activity),
                    ]
                      .filter(Boolean)
                      .join(" · "),
                    selectedNode.id,
                  ),
                )}
              </ul>
            </section>
          ) : selectedAct ? (
            <section className="nw-side-block">
              <header className="nw-side-head">
                {backTo && backTo.act === selectedAct.id && nodesById.has(backTo.entry) && (
                  <button type="button" className="link-like nw-side-back" onClick={() => setSelect({ kind: "person", id: backTo.entry })}>
                    ‹ Back to {label(nodesById.get(backTo.entry)!)}
                  </button>
                )}
                <div className="nw-side-head-row">
                  <p className="eyebrow">Act</p>
                  {clearButton}
                </div>
                <h3>
                  <ContractLink id={selectedAct.id} onOpen={setDrawer} />{" "}
                  <span className={selectedAct.firm_name ? undefined : "muted"}>{shown(selectedAct.firm_name)}</span>
                </h3>
                <p className="nw-side-facts">
                  {dateWords(selectedAct)} · {placeWords(selectedAct)} · {shown(selectedAct.activity)}
                  {selectedAct.total !== null ? ` · ${selectedAct.total.toLocaleString("en-US")}${selectedAct.currency ? ` ${selectedAct.currency}` : ""}` : ""}
                </p>
                {selectedAct.signers_live !== selectedAct.signers_in_view && (
                  <p className="nw-side-note">
                    {selectedAct.signers_live - selectedAct.signers_in_view === 1
                      ? "This act has one more signer, outside this circle."
                      : `${selectedAct.signers_live.toLocaleString("en-US")} signers, ${selectedAct.signers_in_view.toLocaleString("en-US")} in this circle.`}
                  </p>
                )}
              </header>
              <ul className="nw-signers">
                {actSigners.map(({ appearance, node }) => (
                  <li key={node.id} {...rowProps(node.id)}>
                    <button type="button" className="nw-cosigner" onClick={() => setSelect({ kind: "person", id: node.id })}>
                      <span className="nw-row-name">
                        {label(node)} <span className="nw-side-id">entry {node.id}</span>
                      </span>
                      <span className="nw-row-meta">
                        {[roleCell(appearance), jewishWords(appearance), cashWords(appearance, selectedAct)].filter(Boolean).join(" · ")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : selectedPair ? (
            <section className="nw-side-block">
              <header className="nw-side-head">
                <div className="nw-side-head-row">
                  <p className="eyebrow">Two entries</p>
                  {clearButton}
                </div>
                <h3>
                  {label(selectedPair.a)} <span className="nw-side-id">entry {selectedPair.a.id}</span>
                  <br />
                  {label(selectedPair.b)} <span className="nw-side-id">entry {selectedPair.b.id}</span>
                </h3>
                <p className="nw-side-facts">{tieWords(selectedPair.tie, actsById)}.</p>
              </header>
              <ul className="nw-acts">
                {pairActs.map(({ act, a, b }) =>
                  actRow(
                    act,
                    `${label(selectedPair.a)} ${a ? [roleCell(a), jewishWords(a)].filter(Boolean).join(", ") : "no role recorded"} · ${label(selectedPair.b)} ${b ? [roleCell(b), jewishWords(b)].filter(Boolean).join(", ") : "no role recorded"}`,
                    null,
                  ),
                )}
              </ul>
            </section>
          ) : (
            <section className="nw-side-block">
              <p className="muted nw-side-note">
                {outsideWords(state.select)}{" "}
                {state.select.kind === "person" && (
                  <button type="button" className="link-like" onClick={() => openEntry((state.select as { id: number }).id)}>
                    Draw its circle
                  </button>
                )}
              </p>
              <p className="nw-side-actions">{clearButton}</p>
            </section>
          )}
          {howCard}
        </aside>
      )}

      {drawer !== null && <ContractDrawer id={drawer} order={listOrder} onMove={setDrawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
