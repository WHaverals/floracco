import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { NetworkAct, NetworkAppearance, NetworkCircle, NetworkKind } from "../types";
import { labelWidth, layoutCircle, pairKey, tieWidth, windowTies, type NetworkLayout, type PlacedLine, type PlacedNode } from "../utils/networkLayout";
import { pairSelect, sameSelect, type NetworkHubs, type NetworkSelect } from "../utils/networkSpec";
import { roleLabel } from "../utils/roles";

/* The circle of one entry as a figure (docs/network/step1_contract.md §3,
 * docs/network/step1_review_2026-10-29.md §2–§3): the ring layout of
 * utils/networkLayout.ts laid out in the stage's own pixels (a ResizeObserver
 * on .nw-stage gives the frame; the viewBox is the frame), with the house
 * marks (a dot sized by acts in view; a square for an institution, estate,
 * shareholder group or placeholder; a thin outer ring for an entry on more
 * than 20 acts; a hollow diamond for an entry that appears only through
 * heirs), the spokes and parent lines always, the other ties light at one
 * step and on hover or selection at two, labels at rest for the centre, the
 * hubs, the best-connected entries and every ring-1 entry when the ring has
 * room, and the house dark balloon on hover: who, how many acts in what
 * roles, how tied to the entry it hangs from, one qualifier. A mark or its
 * label selects the entry; a line selects the pair; the selected mark again,
 * or empty stage, clears. The page keeps the selection and fills the side
 * column.
 *
 * The window (step 2a, `years`): the mask of utils/networkLayout.ts
 * (windowTies) decides which lines are drawn and how wide; nothing moves. An
 * entry left with no tie inside is faded to 30% and keeps its place and its
 * label rule; the balloon's tie line adds the acts inside the window. With no
 * window the figure is the full picture. */

export const KIND_WORDS: Record<NetworkKind, string> = {
  person: "person entry",
  institution: "institution",
  estate: "estate",
  collective: "shareholder group",
  placeholder: "placeholder",
};

/** The balloon's line for a non-person entry: the kind is a rule over the
 * name fields, so it is said as the name reads, with no claim of a record. */
const KIND_LINES: Record<Exclude<NetworkKind, "person">, string> = {
  institution: "An institution, by the name as entered.",
  estate: "An estate, by the name as entered.",
  collective: "A shareholder group, by the name as entered.",
  placeholder: "A placeholder, by the name as entered.",
};

/** The role of a stake in running prose: "general partner", "external investor
 * (accomandante)", both, or "" with no live stake. */
export function roleWords(role: string | null | undefined): string {
  if (!role) return "";
  if (role === "both") return `${roleLabel("gp").toLowerCase()} and ${roleLabel("lp").toLowerCase()}`;
  return roleLabel(role).toLowerCase();
}

/** The qualifiers of an appearance, after the role: through heirs, through a proxy, on a joint stake. */
export function appearanceNotes(appearance: NetworkAppearance): string[] {
  const notes: string[] = [];
  if (appearance.heirs_of) notes.push("through heirs");
  if (appearance.proxy) notes.push("through a proxy");
  if (appearance.joint) notes.push("on a joint stake");
  return notes;
}

/** Labelled at rest: the centre, the hubs, and the best-connected entries (ten
 * or more co-signers in view), at most LABEL_MOST of them so a hub's circle
 * stays readable, and every ring-1 entry when ring 1's pitch allows; the rest
 * label on hover, on selection, or with Label all. Two rest labels closer than
 * LABEL_YIELD px along a ring: the lower degree yields. */
const LABEL_DEGREE = 10;
const LABEL_MOST = 20;
const LABEL_RING1_PITCH = 28;
const LABEL_YIELD = 14;
/** Labels sit this far outside the mark; in the 40° sectors at twelve and six
 * o'clock they alternate between the near and the far offset. */
const LABEL_NEAR = 6;
const LABEL_FAR = 34;
const STAGGER_SIN = Math.sin((20 * Math.PI) / 180);
/** The balloon stands this far from the mark, and this far inside the stage. */
const TIP_GAP = 10;
const TIP_EDGE = 6;
const TIP_ACTS_LISTED = 3;
const LINE_TIP_LISTED = 5;
const HUB_ACTS = 20;

type Hover = { kind: "node"; id: number } | { kind: "line"; key: string };
type TipSide = "right" | "left" | "above" | "below";

