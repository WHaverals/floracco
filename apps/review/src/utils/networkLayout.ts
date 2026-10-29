/* The ring layout of one entry's circle (docs/network/step1_contract.md §3,
 * scope §4.3 "Rings"): a pure function of the server's response, with no
 * simulation and no randomness, so the same response draws the same picture
 * on every machine.
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
 * sector. Rings start at radii 150, 290 and 400; if neighbours on a ring would
 * come closer than 9 px, that ring moves out until they do not, and the
 * figure's height grows with it. */

import type { NetworkAct, NetworkCircle, NetworkNode } from "../types";

export const VIEW_W = 960;
export const VIEW_H = 640;
const RING_RADII = [0, 150, 290, 400];
const RING_STEP = 110;
const MIN_SPACING = 9;
const MARGIN = 30;
export const NODE_R_MIN = 3;
export const NODE_R_MAX = 14;

export type PlacedNode = {
  node: NetworkNode;
  x: number;
  y: number;
  /** clockwise from the top, in radians */
  angle: number;
  ring: number;
  /** mark radius, by the square root of acts in view */
  r: number;
  /** co-signers in view */
  degree: number;
  /** every appearance in view is through heirs */
  heirsOnly: boolean;
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
};

function pairKey(a: number, b: number): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}

/** Line width by acts shared: 1, 2, 3–5, 6 or more. */
export function tieWidth(acts: number): number {
  if (acts >= 6) return 3.4;
  if (acts >= 3) return 2.3;
  if (acts === 2) return 1.5;
  return 0.9;
}

/** A sortable date: undated acts ("0000-00-00") come after every dated one. */
function sortableDate(act: NetworkAct | undefined): string {
  const date = act?.date ?? "";
  return !date || date.startsWith("0000") ? "9999-99-99" : date;
}

export function layoutCircle(data: NetworkCircle): NetworkLayout {
  const actsById = new Map(data.acts.map((act) => [act.id, act]));
  const nodesById = new Map(data.nodes.map((node) => [node.id, node]));
  const sharedActs = new Map<string, number[]>();
  const degree = new Map<number, number>();
  for (const tie of data.ties) {
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

  // Radii: the base radius of each ring, moved out when neighbours on the
  // ring would come closer than MIN_SPACING, and never inside the ring before.
  const rings = Math.max(0, ...ringOf.values());
  const radii: number[] = [0];
  for (let ring = 1; ring <= rings; ring += 1) {
    const base = RING_RADII[ring] ?? RING_RADII[RING_RADII.length - 1] + RING_STEP * (ring - RING_RADII.length + 1);
    const angles = [...ringOf.entries()]
      .filter(([, r]) => r === ring)
      .map(([id]) => angle.get(id) ?? 0)
      .sort((a, b) => a - b);
    let gap = 2 * Math.PI;
    for (let i = 1; i < angles.length; i += 1) gap = Math.min(gap, angles[i] - angles[i - 1]);
    if (angles.length > 1) gap = Math.min(gap, angles[0] + 2 * Math.PI - angles[angles.length - 1]);
    const needed = gap > 0 ? MIN_SPACING / gap : base;
    radii[ring] = Math.max(base, needed, radii[ring - 1] + (ring > 1 ? 100 : 0));
  }
  const outer = radii[rings] ?? 0;
  // A circle too wide for the frame shrinks to fit; the height grows up to the width.
  const scale = outer > 0 ? Math.min(1, (VIEW_W / 2 - MARGIN) / outer) : 1;
  const scaled = radii.map((r) => r * scale);
  const height = Math.max(VIEW_H, Math.ceil(2 * (outer * scale + MARGIN)));
  const cx = VIEW_W / 2;
  const cy = height / 2;

  // Marks: radius by the square root of acts in view, scaled 3–14.
  const roots = data.nodes.map((node) => Math.sqrt(Math.max(1, node.acts_in_view)));
  const rootMin = Math.min(...roots);
  const rootMax = Math.max(...roots);
  const markRadius = (acts: number) => {
    if (rootMax <= rootMin) return (NODE_R_MIN + NODE_R_MAX) / 2;
    const t = (Math.sqrt(Math.max(1, acts)) - rootMin) / (rootMax - rootMin);
    return NODE_R_MIN + (NODE_R_MAX - NODE_R_MIN) * t;
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
    const radius = scaled[ring] ?? 0;
    const heir = heirs.get(node.id);
    return {
      node,
      x: cx + radius * Math.sin(theta),
      y: cy - radius * Math.cos(theta),
      angle: theta,
      ring,
      r: markRadius(node.acts_in_view),
      degree: degree.get(node.id) ?? 0,
      heirsOnly: Boolean(heir && heir.any && heir.all),
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

  return { width: VIEW_W, height, cx, cy, nodes, byId, lines, radii: scaled };
}
