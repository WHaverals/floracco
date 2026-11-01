/* The ring layout of one entry's circle (docs/network/step1_contract.md §3,
 * scope §4.3 "Rings", docs/network/step1_review_2026-10-29.md §2): the angles,
 * ring order and sectors are a pure function of the server's response, with
 * no simulation and no randomness; the radii fit the measured frame, so the
 * same response draws the same angles on every machine and the circle fills
 * whatever stage it is given (one unit of the viewBox is one pixel).
 *
 * The centre sits in the middle. Its co-signers stand on ring 1, ordered
 * clockwise from the top by the earliest act each shares with the centre,
 * then by acts shared (most first), then by entry id; the first stands at
 * twelve o'clock. Each ring-1 entry owns an angular sector proportional to
 * 1 + the entries hanging from it, so a busy co-signer gets the room its
 * circle needs (with no second ring the sectors are equal and ring 1 is
 * evenly spaced). A ring-2 entry stands on ring 2 inside its parent's sector,
 * the children ordered the same way relative to the parent; the co-signers of
 * an entry widened by hand stand on the next ring inside that entry's own
 * sector.
 *
 * Radii (review §2). R_out = min(w/2 − 100, h/2 − 36), 100 px being a
 * 17-character name beside a mark at three or nine o'clock; a longer name
 * there gives up its rest label rather than the circle its size. Rings by the
 * outermost ring in use: one ring at R_out; two rings, ring 1 at 50–62 % of
 * R_out as its label room asks; three rings at 34 / 66 / 100 %. A near-empty
 * ring is never wider than its entries need at a generous pitch, and never
 * under 120 px, so one co-signer is not a long spoke across an empty stage.
 * Marks r = 4 + 12·t (t the square-root scale of acts in view), capped by the
 * smallest pitch on the ring; lines 1 / 1.6 / 2.4 / 3.5 px by acts shared. */

import type { NetworkAct, NetworkCircle, NetworkNode, NetworkTie } from "../types";
import { YEAR_MAX, YEAR_MIN, duplicateNames, nodeLabel } from "./networkSpec";

export type Frame = { width: number; height: number };

/** The room kept beside the circle at three and nine o'clock, in px. */
const MARGIN_X = 100;
const MARGIN_Y = 36;
/** Inter at 11.5 px: about 5.9 px per character. */
const LABEL_FONT = 11.5;
const LABEL_CHAR = 0.513 * LABEL_FONT;
/** A near-empty ring is no wider than its entries need at this pitch (review
 * §2 gives 36 px, which would cap a 37-entry ring at 212 px against the same
 * section's expected 234 and 434 px radii; 74 px keeps those figures). */
const SPARSE_PITCH = 74;
const RING_MIN = 120;
const RING_GAP_MIN = 70;
/** Ring 1 under ring 2 asks for this much room per entry, up to 62 % of R_out. */
const RING1_PITCH = 28;
export const NODE_R_MIN = 4;
export const NODE_R_MAX = 16;
const NODE_R_SPAN = 12;
const NODE_R_PITCH = 0.45;

export type PlacedNode = {
  node: NetworkNode;
  x: number;
  y: number;
  /** clockwise from the top, in radians */
  angle: number;
  ring: number;
  /** mark radius in px, by the square root of acts in view, capped by the ring's pitch */
  r: number;
  /** co-signers in view */
  degree: number;
  /** every appearance in view is through heirs */
  heirsOnly: boolean;
  /** the entry this one hangs from (the centre on ring 1); null on the centre */
  anchor: number | null;
  /** the name as written on the figure (nodeLabel: the namesake rule) */
  label: string;
};

export type PlacedLine = {
  key: string;
  a: number;
  b: number;
  acts: number[];
  /** spoke: centre to ring 1; parent: an entry to the entry it hangs from; tie: any other */
  kind: "spoke" | "parent" | "tie";
  width: number;
};

