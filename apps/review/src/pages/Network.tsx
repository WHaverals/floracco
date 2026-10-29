import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { loadNetworkCircle, searchPersonsRich } from "../api";
import ContractDrawer, { ContractLink, drawerOrder } from "../components/ContractDrawer";
import NetworkFigure, { KIND_WORDS, NetworkKey, appearanceNotes, roleWords } from "../components/NetworkFigure";
import type { NetworkAct, NetworkAppearance, NetworkCircle, NetworkNode, PersonHit } from "../types";
import { useEscapeLayer } from "../utils/escapeLayers";
import { useLatest } from "../utils/latest";
import { layoutCircle } from "../utils/networkLayout";
import {
  DEFAULT_NETWORK,
  HUB_WORDS,
  OTHERS_PHRASE,
  circleKey,
  networkSearch,
  parseNetworkSearch,
  type NetworkHubs,
  type NetworkSelect,
  type NetworkState,
} from "../utils/networkSpec";
import { roleLabel } from "../utils/roles";

/* The Network view, step 1 (docs/network/scope_2026-10-29.md §4.8, the
 * contract in docs/network/step1_contract.md): the circle of one person
 * entry as entered, two steps out, from GET /api/network/circle/{id}. The page
 * follows the Database page: the dark rail on the left (the search, the
 * sentence and the answer line, the controls), the figure in the middle with
 * How this was drawn under it, and the side table on the right with what is
 * selected. The state lives in the address (utils/networkSpec.ts). In
 * development, `?fixture=1` draws the hand-written fixture instead of asking
 * the server. */

const FIXTURE_KEY = "fixture";
const STANDING_LINE = "Person entries are counted as entered; one person may have several.";

function withFixture(search: string, fixture: boolean): string {
  if (!fixture) return search;
  return search ? `${search}&${FIXTURE_KEY}=1` : `${FIXTURE_KEY}=1`;
}

/** A stored value as the side table writes it: blanks read "not recorded". */
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