const num = (n: number) => n.toLocaleString("en-US");

function sortByDate(ids: number[], actsById: Map<number, NetworkAct>): number[] {
  const date = (id: number) => {
    const d = actsById.get(id)?.date ?? "";
    return !d || d.startsWith("0000") ? "9999-99-99" : d;
  };
  return [...ids].sort((p, q) => {
    const dp = date(p);
    const dq = date(q);
    return dp < dq ? -1 : dp > dq ? 1 : p - q;
  });
}

type LabelPlace = { x: number; y: number; anchor: "start" | "middle" | "end" };

/** Where a label stands: outside the mark along its radius, horizontal, anchored
 * away from the circle (centred in the twelve and six o'clock sectors), the
 * centre's under its mark. */
function labelPlace(placed: PlacedNode, offset: number): LabelPlace {
  const { x, y, r, angle, ring } = placed;
  if (ring === 0) return { x, y: y + r + 15, anchor: "middle" };
  const s = Math.sin(angle);
  const c = Math.cos(angle);
  const anchor = Math.abs(s) < STAGGER_SIN ? "middle" : s > 0 ? "start" : "end";
  return { x: x + s * (r + offset), y: y - c * (r + offset) + (4 - 6 * c), anchor };
}

/** The estimated box of a label, [x0, y0, x1, y1], for the yield rule. */
function labelBox(placed: PlacedNode, place: LabelPlace): [number, number, number, number] {
  const width = labelWidth(placed.label) * (placed.ring === 0 ? 13 / 11.5 : 1);
  const x0 = place.anchor === "start" ? place.x : place.anchor === "end" ? place.x - width : place.x - width / 2;
  return [x0, place.y - 9, x0 + width, place.y + 3];
}