export type RingInfo = {
  radius: number;
  count: number;
  /** the mean pitch along the ring, px (Infinity with one entry or none) */
  pitch: number;
  /** the smallest pitch between two neighbours on the ring, px */
  minPitch: number;
};

export type NetworkLayout = {
  width: number;
  height: number;
  cx: number;
  cy: number;
  nodes: PlacedNode[];
  byId: Map<number, PlacedNode>;
  lines: PlacedLine[];
  /** the radius of each ring, by ring number */
  radii: number[];
  /** each ring's radius, count and pitch, by ring number (ring 0 is the centre) */
  rings: RingInfo[];
  /** acts shared by a drawn pair, by pairKey */
  sharedActs: Map<string, number[]>;
};

export function pairKey(a: number, b: number): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

/** Line width by acts shared: 1, 2, 3–5, 6 or more. */
export function tieWidth(acts: number): number {
  if (acts >= 6) return 3.5;
  if (acts >= 3) return 2.4;
  if (acts === 2) return 1.6;
  return 1;
}

/** A sortable date: undated acts ("0000-00-00") come after every dated one. */
function sortableDate(act: NetworkAct | undefined): string {
  const date = act?.date ?? "";
  return !date || date.startsWith("0000") ? "9999-99-99" : date;
}

type Arrangement = {
  anchorOf: Map<number, number>;
  angle: Map<number, number>;
  ringOf: Map<number, number>;
  degree: Map<number, number>;
  sharedActs: Map<string, number[]>;
  /** the outermost ring in use */
  rings: number;
};

/** The angles, rings and anchors: the part of the layout that never depends on the frame. */
function arrange(data: NetworkCircle): Arrangement {
  const actsById = new Map(data.acts.map((act) => [act.id, act]));
  const nodesById = new Map(data.nodes.map((node) => [node.id, node]));
  const sharedActs = new Map<string, number[]>();
  const degree = new Map<number, number>();
  for (const tie of data.ties) {
    if (!nodesById.has(tie.a) || !nodesById.has(tie.b)) continue;
    sharedActs.set(pairKey(tie.a, tie.b), tie.acts);
    degree.set(tie.a, (degree.get(tie.a) ?? 0) + 1);
    degree.set(tie.b, (degree.get(tie.b) ?? 0) + 1);
  }

  // The order on a ring, relative to the entry it hangs from.
  const key = (id: number, anchor: number): [string, number, number] => {
    const acts = sharedActs.get(pairKey(id, anchor)) ?? [];
    const earliest = acts.length ? acts.map((a) => sortableDate(actsById.get(a))).sort()[0] : "9999-99-99";
    return [earliest, -acts.length, id];
  };
  const compare = (anchor: number) => (p: number, q: number) => {
    const [dp, ap, ip] = key(p, anchor);
    const [dq, aq, iq] = key(q, anchor);
    return dp < dq ? -1 : dp > dq ? 1 : ap - aq || ip - iq;
  };

  // The tree: the centre, its co-signers, and under each of those the entries
  // hanging from it. An entry whose parent is not drawn hangs from the centre.
  const children = new Map<number, number[]>();
  const anchorOf = new Map<number, number>();
  for (const node of data.nodes) {
    if (node.id === data.centre) continue;
    const parent = node.parent !== null && nodesById.has(node.parent) && node.parent !== node.id ? node.parent : data.centre;
    anchorOf.set(node.id, parent);
    const list = children.get(parent) ?? [];
    list.push(node.id);
    children.set(parent, list);
  }
  for (const [parent, list] of children) list.sort(compare(parent));

  const weight = new Map<number, number>();
  const weigh = (id: number): number => {
    const w = 1 + (children.get(id) ?? []).reduce((sum, child) => sum + weigh(child), 0);
    weight.set(id, w);
    return w;
  };
  weigh(data.centre);

  // Angles: sectors proportional to weight, children centred in their own
  // sub-sector; the first ring-1 entry stands exactly at the top.
  const angle = new Map<number, number>([[data.centre, 0]]);
  const ringOf = new Map<number, number>([[data.centre, 0]]);
  const place = (parent: number, start: number, span: number, ring: number) => {
    const list = children.get(parent) ?? [];
    const total = list.reduce((sum, id) => sum + (weight.get(id) ?? 1), 0);
    let at = start;
    for (const id of list) {
      const width = (span * (weight.get(id) ?? 1)) / total;
      angle.set(id, at + width / 2);
      ringOf.set(id, ring);
      place(id, at, width, ring + 1);
      at += width;
    }
  };
  const first = children.get(data.centre)?.[0];
  const firstWidth = first === undefined ? 0 : (2 * Math.PI * (weight.get(first) ?? 1)) / ((weight.get(data.centre) ?? 1) - 1);
  place(data.centre, -firstWidth / 2, 2 * Math.PI, 1);

  return { anchorOf, angle, ringOf, degree, sharedActs, rings: Math.max(0, ...ringOf.values()) };
}

