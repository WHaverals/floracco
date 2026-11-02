import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { loadNetworkChain, loadNetworkCircle, loadNetworkFirm, searchPersonsRich } from "../api";
import ChainStrip, { chainOrder, type ChainHover } from "../components/ChainStrip";
import ContractDrawer, { ContractLink, drawerOrder, drawerRow } from "../components/ContractDrawer";
import FirmFigure, { FirmKey } from "../components/FirmFigure";
import NetworkFigure, { KIND_WORDS, NetworkKey, appearanceNotes, roleWords, type YearCard } from "../components/NetworkFigure";
import NetworkLanding from "../components/NetworkLanding";
import NetworkStrip, { type StripHover } from "../components/NetworkStrip";
import type {
  NetworkAct,
  NetworkAppearance,
  NetworkChain,
  NetworkChainRun,
  NetworkCircle,
  NetworkFirm,
  NetworkFirmAct,
  NetworkFirmCell,
  NetworkFirmChange,
  NetworkFirmRow,
  NetworkNode,
  NetworkTie,
  PersonHit,
} from "../types";
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
 * window line with "All years", and the How card two more lines.
 *
 * Step 2c (step2c_contract.md §2): the reading card in the stage's corner
 * lives in the figure; the page feeds it what the figure cannot see, the
 * side-column row under the pointer (`highlight`) and the strip year under it
 * (`yearCard`, with the year's signers lit as before).
 *
 * Step 3 (docs/network/step3_contract.md §4): the firm view. "Follow this firm
 * name" on an act row or in the act block's head sets `firm=` in the address;
 * while it is set the figure column shows the registrations carrying that
 * stored name (components/FirmFigure.tsx, from GET /api/network/firm), the
 * rail the firm's sentence and answer with "‹ Back to the circle", and the
 * side column the registrations at rest, or the selected entry or act over
 * the firm's rows. The circle's keys stay in the address, so Back returns the
 * circle as it was; a link with `firm=` and no entry shows the firm alone.
 *
 * Step 4 (docs/network/step4_contract.md §4): the chain between the centre and
 * a second entry chosen in the rail's second search box, `to=` in the address
 * (components/ChainStrip.tsx, from GET /api/network/chain/{a}/{b}). While it is
 * set the rail carries the chain's sentence and answer in the circle's place,
 * the pills "Forward in time" · "Any order" when both chains exist (`chain=any`)
 * and "× No chain"; the strip stands between the stage and the time strip; the
 * chain's entries are lit on the circle; the How card adds the chain's lines.
 * The circle's keys stay as they are, so the chain reads over the circle. */

const FIXTURE_KEY = "fixture";
/** The landing's heading and its one instruction (docs/ui_review/2026-11-01_network_landing.md L1, L2). */
const LANDING_HEADING = "Who signed with whom";
const LANDING_LINE = "Search for a person entry to draw its circle, or start from a name in the lists below.";
const DROPPED_LINE = "Part of this link asks for a setting this page does not offer; it was left out.";
const OPEN_TITLE = "The full record, where it can be edited, in a new tab";
const ALL_YEARS = "All years";
/** The How card's lines while a window is set (next_2026-11-01.md §4.2). */
const HOW_WINDOW = (window: string) =>
  `Only acts registered ${window} make ties, and a tie's width counts those acts. An entry with no act in those years is faded and keeps its place.`;
const HOW_DRAG = "Drag the end earlier to see the circle as it stood in that year.";
/** The firm view's entry point on act rows and in the act block's head, and its way back. */
const FOLLOW_FIRM = "Follow this firm name";
const BACK_TO_CIRCLE = "‹ Back to the circle";
/** The chain's rail (step 4): the second box, the two pills and the way out. */
const CHAIN_PLACEHOLDER = "A second entry, for the chain";
const FORWARD_PILL = "Forward in time";
const ANY_PILL = "Any order";
const NO_CHAIN = "× No chain";
/** The standing line every Network answer ends with (step1b_contract.md §2). */
const STANDING_LINE = "Person entries are counted as entered; one person may have several.";
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

function dateWords(act: Pick<NetworkAct, "date">): string {
  return !act.date || act.date.startsWith("0000") ? "no registration date" : act.date;
}

function placeWords(act: Pick<NetworkAct, "places" | "places_inferred">): string {
  if (act.places.length === 0) return "not recorded";
  return `${act.places.join("; ")}${act.places_inferred ? " (marked as inferred)" : ""}`;
}

function cashWords(appearance: Pick<NetworkAppearance, "cash" | "non_cash">, act: Pick<NetworkAct, "currency"> | undefined): string {
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

/** The stored firm name an act row can be followed by: trimmed of ASCII and
 * non-breaking space at the ends (the server trims the same way); null when blank. */
function firmNameOf(act: Pick<NetworkAct, "firm_name">): string | null {
  const name = (act.firm_name ?? "").replace(/^[\s\u00a0]+|[\s\u00a0]+$/g, "");
  return name || null;
}

/** A firm cell as an appearance, for the words the circle's rows use. */
function cellAppearance(cell: NetworkFirmCell, rowId: number, actId: number): NetworkAppearance {
  return { person: rowId, act: actId, ...cell };
}

/** "renewal 1662-09-10 · termination 1700-01-05": a registration's later acts, as stored. */
function laterWords(act: NetworkFirmAct): string {
  return act.later.map((later) => `${later.kind.trim() || "later act"} ${dateWords(later)}`).join(" · ");
}

/** "1659 → 1664: 4 stayed, 1 left, 1 joined" between two consecutive dated registrations. */
function changeLine(change: NetworkFirmChange, actsById: Map<number, NetworkFirmAct>): string {
  const year = (id: number) => actsById.get(id)?.year ?? "no date";
  return `${year(change.from)} → ${year(change.to)}: ${change.stayed} stayed, ${change.left} left, ${change.joined} joined`;
}

/** "3 acts under this firm name, 1669–1690; general partner" for the firm's entry block. */
function firmRowFacts(row: NetworkFirmRow, acts: NetworkFirmAct[]): string {
  const mine = acts.filter((act) => row.cells[String(act.id)]);
  const years = mine.map((act) => act.year).filter((y): y is number => typeof y === "number" && y > 0);
  const span = years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "";
  const undated = mine.length - years.length;
  const roles = [...new Set(mine.map((act) => row.cells[String(act.id)].role).filter((role): role is string => Boolean(role)))]
    .flatMap((role) => (role === "both" ? ["gp", "lp"] : [role]))
    .filter((role, i, all) => all.indexOf(role) === i)
    .sort()
    .map((role) => roleLabel(role).toLowerCase())
    .join(" and ");
  const when = [span, undated > 0 ? `${undated} with no registration date` : ""].filter(Boolean).join("; ");
  return `${mine.length === 1 ? "One act" : `${mine.length.toLocaleString("en-US")} acts`} under this firm name${when ? `, ${when}` : ""}${roles ? `; ${roles}` : ""}.`;
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

/** The untimed chain's answer while a forward one exists and the reader asks
 * for "Any order": the contract's second sentence for a pair with no forward
 * chain, then the standing line. The server's answer describes the forward one. */
function anyAnswer(run: NetworkChainRun): string {
  const n = run.hops.length;
  const acts = n === 1 ? "one act" : `${n.toLocaleString("en-US")} acts`;
  const equal = run.equal > 1 ? `, one of ${run.equal.toLocaleString("en-US")} equally short chains` : "";
  const hubs = !run.touches_hubs
    ? ""
    : run.avoiding_hubs === n
      ? "; one of them avoids the entries on more than 20 acts"
      : "; every one of them passes through an entry on more than 20 acts";
  return `Through any acts, in any order: ${acts}${equal}${hubs}. ${STANDING_LINE}`;
}

/** The person search behind a rail box: the hits for a term of two or more
 * characters, 250 ms after the last keystroke; null with no term. */
function usePersonSearch(query: string): { hits: PersonHit[] | null; failed: boolean } {
  const [hits, setHits] = useState<PersonHit[] | null>(null);
  const [failed, setFailed] = useState(false);
  const begin = useLatest();
  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setHits(null);
      setFailed(false);
      begin();
      return;
    }
    const fresh = begin();
    const handle = window.setTimeout(() => {
      searchPersonsRich(term, 12)
        .then((res) => fresh() && setHits(res.results))
        .catch(() => fresh() && setFailed(true));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [query, begin]);
  return { hits, failed };
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
  /** the strip year under the pointer, for the card; set with `highlight` */
  const [yearCard, setYearCard] = useState<YearCard | null>(null);
  /** the entry an act was opened from, for "‹ Back to {name}" */
  const [backTo, setBackTo] = useState<{ act: number; entry: number } | null>(null);
  /** the firm view's data while `firm=` is set */
  const [firmData, setFirmData] = useState<NetworkFirm | null>(null);
  const [firmLoading, setFirmLoading] = useState(false);
  const [firmError, setFirmError] = useState("");
  const [firmAttempt, setFirmAttempt] = useState(0);
  /** the chain's data while `to=` is set beside an entry */
  const [chainData, setChainData] = useState<NetworkChain | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [chainError, setChainError] = useState("");
  const [chainAttempt, setChainAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [chainQuery, setChainQuery] = useState("");
  const search = usePersonSearch(query);
  const chainSearch = usePersonSearch(chainQuery);
  const written = useRef<string | null>(null);
  const loadedFor = useRef<number | null>(null);
  const side = useRef<HTMLElement | null>(null);
  const beginLoad = useLatest();
  const beginFirm = useLatest();
  const beginChain = useLatest();
  const firmFor = useRef<string | null>(null);
  const chainFor = useRef<string | null>(null);
  const narrow = useMedia(NARROW);

  // Read a link (or Back/Forward) into the state; our own writes never re-read.
  const address = location.search.replace(/^\?/, "");
  useEffect(() => {
    if (address === written.current) return;
    const parsed = parseNetworkSearch(address, [FIXTURE_KEY]);
    const useFixture = Boolean(import.meta.env.DEV) && new URLSearchParams(address).get(FIXTURE_KEY) === "1";
    if (!import.meta.env.DEV && new URLSearchParams(address).has(FIXTURE_KEY)) parsed.dropped.push(`“${FIXTURE_KEY}”`);
    setState(parsed.state);
    setDropped(parsed.dropped);
    setFixture(useFixture);
    written.current = withFixture(networkSearch(parsed.state), useFixture);
  }, [address]);

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

  // The firm view, from the server (or the fixture in development), while
  // `firm=` is set; another name starts from nothing, a retry keeps the drawing.
  useEffect(() => {
    const name = state.firm;
    if (!name) {
      setFirmData(null);
      setFirmError("");
      setFirmLoading(false);
      firmFor.current = null;
      beginFirm();
      return;
    }
    if (firmFor.current !== name) {
      setFirmData(null);
      firmFor.current = name;
    }
    const fresh = beginFirm();
    setFirmLoading(true);
    setFirmError("");
    const request = fixture
      ? import("../dev/network-firm-fixture.json").then((module) => module.default as unknown as NetworkFirm)
      : loadNetworkFirm(name);
    request
      .then((data) => {
        if (!fresh()) return;
        setFirmData(data);
        setFirmLoading(false);
      })
      .catch((e: Error) => {
        if (!fresh()) return;
        setFirmError(e.message);
        setFirmData(null);
        setFirmLoading(false);
      });
  }, [state.firm, fixture, firmAttempt, beginFirm]);

  // The chain, from the server (or the fixture in development), while `to=` is
  // set beside an entry; another pair starts from nothing, a retry keeps the strip.
  const chainPair = personId !== null && state.to !== null ? `${personId}.${state.to}` : null;
  useEffect(() => {
    if (chainPair === null) {
      setChainData(null);
      setChainError("");
      setChainLoading(false);
      chainFor.current = null;
      beginChain();
      return;
    }
    if (chainFor.current !== chainPair) {
      setChainData(null);
      chainFor.current = chainPair;
    }
    const [a, b] = chainPair.split(".").map(Number);
    const fresh = beginChain();
    setChainLoading(true);
    setChainError("");
    const request = fixture
      ? import("../dev/network-chain-fixture.json").then((module) => module.default as unknown as NetworkChain)
      : loadNetworkChain(a, b);
    request
      .then((data) => {
        if (!fresh()) return;
        setChainData(data);
        setChainLoading(false);
      })
      .catch((e: Error) => {
        if (!fresh()) return;
        setChainError(e.message);
        setChainData(null);
        setChainLoading(false);
      });
  }, [chainPair, fixture, chainAttempt, beginChain]);

  const actsById = useMemo(() => new Map((circle?.acts ?? []).map((act) => [act.id, act])), [circle]);
  const nodesById = useMemo(() => new Map((circle?.nodes ?? []).map((node) => [node.id, node])), [circle]);
  const tiesByPair = useMemo(() => new Map((circle?.ties ?? []).map((tie) => [pairKey(tie.a, tie.b), tie])), [circle]);
  const duplicates = useMemo(() => duplicateNames(circle?.nodes ?? []), [circle]);
  const order = useMemo(() => (circle ? ringOrder(circle) : []), [circle]);
  const label = (node: NetworkNode) => nodeLabel(node, duplicates);
  /** the window's counts for the rail line; the figure applies the same mask */
  const mask = useMemo(() => (circle && state.years ? windowTies(circle, state.years) : null), [circle, state.years]);
  // A circle drawn afresh carries no lit entries over.
  useEffect(() => {
    setHighlight(null);
    setYearCard(null);
  }, [circle]);
  const hoverYear = (hover: StripHover | null) => {
    setHighlight(hover ? hover.ids : null);
    setYearCard(hover ? { head: hover.head, line: hover.line } : null);
  };
  /** a mark or a hop under the pointer on the chain strip: its entries lit, its words on the card */
  const hoverChain = (hover: ChainHover | null) => {
    setHighlight(hover ? hover.ids : null);
    setYearCard(hover ? { head: hover.head, line: hover.line } : null);
  };

  const setSelect = (select: NetworkSelect | null) => {
    setBackTo(null);
    setState((s) => ({ ...s, select }));
  };
  const update = (next: Partial<NetworkState>) => setState((s) => ({ ...s, ...next }));
  useEscapeLayer(state.select !== null && drawer === null, () => setSelect(null));

  // Opening another entry's circle keeps the controls and the chain's second
  // entry (the chain then reads from the new centre; opening the second entry
  // itself turns the chain round) and drops the selection, the window (another
  // entry's acts lie in other years) and the firm; it is pushed, so Back
  // returns to this circle.
  const openEntry = (entryId: number) => {
    const to = entryId === state.to ? personId : state.to;
    const controls = withFixture(networkSearch({ ...state, select: null, years: null, firm: null, to }), fixture);
    setQuery("");
    setChainQuery("");
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

  // The firm view's lookups and what its side column shows: a selected entry
  // or act the firm holds; any other selection reads as rest.
  const firm = state.firm !== null;
  const firmActsById = useMemo(() => new Map((firmData?.acts ?? []).map((act) => [act.id, act])), [firmData]);
  const firmRowsById = useMemo(() => new Map((firmData?.rows ?? []).map((row) => [row.id, row])), [firmData]);
  const firmDuplicates = useMemo(() => duplicateNames(firmData?.rows ?? []), [firmData]);
  const firmLabel = (row: NetworkFirmRow) => nodeLabel(row, firmDuplicates);
  const firmRow = firm && state.select?.kind === "person" ? firmRowsById.get(state.select.id) ?? null : null;
  const firmAct = firm && state.select?.kind === "contract" ? firmActsById.get(state.select.id) ?? null : null;
  const firmDrawn = firmRow !== null || firmAct !== null;
  const firmRowActs = useMemo(
    () => (firmData && firmRow ? firmData.acts.filter((act) => firmRow.cells[String(act.id)]).map((act) => ({ act, cell: firmRow.cells[String(act.id)] })) : []),
    [firmData, firmRow],
  );
  const firmActSigners = useMemo(() => {
    if (!firmData || !firmAct) return [];
    const rank = (role: string | null) => (role === "gp" ? 0 : role === "both" ? 1 : role === "lp" ? 2 : 3);
    return firmData.rows
      .filter((row) => row.cells[String(firmAct.id)])
      .map((row) => ({ row, cell: row.cells[String(firmAct.id)] }))
      .sort((p, q) => rank(p.cell.role) - rank(q.cell.role) || p.row.name.localeCompare(q.row.name) || p.row.id - q.row.id);
  }, [firmData, firmAct]);
  const firmChangeByFrom = useMemo(() => new Map((firmData?.changes ?? []).map((change) => [change.from, change])), [firmData]);
  const followFirm = (act: Pick<NetworkAct, "firm_name">) => {
    const name = firmNameOf(act);
    if (name) update({ firm: name });
  };
  /** the firm's data while the firm view is up; the circle's otherwise */
  const shownData = firm ? firmData : circle;

  // The chain (step 4) over the circle: the run shown is the forward one when
  // it exists, the untimed one when none does or when the reader asks for it;
  // its entries are lit on the circle while nothing else is.
  const chainOn = !firm && personId !== null && state.to !== null;
  const chainRun: NetworkChainRun | null = chainData ? (state.chain === "any" && chainData.any ? chainData.any : (chainData.forward ?? chainData.any)) : null;
  const chainForward = chainRun !== null && chainRun === chainData?.forward;
  const chainIds = useMemo(() => new Set(chainRun ? chainOrder(chainRun) : []), [chainRun]);
  const chainAnswer = chainData && chainRun ? (chainForward || !chainData.forward ? chainData.answer : anyAnswer(chainRun)) : chainData?.answer ?? "";
  const drawnIds = useMemo(() => new Set(nodesById.keys()), [nodesById]);
  const figureHighlight = highlight ?? (chainOn && chainIds.size > 0 ? chainIds : null);
  /** a mark on the strip: selected when the circle draws it, else its circle opens with the chain kept */
  const clickChainMark = (id: number) => {
    if (nodesById.has(id)) setSelect({ kind: "person", id });
    else openEntry(id);
  };

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
    () =>
      drawerOrder(
        firm
          ? firmAct
            ? [firmAct.id]
            : firmRow
              ? firmRowActs.map((row) => row.act.id)
              : (firmData?.acts ?? []).map((act) => act.id)
          : selectedAct
            ? [selectedAct.id]
            : selectedPair
              ? pairActs.map((row) => row.act.id)
              : selectedNode
                ? nodeAppearances.map((row) => row.act.id)
                : chainOn && chainRun
                  ? chainRun.hops.map((hop) => hop.act.id)
                  : [],
      ),
    [firm, firmAct, firmRow, firmRowActs, firmData, selectedAct, selectedPair, pairActs, selectedNode, nodeAppearances, chainOn, chainRun],
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

  const howLines = firm
    ? (firmData?.how ?? [])
    : circle
      ? [
          ...(state.years ? [...circle.how, HOW_WINDOW(windowWords(state.years)), HOW_DRAG] : circle.how),
          // the chain's lines after the circle's; a line the circle already carries (the hidden-rows line) once
          ...(chainOn && chainData ? chainData.how.filter((line) => !circle.how.includes(line)) : []),
        ]
      : [];
  const howCard = shownData && (
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
        <p className="nw-act-line muted">
          {line2}
          {firmNameOf(act) && (
            <>
              {" · "}
              <button type="button" className="link-like nw-firm-follow" onClick={() => followFirm(act)}>
                {FOLLOW_FIRM}
              </button>
            </>
          )}
        </p>
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

  /** A registration's row in the firm view: the act number, date, places and
   * activity; its later acts on a muted second line; "{n} signers" selects it. */
  const firmActRow = (act: NetworkFirmAct, line2: string | null, from: number | null) => (
    <li key={act.id} {...drawerRow(act.id, drawer)}>
      <div className="nw-act-main">
        <p className="nw-act-line">
          <ContractLink id={act.id} onOpen={setDrawer} />
          {" · "}
          <span className={dateWords(act) === "no registration date" ? "muted" : undefined}>{dateWords(act)}</span>
          {" · "}
          {placeWords(act)}
          {" · "}
          {shown(act.activity)}
        </p>
        {line2 && <p className="nw-act-line muted">{line2}</p>}
        {act.later.length > 0 && <p className="nw-act-line muted nw-firm-later">{laterWords(act)}</p>}
      </div>
      <button
        type="button"
        className="link-like nw-act-signers"
        onClick={() => {
          setState((s) => ({ ...s, select: { kind: "contract", id: act.id } }));
          setBackTo(from !== null ? { act: act.id, entry: from } : null);
        }}
      >
        {count(act.signers, "signer", "signers")}
      </button>
    </li>
  );

  /** The hits under a rail box, the same list for both boxes. */
  const hitsList = (hits: PersonHit[], onPick: (id: number) => void) => (
    <ul className="db-results nw-hits">
      {hits.map((hit) => (
        <li key={hit.person_id}>
          <button type="button" className="db-result" onClick={() => onPick(Number(hit.person_id))}>
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
  );

  const browserClass = ["db-browser", "nw-browser", shownData ? "" : "is-bare", (firm ? firmDrawn : drawn) ? "has-selection" : ""].filter(Boolean).join(" ");

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
          {search.failed && <p className="db-count error-text">The search did not run.</p>}
          {search.hits && hitsList(search.hits, (hitId) => openEntry(hitId))}
          {personId !== null && (
            <>
              <input
                className="db-search nw-chain-search"
                type="search"
                value={chainQuery}
                onChange={(event) => setChainQuery(event.target.value)}
                placeholder={CHAIN_PLACEHOLDER}
                aria-label="Search a second person entry, for the chain"
              />
              {chainSearch.failed && <p className="db-count error-text">The search did not run.</p>}
              {chainSearch.hits &&
                hitsList(chainSearch.hits, (hitId) => {
                  setChainQuery("");
                  update({ to: hitId, chain: null });
                })}
            </>
          )}
        </div>
        {(personId !== null || firm) && (
          <div className="nw-rail-body">
            {firm ? (
              <>
                {firmData ? (
                  <>
                    <p className="nw-sentence">{firmData.sentence}</p>
                    <p className="nw-answer">{firmData.answer}</p>
                  </>
                ) : firmLoading ? (
                  <p className="nw-answer">Loading…</p>
                ) : null}
                {personId !== null && (
                  <p className="nw-firm-back">
                    <button type="button" className="link-like" onClick={() => update({ firm: null })}>
                      {BACK_TO_CIRCLE}
                    </button>
                  </p>
                )}
              </>
            ) : circle ? (
              <>
                {chainOn && chainData ? (
                  <>
                    <p className="nw-sentence">{chainData.sentence}</p>
                    <p className="nw-answer">{chainAnswer}</p>
                  </>
                ) : (
                  <>
                    <p className="nw-sentence">{circle.sentence}</p>
                    {chainOn && chainError ? (
                      <p className="nw-answer nw-chain-error">
                        {chainError}{" "}
                        <button type="button" className="link-like" onClick={() => setChainAttempt((n) => n + 1)}>
                          Try again
                        </button>
                      </p>
                    ) : chainOn && chainLoading ? (
                      <p className="nw-answer">Loading…</p>
                    ) : (
                      <p className="nw-answer">{circle.answer}</p>
                    )}
                  </>
                )}
                {chainOn && (
                  <div className="nw-chain-rail">
                    {chainData?.forward && chainData.any && (
                      <div className="db-tabs nw-chain-pills">
                        <button type="button" className={state.chain === "any" ? "db-tab" : "db-tab is-active"} onClick={() => update({ chain: null })}>
                          {FORWARD_PILL}
                        </button>
                        <button type="button" className={state.chain === "any" ? "db-tab is-active" : "db-tab"} onClick={() => update({ chain: "any" })}>
                          {ANY_PILL}
                        </button>
                      </div>
                    )}
                    <button type="button" className="link-like nw-chain-clear" onClick={() => update({ to: null, chain: null })}>
                      {NO_CHAIN}
                    </button>
                  </div>
                )}
                {windowLine}
              </>
            ) : loading ? (
              <p className="nw-answer">Loading…</p>
            ) : null}
            <div className={firm ? "nw-controls nw-firm-dim" : "nw-controls"}>
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
            </div>
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

      <section className={(firm ? firmLoading && firmData : loading && circle) ? "nw-main is-updating" : "nw-main"}>
        {firm ? (
          firmError ? (
            <p className="error-text nw-error">
              {firmError}{" "}
              <button type="button" className="link-like" onClick={() => setFirmAttempt((n) => n + 1)}>
                Try again
              </button>
            </p>
          ) : !firmData ? (
            <p className="muted nw-loading">Loading…</p>
          ) : (
            <>
              <FirmFigure
                data={firmData}
                selected={state.select}
                highlight={typeof highlight === "number" ? highlight : null}
                onSelect={setSelect}
                onHover={setHovered}
              />
              <FirmKey data={firmData} />
            </>
          )
        ) : badId ? (
          <div className="nw-landing">
            <p className="eyebrow">Network</p>
            <h2>There is no entry at this address.</h2>
            <p className="muted">Search for a person entry to draw its circle.</p>
          </div>
        ) : personId === null ? (
          <div className="nw-landing nw-landing-wide">
            <p className="eyebrow">Network</p>
            <h2>{LANDING_HEADING}</h2>
            <p>{LANDING_LINE}</p>
            {dropped.length > 0 && (
              <p className="muted" title={dropped.join(", ")}>
                {DROPPED_LINE}
              </p>
            )}
            <NetworkLanding fixture={fixture || (Boolean(import.meta.env.DEV) && new URLSearchParams(address).get(FIXTURE_KEY) === "1")} />
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
              highlight={figureHighlight}
              years={state.years}
              yearCard={yearCard}
              labelAll={labelAll}
              onSelect={setSelect}
              onHover={setHovered}
            />
            {chainOn && chainData && chainRun && (
              <ChainStrip
                data={chainData}
                run={chainRun}
                forward={chainForward}
                drawn={drawnIds}
                selected={state.select?.kind === "person" ? state.select.id : null}
                onHover={hoverChain}
                onMark={clickChainMark}
                onHop={setDrawer}
              />
            )}
            <NetworkStrip
              data={circle}
              centreName={nodesById.has(circle.centre) ? label(nodesById.get(circle.centre)!) : `entry ${circle.centre}`}
              years={state.years}
              onYears={(years) => update({ years })}
              onHoverYear={hoverYear}
            />
            <NetworkKey data={circle} labelAll={labelAll} onLabelAll={setLabelAll} />
          </>
        )}
      </section>

      {shownData && (
        <aside className="nw-side" ref={side}>
          {firm && firmData ? (
            firmRow ? (
              <section className="nw-side-block">
                <header className="nw-side-head">
                  <div className="nw-side-head-row">
                    <p className="eyebrow">Person entry</p>
                    {clearButton}
                  </div>
                  <h3>
                    {firmLabel(firmRow)} <span className="nw-side-id">entry {firmRow.id}</span>
                  </h3>
                  <p className="nw-side-facts">
                    {firmRow.kind !== "person" && (
                      <span>
                        Recorded as {firmRow.kind === "institution" || firmRow.kind === "estate" ? "an" : "a"} {KIND_WORDS[firmRow.kind]}.{" "}
                      </span>
                    )}
                    {firmRow.woman && <span>Recorded as woman. </span>}
                    <span>{firmRowFacts(firmRow, firmData.acts)}</span>
                    {firmRow.acts_total !== firmRowActs.length && (
                      <span>
                        {" "}
                        On {firmRow.acts_total.toLocaleString("en-US")} acts in the corpus, {firmRowActs.length.toLocaleString("en-US")} here.
                      </span>
                    )}
                    {firmDuplicates.has(firmRow.name) && (
                      <span>
                        {" "}
                        Same name as{" "}
                        {firmData.rows
                          .filter((row) => row.name === firmRow.name && row.id !== firmRow.id)
                          .map((row) => `entry ${row.id}`)
                          .join(", ")}
                        , as entered.
                      </span>
                    )}
                  </p>
                  <div className="nw-side-actions">
                    <button type="button" className="pill-button" onClick={() => openEntry(firmRow.id)}>
                      Draw its circle
                    </button>
                    <Link className="nw-side-open" to={`/database/person/${firmRow.id}`} target="_blank" rel="noopener" title={OPEN_TITLE}>
                      Open in the Database
                    </Link>
                  </div>
                </header>
                <ul className="nw-acts">
                  {firmRowActs.map(({ act, cell }) => {
                    const appearance = cellAppearance(cell, firmRow.id, act.id);
                    return firmActRow(act, [roleCell(appearance), jewishWords(appearance), cashWords(appearance, act)].filter(Boolean).join(" · "), firmRow.id);
                  })}
                </ul>
              </section>
            ) : firmAct ? (
              <section className="nw-side-block">
                <header className="nw-side-head">
                  {backTo && backTo.act === firmAct.id && firmRowsById.has(backTo.entry) && (
                    <button type="button" className="link-like nw-side-back" onClick={() => setSelect({ kind: "person", id: backTo.entry })}>
                      ‹ Back to {firmLabel(firmRowsById.get(backTo.entry)!)}
                    </button>
                  )}
                  <div className="nw-side-head-row">
                    <p className="eyebrow">Act</p>
                    {clearButton}
                  </div>
                  <h3>
                    <ContractLink id={firmAct.id} onOpen={setDrawer} /> {firmAct.firm_name}
                  </h3>
                  <p className="nw-side-facts">
                    {dateWords(firmAct)} · {placeWords(firmAct)} · {shown(firmAct.activity)}
                    {firmAct.total !== null ? ` · ${firmAct.total.toLocaleString("en-US")}${firmAct.currency ? ` ${firmAct.currency}` : ""}` : ""}
                  </p>
                  {firmAct.later.length > 0 && <p className="nw-side-note muted">Later acts: {laterWords(firmAct)}.</p>}
                </header>
                <ul className="nw-signers">
                  {firmActSigners.map(({ row, cell }) => {
                    const appearance = cellAppearance(cell, row.id, firmAct.id);
                    return (
                      <li key={row.id} {...rowProps(row.id)}>
                        <button type="button" className="nw-cosigner" onClick={() => setSelect({ kind: "person", id: row.id })}>
                          <span className="nw-row-name">
                            {firmLabel(row)} <span className="nw-side-id">entry {row.id}</span>
                          </span>
                          <span className="nw-row-meta">{[roleCell(appearance), jewishWords(appearance), cashWords(appearance, firmAct)].filter(Boolean).join(" · ")}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : (
              <section className="nw-side-block">
                <div className="nw-side-head-row">
                  <p className="eyebrow">Registrations</p>
                </div>
                <ul className="nw-acts nw-firm-acts">
                  {firmData.acts.flatMap((act) => {
                    const change = firmChangeByFrom.get(act.id);
                    return [
                      firmActRow(act, null, null),
                      change ? (
                        <li key={`change-${act.id}`} className="nw-firm-change-row muted">
                          {changeLine(change, firmActsById)}
                        </li>
                      ) : null,
                    ];
                  })}
                </ul>
                {(firmData.empty?.length ?? firmData.counts.empty ?? 0) > 0 && (
                  <p className="muted nw-side-note nw-firm-empty">
                    {count(firmData.empty?.length ?? firmData.counts.empty ?? 0, "registration", "registrations")} under this name{" "}
                    {(firmData.empty?.length ?? firmData.counts.empty) === 1 ? "carries" : "carry"} no partner
                    {firmData.empty?.length ? (
                      <>
                        :{" "}
                        {firmData.empty.map((act, i) => (
                          <span key={act.id}>
                            {i > 0 ? ", " : ""}
                            <ContractLink id={act.id} onOpen={setDrawer} /> ({dateWords(act)})
                          </span>
                        ))}
                      </>
                    ) : null}
                    .
                  </p>
                )}
              </section>
            )
          ) : !circle ? null : !state.select ? (
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
                {firmNameOf(selectedAct) && (
                  <p className="nw-side-actions">
                    <button type="button" className="link-like nw-firm-follow" onClick={() => followFirm(selectedAct)}>
                      {FOLLOW_FIRM}
                    </button>
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