function overlaps(a: [number, number, number, number], b: [number, number, number, number]): boolean {
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

/** "2614 (1636)", or "4037 (no registration date)". */
function actRef(id: number, actsById: Map<number, NetworkAct>): string {
  const year = actsById.get(id)?.year;
  return `${id} (${year ? year : "no registration date"})`;
}

/** "1636–1653", "1652", or "" when no act in the list is dated. */
function yearSpan(ids: number[], actsById: Map<number, NetworkAct>): string {
  const years = ids.map((id) => actsById.get(id)?.year).filter((y): y is number => typeof y === "number" && y > 0);
  if (!years.length) return "";
  const first = Math.min(...years);
  const last = Math.max(...years);
  return first === last ? String(first) : `${first}–${last}`;
}

/** "6 acts together, 1636–1653" (the span left out when no act is dated). */
function actsTogether(ids: number[], actsById: Map<number, NetworkAct>): string {
  const span = yearSpan(ids, actsById);
  return `${num(ids.length)} acts together${span ? `, ${span}` : ""}`;
}

/** "1640–1649", or "1687" for a window of one year. */
function windowWords(years: [number, number]): string {
  return years[0] === years[1] ? String(years[0]) : `${years[0]}–${years[1]}`;
}

/** The balloon's second line: acts in view, their span, the roles by count. */
function actsLine(placed: PlacedNode, appearances: NetworkAppearance[]): string {
  const node = placed.node;
  const total = node.acts_in_view;
  let gp = 0;
  let lp = 0;
  let none = 0;
  for (const appearance of appearances) {
    if (appearance.role === "gp" || appearance.role === "both") gp += 1;
    if (appearance.role === "lp" || appearance.role === "both") lp += 1;
    if (!appearance.role) none += 1;
  }
  const pairs: [string, number][] = [
    [roleLabel("lp").toLowerCase(), lp],
    [roleLabel("gp").toLowerCase(), gp],
    ["no role recorded", none],
  ];
  const counted = pairs.filter((pair) => pair[1] > 0).sort((p, q) => q[1] - p[1]);
  const roles =
    counted.length === 0
      ? "no role recorded"
      : total === 1
        ? counted[0][0]
        : counted.length === 1 && counted[0][1] >= total
          ? `${counted[0][0]} on all ${num(total)}`
          : counted.map(([word, n]) => `${word} on ${num(n)}`).join(", ");
  const dated = total - node.undated_acts;
  const span =
    node.first_year && node.last_year && dated > 0
      ? node.first_year === node.last_year
        ? String(node.first_year)
        : `${node.first_year}–${node.last_year}`
      : "";
  if (total === 1) return `One act in view, ${span || "with no registration date"}; ${roles}.`;
  const undated =
    node.undated_acts === 0 ? "" : span ? `; ${num(node.undated_acts)} with no registration date` : "all with no registration date";
  return `${num(total)} acts in view, ${span}${undated}; ${roles}.`;
}

export default function NetworkFigure({
  data,
  hubs,
  selected,
  highlight,
  years = null,
  labelAll,
  onSelect,
  onHover,
}: {
  data: NetworkCircle;
  /** the hub state in force; nothing in the balloon reads it now, the key may */
  hubs: NetworkHubs;
  selected: NetworkSelect | null;
  /** an entry lit from a side-column row, as hover lights it; or a set of
   * entries lit together from the strip (a year's signers) */
  highlight: number | ReadonlySet<number> | null;
  /** the window on the strip; null for all years */
  years?: [number, number] | null;
  labelAll: boolean;
  /** null clears: the selected mark clicked again, or empty stage */
  onSelect: (select: NetworkSelect | null) => void;
  /** the entry under the pointer, for the side column */
  onHover: (id: number | null) => void;
}) {
  void hubs;
  const stageRef = useRef<HTMLDivElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });
  const [hover, setHover] = useState<Hover | null>(null);
  const [nudge, setNudge] = useState({ key: "", dx: 0, dy: 0 });

  // The stage's own box, measured; the layout fits it, one unit one pixel.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = (width: number, height: number) => {
      const w = Math.round(width);
      const h = Math.round(height);
      setFrame((old) => (old.width === w && old.height === h ? old : { width: w, height: h }));
    };
    // The first frame at once (the observer reports only once the page paints),
    // then every change of the stage's box.
    measure(el.clientWidth, el.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) measure(box.width, box.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const layout: NetworkLayout | null = useMemo(
    () => (frame.width > 0 && frame.height > 0 ? layoutCircle(data, frame) : null),
    [data, frame],
  );
  // The balloon is hidden during a relayout.
  useEffect(() => setHover(null), [layout]);

  const actsById = useMemo(() => new Map(data.acts.map((act) => [act.id, act])), [data]);
  const appearancesByPerson = useMemo(() => {
    const map = new Map<number, NetworkAppearance[]>();
    for (const appearance of data.appearances) {
      const list = map.get(appearance.person) ?? [];
      list.push(appearance);
      map.set(appearance.person, list);
    }
    return map;
  }, [data]);
  // The window's mask: the lines drawn are those with an act inside, their
  // width by the acts inside; the acts listed stay the full shared list.
  const mask = useMemo(() => windowTies(data, years), [data, years]);
  const insideByKey = useMemo(() => new Map(mask.ties.map((tie) => [pairKey(tie.a, tie.b), tie.acts])), [mask]);
  const lines = useMemo<PlacedLine[]>(() => {
    if (!layout) return [];
    if (!years) return layout.lines;
    return layout.lines.flatMap((line) => {
      const inside = insideByKey.get(line.key);
      return inside ? [{ ...line, width: tieWidth(inside.length) }] : [];
    });
  }, [layout, years, insideByKey]);
  const neighbours = useMemo(() => {
    const map = new Map<number, Set<number>>();
    for (const line of lines) {
      (map.get(line.a) ?? map.set(line.a, new Set()).get(line.a)!).add(line.b);
      (map.get(line.b) ?? map.set(line.b, new Set()).get(line.b)!).add(line.a);
    }
    return map;
  }, [lines]);
  const linesByKey = useMemo(() => new Map(lines.map((line) => [line.key, line])), [lines]);
  const signersOfSelectedAct = useMemo(() => {
    if (selected?.kind !== "contract") return new Set<number>();
    return new Set(data.appearances.filter((a) => a.act === selected.id).map((a) => a.person));
  }, [data, selected]);

  const hoveredNode = hover?.kind === "node" ? hover.id : null;
  const hoveredLine = hover?.kind === "line" ? (linesByKey.get(hover.key) ?? null) : null;
  /** the entry lit: under the pointer on the figure, or under it on a side-column row */
  const lit = hoveredNode ?? (typeof highlight === "number" ? highlight : null);
  /** the entries lit together from the strip: a year's signers */
  const litSet = lit === null && highlight !== null && typeof highlight !== "number" ? highlight : null;
  const selectedPerson = selected?.kind === "person" ? selected.id : null;
  const selectedAct = selected?.kind === "contract" ? selected.id : null;
  const selectedPair = selected?.kind === "pair" ? selected : null;
  const selectedPairKey = selectedPair ? pairKey(selectedPair.a, selectedPair.b) : null;
  const oneStep = data.steps === 1;

  const touches = (line: PlacedLine, id: number | null) => id !== null && (line.a === id || line.b === id);
  /** lit by the pointer or a row (one end), or by the strip (both ends) */
  const litLine = (line: PlacedLine) => touches(line, lit) || (litSet !== null && litSet.has(line.a) && litSet.has(line.b));
  /** drawn for a selection: these chords carry a hit line at two steps */
  const chosen = (line: PlacedLine) =>
    touches(line, selectedPerson) || (selectedAct !== null && line.acts.includes(selectedAct)) || line.key === selectedPairKey;
  const drawn = lines.filter((line) => line.kind !== "tie" || oneStep || litLine(line) || chosen(line));

  // Labels at rest, with the stagger at twelve and six o'clock and the yield rule.
  const rest = useMemo(() => {
    const shown = new Set<number>();
    const offsets = new Map<number, number>();
    /** rest labels given up: for the stage's edge, and for another label */
    const yielded = { edge: 0, overlap: 0 };
    if (!layout) return { shown, offsets, yielded };
    const best = layout.nodes
      .filter((placed) => placed.ring !== 0 && !placed.node.hub && placed.degree >= LABEL_DEGREE)
      .sort((p, q) => q.degree - p.degree || p.node.id - q.node.id)
      .slice(0, LABEL_MOST);
    const candidates = new Set(best.map((placed) => placed.node.id));
    const ring1 = layout.rings[1];
    const allRing1 = Boolean(ring1 && ring1.pitch >= LABEL_RING1_PITCH);
    for (const placed of layout.nodes) {
      if (placed.ring === 0 || placed.node.hub || (allRing1 && placed.ring === 1)) candidates.add(placed.node.id);
    }
    // The stagger: in the 40° sectors at twelve and six o'clock, rest labels
    // alternate between the near and the far offset along the ring.
    for (let ring = 1; ring < layout.rings.length; ring += 1) {
      for (const top of [true, false]) {
        const sector = layout.nodes
          .filter((p) => p.ring === ring && candidates.has(p.node.id) && Math.abs(Math.sin(p.angle)) < STAGGER_SIN && Math.cos(p.angle) > 0 === top)
          .sort((p, q) => Math.sin(p.angle) - Math.sin(q.angle));
        sector.forEach((p, i) => offsets.set(p.node.id, i % 2 === 0 ? LABEL_NEAR : LABEL_FAR));
      }
    }
    // The yield rule: a rest label whose box would cross the stage's edge
    // yields (the name stays on hover, on selection, with Label all and in the
    // side column); of two rest labels whose boxes would overprint (two
    // neighbours closer than LABEL_YIELD px along a ring always do), the lower
    // degree yields; the centre and the hubs stand first.
    const rank = (p: PlacedNode) => (p.ring === 0 ? 2 : p.node.hub ? 1 : 0);
    const ordered = layout.nodes
      .filter((p) => candidates.has(p.node.id))
      .sort((p, q) => rank(q) - rank(p) || q.degree - p.degree || p.node.id - q.node.id);
    const kept: [number, number, number, number][] = [];
    for (const placed of ordered) {
      const box = labelBox(placed, labelPlace(placed, offsets.get(placed.node.id) ?? LABEL_NEAR));
      const padded: [number, number, number, number] = [box[0] - LABEL_YIELD / 2, box[1], box[2] + LABEL_YIELD / 2, box[3]];
      if (placed.ring !== 0 && (box[0] < 0 || box[1] < 0 || box[2] > layout.width || box[3] > layout.height)) {
        yielded.edge += 1;
        continue;
      }
      if (placed.ring !== 0 && kept.some((other) => overlaps(padded, other))) {
        yielded.overlap += 1;
        continue;
      }
      kept.push(padded);
      shown.add(placed.node.id);
    }
    return { shown, offsets, yielded };
  }, [layout]);

  const labelled = (placed: PlacedNode): boolean => {
    const id = placed.node.id;
    if (labelAll || placed.ring === 0 || rest.shown.has(id)) return true;
    if (id === selectedPerson || signersOfSelectedAct.has(id)) return true;
    if (selectedPair && (id === selectedPair.a || id === selectedPair.b)) return true;
    if (lit !== null && (id === lit || neighbours.get(lit)?.has(id))) return true;
    if (litSet !== null && litSet.has(id)) return true;
    if (hoveredLine && (hoveredLine.a === id || hoveredLine.b === id)) return true;
    return false;
  };

  // The balloon stands outward from the mark: beside it in the left and right
  // halves, above or below it in the twelve and six o'clock sectors, and is
  // nudged inside the stage when it would cross an edge.
  const tipAnchor = (placed: PlacedNode): { side: TipSide; x: number; y: number } => {
    const { x, y, r, angle, ring } = placed;
    const s = Math.sin(angle);
    const c = Math.cos(angle);
    if (ring !== 0 && Math.abs(s) < STAGGER_SIN) {
      return c > 0 ? { side: "above", x, y: y - r - TIP_GAP } : { side: "below", x, y: y + r + TIP_GAP };
    }
    return s < 0 ? { side: "left", x: x - r - TIP_GAP, y } : { side: "right", x: x + r + TIP_GAP, y };
  };

  /** "; 4 in 1640–1649" for the balloon's tie line while a window is set. */
  const insideWords = (acts: number[]): string => {
    if (!years) return "";
    const inside = acts.filter((id) => mask.inside.has(id)).length;
    return `; ${num(inside)} in ${windowWords(years)}`;
  };

  const nodeTip = (placed: PlacedNode): ReactNode => {
    const node = placed.node;
    const appearances = appearancesByPerson.get(node.id) ?? [];
    const anchor = placed.anchor !== null && layout ? layout.byId.get(placed.anchor) : undefined;
    const shared = anchor ? sortByDate(layout?.sharedActs.get(pairKey(node.id, anchor.node.id)) ?? [], actsById) : [];
    let tie: string | null = null;
    if (anchor && shared.length) {
      const lead = placed.ring === 1 ? `With ${anchor.label}` : `In this circle through ${anchor.label}`;
      tie =
        shared.length <= TIP_ACTS_LISTED
          ? `${lead}: ${shared.length === 1 ? "act" : "acts"} ${shared.map((id) => actRef(id, actsById)).join(", ")}`
          : `${lead}: ${actsTogether(shared, actsById)}`;
      tie += `${insideWords(shared)}.`;
    }
    return (
      <>
        <strong>
          {placed.label} <span className="nw-tip-id">entry {node.id}</span>
        </strong>
        <span>{actsLine(placed, appearances)}</span>
        {tie && <span>{tie}</span>}
        {node.woman && <span>Recorded as woman.</span>}
        {node.kind !== "person" && <span>{KIND_LINES[node.kind]}</span>}
        {node.acts_total > HUB_ACTS && <span>On {num(node.acts_total)} acts in the corpus.</span>}
        {placed.heirsOnly && <span>Appears here only through heirs.</span>}
      </>
    );
  };

  const lineTip = (line: PlacedLine): ReactNode => {
    const a = layout?.byId.get(line.a);
    const b = layout?.byId.get(line.b);
    const acts = sortByDate(line.acts, actsById);
    let text: string;
    if (acts.length === 1) {
      const firm = actsById.get(acts[0])?.firm_name;
      text = `One act together: ${actRef(acts[0], actsById)}${firm ? `, in the firm ${firm}` : ""}`;
    } else if (acts.length <= LINE_TIP_LISTED) {
      text = `${num(acts.length)} acts together: ${acts.map((id) => actRef(id, actsById)).join(", ")}`;
    } else if (acts.length === LINE_TIP_LISTED + 1) {
      text = `${num(acts.length)} acts together: ${acts
        .slice(0, LINE_TIP_LISTED)
        .map((id) => actRef(id, actsById))
        .join(", ")}, and one more`;
    } else {
      text = actsTogether(acts, actsById);
    }
    text += `${insideWords(acts)}.`;
    return (
      <>
        <strong>
          {a?.label} <span className="nw-tip-id">entry {line.a}</span> and {b?.label} <span className="nw-tip-id">entry {line.b}</span>
        </strong>
        <span>{text}</span>
      </>
    );
  };

  const tip = (() => {
    if (!hover || !layout) return null;
    if (hover.kind === "node") {
      const placed = layout.byId.get(hover.id);
      if (!placed) return null;
      return { key: `n${hover.id}`, ...tipAnchor(placed), body: nodeTip(placed) };
    }
    const line = linesByKey.get(hover.key);
    if (!line) return null;
    const a = layout.byId.get(line.a);
    const b = layout.byId.get(line.b);
    if (!a || !b) return null;
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    const side: TipSide = my < layout.cy ? "below" : "above";
    return { key: `l${line.key}`, side, x: mx, y: side === "below" ? my + TIP_GAP : my - TIP_GAP, body: lineTip(line) };
  })();

  // The edge-hug fallback: once drawn, a balloon crossing the stage's edge is moved inside.
  useLayoutEffect(() => {
    const el = tipRef.current;
    const stage = stageRef.current;
    if (!el || !stage || !tip) return;
    const box = el.getBoundingClientRect();
    const host = stage.getBoundingClientRect();
    const current = nudge.key === tip.key ? nudge : { key: tip.key, dx: 0, dy: 0 };
    let dx = current.dx;
    let dy = current.dy;
    if (box.left < host.left + TIP_EDGE) dx += host.left + TIP_EDGE - box.left;
    else if (box.right > host.right - TIP_EDGE) dx -= box.right - (host.right - TIP_EDGE);
    if (box.top < host.top + TIP_EDGE) dy += host.top + TIP_EDGE - box.top;
    else if (box.bottom > host.bottom - TIP_EDGE) dy -= box.bottom - (host.bottom - TIP_EDGE);
    if (nudge.key !== tip.key || Math.abs(dx - nudge.dx) > 0.5 || Math.abs(dy - nudge.dy) > 0.5) setNudge({ key: tip.key, dx, dy });
  });

  const enterNode = (id: number) => {
    setHover({ kind: "node", id });
    onHover(id);
  };
  const leaveNode = () => {
    setHover(null);
    onHover(null);
  };
  const clickNode = (id: number) => {
    const next: NetworkSelect = { kind: "person", id };
    onSelect(sameSelect(selected, next) ? null : next);
  };
  const clickLine = (line: PlacedLine) => {
    const next = pairSelect(line.a, line.b);
    onSelect(sameSelect(selected, next) ? null : next);
  };

  const mark = (placed: PlacedNode): ReactNode => {
    const { x, y, r, node } = placed;
    if (placed.heirsOnly) {
      const d = r * 1.35;
      return <polygon className="nw-mark is-heirs" points={`${x},${y - d} ${x + d},${y} ${x},${y + d} ${x - d},${y}`} />;
    }
    if (node.kind !== "person") return <rect className="nw-mark is-other" x={x - r} y={y - r} width={2 * r} height={2 * r} />;
    return <circle className="nw-mark" cx={x} cy={y} r={r} />;
  };

  const label = (placed: PlacedNode): ReactNode => {
    const { ring, node } = placed;
    const id = node.id;
    const at = labelPlace(placed, rest.offsets.get(id) ?? LABEL_NEAR);
    return (
      <text
        key={id}
        className={[ring === 0 ? "nw-label is-centre" : node.hub ? "nw-label is-hub" : "nw-label", mask.faded.has(id) ? "is-faded" : ""].filter(Boolean).join(" ")}
        data-id={id}
        x={at.x}
        y={at.y}
        textAnchor={at.anchor}
        onMouseEnter={() => enterNode(id)}
        onMouseLeave={leaveNode}
        onClick={() => clickNode(id)}
      >
        {placed.label}
      </text>
    );
  };

  const litNeighbours = lit !== null ? neighbours.get(lit) : undefined;
  const isSelectedNode = (id: number) => id === selectedPerson || (selectedPair !== null && (id === selectedPair.a || id === selectedPair.b));

  return (
    <div ref={stageRef} className="nw-stage" onMouseLeave={leaveNode}>
      {layout && (
        <svg
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width={layout.width}
          height={layout.height}
          role="img"
          aria-label={data.sentence}
          data-labels-rest={rest.shown.size}
          data-labels-edge={rest.yielded.edge}
          data-labels-overlap={rest.yielded.overlap}
          data-faded={mask.faded.size}
          data-lines={lines.length}
        >
          <rect className="nw-stage-bg" x={0} y={0} width={layout.width} height={layout.height} onClick={() => onSelect(null)} />
          <g>
            {drawn.map((line) => {
              const a = layout.byId.get(line.a)!;
              const b = layout.byId.get(line.b)!;
              const hovered = hover?.kind === "line" ? hover.key === line.key : litLine(line);
              const selectedLine = chosen(line);
              const cls = [
                "nw-line",
                `is-${line.kind}`,
                hovered ? "is-active" : "",
                selectedLine ? "is-selected" : "",
                (hover || highlight !== null) && !hovered && !selectedLine ? "is-dim" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const hit = line.kind !== "tie" || oneStep || selectedLine;
              return (
                <g key={line.key}>
                  <line className={cls} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={line.width} />
                  {hit && (
                    <line
                      className={`nw-hit-line is-${line.kind}`}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      onMouseEnter={() => setHover({ kind: "line", key: line.key })}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => clickLine(line)}
                    />
                  )}
                </g>
              );
            })}
          </g>
          <g>
            {layout.nodes.map((placed) => {
              const id = placed.node.id;
              const near =
                lit !== null
                  ? id === lit || Boolean(litNeighbours?.has(id))
                  : litSet !== null
                    ? litSet.has(id)
                    : hoveredLine
                      ? hoveredLine.a === id || hoveredLine.b === id
                      : true;
              const cls = [
                "nw-node",
                placed.ring === 0 ? "is-centre" : "",
                isSelectedNode(id) ? "is-selected" : "",
                signersOfSelectedAct.has(id) ? "is-signer" : "",
                id === lit || (litSet !== null && litSet.has(id)) ? "is-hover" : "",
                (hover || highlight !== null) && !near && !isSelectedNode(id) ? "is-dim" : "",
                mask.faded.has(id) ? "is-faded" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <g
                  key={id}
                  className={cls}
                  data-id={id}
                  onMouseEnter={() => enterNode(id)}
                  onMouseLeave={leaveNode}
                  onClick={() => clickNode(id)}
                >
                  {placed.node.hub && <circle className="nw-hub-ring" cx={placed.x} cy={placed.y} r={placed.r + 3.5} />}
                  {mark(placed)}
                  <circle className="nw-hit" cx={placed.x} cy={placed.y} r={Math.max(12, placed.r + 4)} />
                </g>
              );
            })}
          </g>
          <g>{layout.nodes.filter(labelled).map(label)}</g>
        </svg>
      )}
      {tip && (
        <div
          ref={tipRef}
          className={`nw-tip is-${tip.side}`}
          style={{ left: tip.x + (nudge.key === tip.key ? nudge.dx : 0), top: tip.y + (nudge.key === tip.key ? nudge.dy : 0) }}
          role="tooltip"
        >
          {tip.body}
        </div>
      )}
    </div>
  );
}