/** The drawn entries in ring order: the centre, ring 1 clockwise from the top,
 * ring 2 by parent sector (clockwise likewise), then the third ring. A pure
 * function of the response, for the side column's list and Previous / Next. */
export function ringOrder(data: NetworkCircle): number[] {
  const { angle, ringOf, rings } = arrange(data);
  const order: number[] = [data.centre];
  for (let ring = 1; ring <= rings; ring += 1) {
    const ids = data.nodes
      .map((node) => node.id)
      .filter((id) => ringOf.get(id) === ring)
      .sort((p, q) => (angle.get(p) ?? 0) - (angle.get(q) ?? 0) || p - q);
    order.push(...ids);
  }
  return order;
}

/** The smallest angular gap between neighbours on a ring, in radians (2π with one entry). */
function minGap(angles: number[]): number {
  if (angles.length < 2) return 2 * Math.PI;
  const sorted = [...angles].sort((a, b) => a - b);
  let gap = sorted[0] + 2 * Math.PI - sorted[sorted.length - 1];
  for (let i = 1; i < sorted.length; i += 1) gap = Math.min(gap, sorted[i] - sorted[i - 1]);
  return gap;
}

/** The estimated width of a label at 11.5 px Inter. */
export function labelWidth(label: string): number {
  return label.length * LABEL_CHAR;
}

/** A near-empty ring's cap: what its entries need at a generous pitch, never under RING_MIN. */
function sparseCap(count: number): number {
  return Math.max(RING_MIN, (SPARSE_PITCH * count) / (2 * Math.PI));
}