function roleCell(appearance: NetworkAppearance): string {
  const role = appearance.role ? (appearance.role === "both" ? roleWords("both") : roleLabel(appearance.role)) : "no live stake";
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

function sortActs(ids: Iterable<number>, actsById: Map<number, NetworkAct>): NetworkAct[] {
  const date = (act: NetworkAct) => (!act.date || act.date.startsWith("0000") ? "9999-99-99" : act.date);
  return [...new Set(ids)]
    .map((id) => actsById.get(id))
    .filter((act): act is NetworkAct => Boolean(act))
    .sort((p, q) => (date(p) < date(q) ? -1 : date(p) > date(q) ? 1 : p.id - q.id));
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
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<PersonHit[] | null>(null);
  const [searchFailed, setSearchFailed] = useState(false);
  const written = useRef<string | null>(null);
  const beginLoad = useLatest();
  const beginSearch = useLatest();

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

  // The circle, from the server (or the fixture in development).
  const key = personId !== null ? circleKey(personId, state) : null;
  useEffect(() => {
    if (personId === null || key === null) {
      setCircle(null);
      setError("");
      return;
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

  const layout = useMemo(() => (circle ? layoutCircle(circle) : null), [circle]);
  const actsById = useMemo(() => new Map((circle?.acts ?? []).map((act) => [act.id, act])), [circle]);
  const nodesById = useMemo(() => new Map((circle?.nodes ?? []).map((node) => [node.id, node])), [circle]);

  const setSelect = (select: NetworkSelect | null) => setState((s) => ({ ...s, select }));
  const update = (next: Partial<NetworkState>) => setState((s) => ({ ...s, ...next }));
  useEscapeLayer(state.select !== null && drawer === null, () => setSelect(null));

  // Opening another entry's circle keeps the controls and drops the selection.
  const openEntry = (entryId: number) => {
    const controls = withFixture(networkSearch({ ...state, select: null }), fixture);
    setQuery("");
    setHits(null);
    navigate(`/network/person/${entryId}${controls ? `?${controls}` : ""}`);
  };

  // What the side table lists.
  const selectedNode = state.select?.kind === "person" ? nodesById.get(state.select.id) ?? null : null;
  const selectedAct = state.select?.kind === "contract" ? actsById.get(state.select.id) ?? null : null;
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
        const order = (role: string | null) => (role === "gp" ? 0 : role === "both" ? 1 : role === "lp" ? 2 : 3);
        return order(p.appearance.role) - order(q.appearance.role) || p.node.name.localeCompare(q.node.name) || p.node.id - q.node.id;
      });
  }, [circle, selectedAct, nodesById]);
  const order = useMemo(
    () => drawerOrder(selectedAct ? [selectedAct.id] : nodeAppearances.map((row) => row.act.id)),
    [selectedAct, nodeAppearances],
  );

  const hubChoices: { value: NetworkHubs; label: string }[] = [
    { value: "stop", label: HUB_WORDS.stop },
    { value: "through", label: HUB_WORDS.through },
    { value: "off", label: HUB_WORDS.off },
  ];

  return (
    <div className="db-browser nw-browser">
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
            {circle && (
              <>
                <p className="nw-sentence">{circle.sentence}</p>
                <p className="nw-answer">{circle.answer}</p>
              </>
            )}
            <div className="nw-control">
              <p className="nw-control-label">Steps</p>
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
                    key={choice.value}
                    type="button"
                    className={state.hubs === choice.value ? "db-tab is-active" : "db-tab"}
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
            <label className="nw-check">
              <input type="checkbox" checked={labelAll} onChange={(event) => setLabelAll(event.target.checked)} />
              <span>Label all</span>
            </label>
            {state.widen.length > 0 && (
              <p className="muted nw-widened">
                Widened by hand: {state.widen.map((w) => `entry ${w}`).join(", ")}.{" "}
                <button type="button" className="link-like" onClick={() => update({ widen: [] })}>
                  Undo
                </button>
              </p>
            )}
          </div>
        )}
      </aside>

      <section className="nw-main">
        {dropped.length > 0 && (
          <p className="muted an-note">Part of this link no longer matches and was left out: {dropped.join(", ")}.</p>
        )}
        {badId ? (
          <div className="nw-landing">
            <p className="eyebrow">Network</p>
            <h2>There is no entry at this address.</h2>
            <p className="muted">Search for a person entry in the rail to draw its circle.</p>
          </div>
        ) : personId === null ? (
          <div className="nw-landing">
            <p className="eyebrow">Network</p>
            <h2>People as nodes, the acts they signed together as ties</h2>
            <p>
              Search for a person entry to draw its circle: the entries that signed acts with it, and the entries that signed
              with those, two steps out.
            </p>
            <p>
              Entries as entered, not yet reviewed as people; a tie is an act signed together. {STANDING_LINE}
            </p>
            <p>
              Entries on more than 20 acts are drawn but not widened through unless asked; the line at 20 is a readability
              cut, not a break in the data. Hidden records are left out. Position is the layout&rsquo;s and nothing more.
            </p>
          </div>
        ) : error ? (
          <p className="error-text nw-error">
            {error}{" "}
            <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </p>
        ) : !circle || !layout ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <div className={loading ? "nw-stage-wrap is-updating" : "nw-stage-wrap"}>
              <NetworkFigure data={circle} layout={layout} hubs={state.hubs} selected={state.select} labelAll={labelAll} onSelect={setSelect} />
              <NetworkKey data={circle} />
            </div>
            <aside className="find-how nw-how">
              <h3>How this was drawn</h3>
              {circle.how.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </aside>
          </>
        )}
      </section>

      <aside className="nw-side">
        {personId === null || !circle ? null : !state.select ? (
          <p className="muted nw-side-empty">Select an entry in the circle to list its acts here.</p>
        ) : selectedNode ? (
          <>
            <header className="nw-side-head">
              <p className="eyebrow">Person entry</p>
              <h3>
                <Link to={`/database/person/${selectedNode.id}`}>{selectedNode.name}</Link>{" "}
                <span className="nw-side-id">entry {selectedNode.id}</span>
              </h3>
              <p className="nw-side-facts">
                {selectedNode.kind !== "person" && <span>Recorded as {selectedNode.kind === "institution" || selectedNode.kind === "estate" ? "an" : "a"} {KIND_WORDS[selectedNode.kind]}. </span>}
                {selectedNode.woman && <span>Recorded as woman. </span>}
                {selectedNode.hub && (
                  <span>
                    On {selectedNode.acts_total.toLocaleString("en-US")} acts in the corpus; {HUB_WORDS[state.hubs]}.{" "}
                  </span>
                )}
                <span>
                  {selectedNode.acts_in_view === 1 ? "One act" : `${selectedNode.acts_in_view.toLocaleString("en-US")} acts`} in view
                  {yearsWords(selectedNode) ? `, ${yearsWords(selectedNode)}` : ""}
                  {selectedNode.roles.length ? `; ${selectedNode.roles.map((r) => roleLabel(r).toLowerCase()).join(" and ")}` : ""}.
                </span>
              </p>
              <div className="nw-side-actions">
                {selectedNode.id !== circle.centre && (
                  <button type="button" className="pill-button" onClick={() => openEntry(selectedNode.id)}>
                    Draw the circle of this entry
                  </button>
                )}
                <button type="button" className="pill-button" onClick={() => setSelect(null)}>
                  Clear
                </button>
              </div>
            </header>
            <table className="db-table nw-table">
              <thead>
                <tr>
                  <th>Act</th>
                  <th>Date</th>
                  <th>Firm name</th>
                  <th>Place</th>
                  <th>Activity</th>
                  <th>Role on the stake</th>
                  <th>Cash</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {nodeAppearances.map(({ act, appearance }) => (
                  <tr key={act.id}>
                    <td>
                      <ContractLink id={act.id} onOpen={setDrawer} />
                    </td>
                    <td className={dateWords(act) === "no registration date" ? "muted" : undefined}>{dateWords(act)}</td>
                    <td className={act.firm_name ? undefined : "muted"}>{shown(act.firm_name)}</td>
                    <td className={act.places.length ? undefined : "muted"}>{placeWords(act)}</td>
                    <td className={act.activity ? undefined : "muted"}>{shown(act.activity)}</td>
                    <td>{roleCell(appearance)}</td>
                    <td className={appearance.cash === null && !appearance.non_cash ? "muted" : undefined}>{cashWords(appearance, act)}</td>
                    <td>
                      <button type="button" className="link-like" onClick={() => setSelect({ kind: "contract", id: act.id })}>
                        Signers
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : selectedAct ? (
          <>
            <header className="nw-side-head">
              <p className="eyebrow">Act</p>
              <h3>
                <ContractLink id={selectedAct.id} onOpen={setDrawer} />{" "}
                <span className={selectedAct.firm_name ? undefined : "muted"}>{shown(selectedAct.firm_name)}</span>
              </h3>
              <p className="nw-side-facts">
                {dateWords(selectedAct)} · {placeWords(selectedAct)} · {shown(selectedAct.activity)}
                {selectedAct.total !== null ? ` · ${selectedAct.total.toLocaleString("en-US")}${selectedAct.currency ? ` ${selectedAct.currency}` : ""}` : ""}
              </p>
              <div className="nw-side-actions">
                <button type="button" className="pill-button" onClick={() => setSelect(null)}>
                  Clear
                </button>
              </div>
            </header>
            <table className="db-table nw-table">
              <thead>
                <tr>
                  <th>Signer</th>
                  <th>Role on the stake</th>
                  <th>Cash</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {actSigners.map(({ appearance, node }) => (
                  <tr key={node.id}>
                    <td>
                      <Link to={`/database/person/${node.id}`}>{node.name}</Link> <span className="nw-side-id">entry {node.id}</span>
                    </td>
                    <td>{roleCell(appearance)}</td>
                    <td className={appearance.cash === null && !appearance.non_cash ? "muted" : undefined}>{cashWords(appearance, selectedAct)}</td>
                    <td>
                      <button type="button" className="link-like" onClick={() => setSelect({ kind: "person", id: node.id })}>
                        Select
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selectedAct.signers_live > selectedAct.signers_in_view && (
              <p className="muted nw-side-note">
                {selectedAct.signers_live - selectedAct.signers_in_view === 1
                  ? "One more live signer of this act is not in this picture."
                  : `${(selectedAct.signers_live - selectedAct.signers_in_view).toLocaleString("en-US")} more live signers of this act are not in this picture.`}
              </p>
            )}
          </>
        ) : (
          <p className="muted nw-side-empty">
            {state.select.kind === "person" ? `Entry ${state.select.id}` : `Act ${state.select.id}`} is not in this picture.{" "}
            {state.select.kind === "person" && (
              <button type="button" className="link-like" onClick={() => openEntry(state.select!.id)}>
                Draw its circle
              </button>
            )}
          </p>
        )}
      </aside>

      {drawer !== null && <ContractDrawer id={drawer} order={order} onMove={setDrawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