/** The marks, for a key under the figure, with "Label all" as its last item. */
export function NetworkKey({
  data,
  labelAll,
  onLabelAll,
}: {
  data: NetworkCircle;
  labelAll: boolean;
  onLabelAll: (labelAll: boolean) => void;
}) {
  const others = data.nodes.some((node) => node.kind !== "person");
  const byPerson = new Map<number, boolean>();
  for (const appearance of data.appearances) byPerson.set(appearance.person, (byPerson.get(appearance.person) ?? true) && appearance.heirs_of);
  const heirs = [...byPerson.values()].some(Boolean);
  return (
    <p className="nw-key">
      <span>
        <i className="nw-key-dot" /> a person entry, sized by its acts in view
      </span>
      {data.counts.hubs > 0 && (
        <span>
          <i className="nw-key-ring" /> on more than 20 acts
        </span>
      )}
      {others && (
        <span>
          <i className="nw-key-square" /> an institution, estate, shareholder group or placeholder
        </span>
      )}
      {heirs && (
        <span>
          <i className="nw-key-diamond" /> appears here only through heirs
        </span>
      )}
      <span>
        <i className="nw-key-line" /> an act signed together; wider with more acts
      </span>
      <label className="nw-key-check">
        <input type="checkbox" checked={labelAll} onChange={(event) => onLabelAll(event.target.checked)} />
        Label all
      </label>
    </p>
  );
}