export function layoutCircle(data: NetworkCircle, frame: Frame): NetworkLayout {
  const { anchorOf, angle, ringOf, degree, sharedActs, rings } = arrange(data);
  const duplicates = duplicateNames(data.nodes);
  const labels = new Map(data.nodes.map((node) => [node.id, nodeLabel(node, duplicates)]));
  const width = Math.max(0, frame.width);
  const height = Math.max(0, frame.height);
  const cx = width / 2;
  const cy = height / 2;

  const idsOnRing: number[][] = [];
  for (let ring = 0; ring <= rings; ring += 1) idsOnRing[ring] = [];
  for (const node of data.nodes) idsOnRing[ringOf.get(node.id) ?? 0].push(node.id);
  const anglesOn = (ring: number) => idsOnRing[ring].map((id) => angle.get(id) ?? 0);

  // R_out: the frame less the room a label needs beside a mark at three or
  // nine o'clock. A longer name that would cross the stage's edge yields its
  // rest label instead (NetworkFigure), so the circle never shrinks for it.
  const rOut = Math.max(0, Math.min(width / 2 - MARGIN_X, height / 2 - MARGIN_Y));

  // Rings by the outermost ring in use (the near-empty cap applies to that
  // ring alone; the inner rings follow their fractions), the order of the
  // rings kept.
  const radii: number[] = [0];
  if (rings >= 1) {
    const outerCount = idsOnRing[rings].length;
    const rK = Math.min(rOut, sparseCap(outerCount));
    if (rings === 1) {
      radii[1] = rK;
    } else if (rings === 2) {
      const n1 = idsOnRing[1].length;
      const fraction = Math.min(0.62, Math.max(0.5, (RING1_PITCH * n1) / (2 * Math.PI) / Math.max(1, rK)));
      radii[1] = fraction * rK;
      radii[2] = rK;
    } else {
      const fractions = rings === 3 ? [0, 0.34, 0.66, 1] : Array.from({ length: rings + 1 }, (_, i) => i / rings);
      for (let ring = 1; ring <= rings; ring += 1) radii[ring] = fractions[ring] * rK;
    }
    for (let ring = 2; ring <= rings; ring += 1) {
      radii[ring] = Math.min(rOut, Math.max(radii[ring], radii[ring - 1] + RING_GAP_MIN));
    }
  }

  const ringInfo: RingInfo[] = radii.map((radius, ring) => {
    const count = idsOnRing[ring]?.length ?? 0;
    const gap = minGap(anglesOn(ring));
    return {
      radius,
      count,
      pitch: count > 1 ? (2 * Math.PI * radius) / count : Infinity,
      minPitch: count > 1 ? gap * radius : Infinity,
    };
  });

  // Marks: radius by the square root of acts in view, 4–16 px, capped by the
  // smallest pitch on the ring so neighbours never touch.
  const roots = data.nodes.map((node) => Math.sqrt(Math.max(1, node.acts_in_view)));
  const rootMin = Math.min(...roots);
  const rootMax = Math.max(...roots);
  const markRadius = (acts: number, ring: number) => {
    const t = rootMax <= rootMin ? 0.5 : (Math.sqrt(Math.max(1, acts)) - rootMin) / (rootMax - rootMin);
    const cap = Math.min(NODE_R_MAX, NODE_R_PITCH * (ringInfo[ring]?.minPitch ?? Infinity));
    return Math.max(NODE_R_MIN, Math.min(NODE_R_MIN + NODE_R_SPAN * t, cap));
  };

  const heirs = new Map<number, { all: boolean; any: boolean }>();
  for (const appearance of data.appearances) {
    const entry = heirs.get(appearance.person) ?? { all: true, any: false };
    entry.all = entry.all && appearance.heirs_of;
    entry.any = true;
    heirs.set(appearance.person, entry);
  }

  const nodes: PlacedNode[] = data.nodes.map((node) => {
    const ring = ringOf.get(node.id) ?? 0;
    const theta = angle.get(node.id) ?? 0;
    const radius = radii[ring] ?? 0;
    const heir = heirs.get(node.id);
    return {
      node,
      x: cx + radius * Math.sin(theta),
      y: cy - radius * Math.cos(theta),
      angle: theta,
      ring,
      r: markRadius(node.acts_in_view, ring),
      degree: degree.get(node.id) ?? 0,
      heirsOnly: Boolean(heir && heir.any && heir.all),
      anchor: ring === 0 ? null : (anchorOf.get(node.id) ?? data.centre),
      label: labels.get(node.id) ?? node.name,
    };
  });
  const byId = new Map(nodes.map((placed) => [placed.node.id, placed]));

  const lines: PlacedLine[] = data.ties
    .filter((tie) => byId.has(tie.a) && byId.has(tie.b))
    .map((tie) => {
      const spoke =
        (tie.a === data.centre && anchorOf.get(tie.b) === data.centre) ||
        (tie.b === data.centre && anchorOf.get(tie.a) === data.centre);
      const parent = anchorOf.get(tie.a) === tie.b || anchorOf.get(tie.b) === tie.a;
      return {
        key: pairKey(tie.a, tie.b),
        a: tie.a,
        b: tie.b,
        acts: tie.acts,
        kind: spoke ? "spoke" : parent ? "parent" : "tie",
        width: tieWidth(tie.acts.length),
      };
    });

  return { width, height, cx, cy, nodes, byId, lines, radii, rings: ringInfo, sharedActs };
}

/* The window (docs/network/next_2026-11-01.md §4.2, step2a_contract.md §4.3):
 * a mask in the client over the loaded circle. Only acts registered inside
 * the window make ties, a tie's width counts the acts inside, an undated act
 * is always inside, and an entry left with no tie keeps its place (the layout
 * above never reads the window). Nothing is refetched. */

export type NetworkWindow = {
  /** the window, or null for all years */
  years: [number, number] | null;
  /** the acts inside: every act with no window; else the dated ones in the
   * window and every undated one */
  inside: Set<number>;
  /** the ties with at least one act inside, each trimmed to those acts */
  ties: NetworkTie[];
  /** the entries left with no tie inside, the centre included when it has none */
  faded: Set<number>;
  /** the ring-1 entries with a tie to the centre inside */
  cosigners: number;
  /** the centre's acts inside */
  acts: number;
};

/** Whether an act counts inside the window: registered in it, or undated. */
export function actInside(act: NetworkAct | undefined, years: [number, number] | null): boolean {
  if (!years || !act) return true;
  return act.year === null || act.year === 0 || (act.year >= years[0] && act.year <= years[1]);
}

/** The window applied to a circle: a pure function of the response and the
 * window. With no window every act is inside, the ties are the response's
 * and nothing is faded, so the default state draws the full picture. */
export function windowTies(data: NetworkCircle, years: [number, number] | null): NetworkWindow {
  const actsById = new Map(data.acts.map((act) => [act.id, act]));
  const inside = new Set<number>();
  for (const act of data.acts) if (actInside(act, years)) inside.add(act.id);
  const ties: NetworkTie[] = [];
  const tied = new Set<number>();
  let cosigners = 0;
  for (const tie of data.ties) {
    const acts = years ? tie.acts.filter((id) => inside.has(id) || !actsById.has(id)) : tie.acts;
    if (!acts.length) continue;
    ties.push(years ? { a: tie.a, b: tie.b, acts } : tie);
    tied.add(tie.a);
    tied.add(tie.b);
    if (tie.a === data.centre || tie.b === data.centre) cosigners += 1;
  }
  const faded = new Set<number>();
  if (years) for (const node of data.nodes) if (!tied.has(node.id)) faded.add(node.id);
  const centreActs = new Set(data.appearances.filter((a) => a.person === data.centre).map((a) => a.act));
  const acts = [...centreActs].filter((id) => inside.has(id)).length;
  return { years, inside, ties, faded, cosigners, acts };
}

/** The strip's axis (components/NetworkStrip.tsx) spans at least this many years. */
const STRIP_MIN_SPAN = 60;

export type StripAxis = { start: number; end: number };

/** The decade-snapped axis of at least 60 years spanning the given years
 * (CareerSpanRibbon's rule): centred, snapped to decades, widened to the next
 * decade when the snap left a year outside, and kept inside the address's
 * 1400–1810. A year's bar runs from x(year) to x(year + 1), so the end is
 * always past the last year. */
export function stripAxis(first: number, last: number): StripAxis {
  const span = last - first;
  const target = Math.max(STRIP_MIN_SPAN, Math.ceil((span + 10) / 10) * 10);
  const mid = (first + last) / 2;
  let start = Math.floor((mid - target / 2) / 10) * 10;
  let end = start + target;
  if (end <= last) end = Math.floor(last / 10) * 10 + 10;
  if (start > first) start = Math.floor(first / 10) * 10;
  if (end > YEAR_MAX) {
    start -= end - YEAR_MAX;
    end = YEAR_MAX;
  }
  if (start < YEAR_MIN) {
    end += YEAR_MIN - start;
    start = YEAR_MIN;
  }
  return { start, end };
}
