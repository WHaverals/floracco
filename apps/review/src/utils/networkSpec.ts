/* The Network view's address (docs/network/step1_contract.md §3): the state of
 * the circle in the query string, defaults omitted, written 350 ms after a
 * change with `replace` and never re-read, parsed back with the parts that no
 * longer fit named on screen (as Find's links are, utils/findSpec.ts):
 *
 *   /network/person/5308?steps=1&hubs=through&others=off&widen=11477.3594&cap=300&select=person:11477
 *
 * Step 3 (docs/network/step3_contract.md §4): `firm={name}`, the stored firm
 * name URL-encoded, beside the circle's keys; while set the page shows the
 * firm view and the circle's keys stay, so removing it returns the circle.
 *
 * Step 4 (docs/network/step4_contract.md §4): `to={id}`, the second entry of
 * the chain (the circle's centre is the first), and `chain=any` when the reader
 * asks for the untimed chain while a forward one exists. Both left out of
 * circleKey: the chain is fetched on its own while `to` is set.
 *
 * Kept free of React and of other imports so it can run under Node. The server
 * checks every value again. */

import type { NetworkNode } from "../types";

export type NetworkHubs = "stop" | "through" | "off";
/** What the side column shows: an entry, an act, or a pair of entries (a < b)
 * whose shared acts are listed; `select=pair:a.b` in the address. */
export type NetworkSelect = { kind: "person" | "contract"; id: number } | { kind: "pair"; a: number; b: number };

export function pairSelect(a: number, b: number): NetworkSelect {
  return a < b ? { kind: "pair", a, b } : { kind: "pair", a: b, b: a };
}

export function sameSelect(p: NetworkSelect | null, q: NetworkSelect | null): boolean {
  if (!p || !q || p.kind !== q.kind) return p === q;
  if (p.kind === "pair" && q.kind === "pair") return p.a === q.a && p.b === q.b;
  return p.kind !== "pair" && q.kind !== "pair" && p.id === q.id;
}

/** The stored names that two or more drawn entries share, as entered. */
export function duplicateNames(nodes: Pick<NetworkNode, "name">[]): Set<string> {
  const seen = new Set<string>();
  const twice = new Set<string>();
  for (const node of nodes) (seen.has(node.name) ? twice : seen).add(node.name);
  return twice;
}

/** The name an entry is written with on the figure, in the balloon and in the
 * side column: the name as stored, and, when another drawn entry has the same
 * name, "di {father_mother}" after it where the patronymic is stored (the rail's
 * convention for search hits). Nothing asserts identity. */
export function nodeLabel(node: Pick<NetworkNode, "name" | "name_fields">, duplicates: Set<string>): string {
  const father = node.name_fields?.father_mother?.trim();
  return duplicates.has(node.name) && father ? `${node.name} di ${father}` : node.name;
}

export type NetworkState = {
  steps: 1 | 2;
  hubs: NetworkHubs;
  others: "on" | "off";
  /** entries widened by one more step from themselves, in order */
  widen: number[];
  cap: number;
  /** what the side table shows */
  select: NetworkSelect | null;
  /** the window on the strip, `years=1680-1689`: only acts registered inside make
   * ties; null = all years (added 2026-11-01) */
  years: [number, number] | null;
  /** the firm view (step 3): the stored firm name followed from an act, `firm=Mario+Morelli+e+compagni`;
   * null = the circle. Not part of circleKey: the circle is kept while it is set. */
  firm: string | null;
  /** the chain's second entry (step 4), `to=3188`; null = no chain. Not part of circleKey. */
  to: number | null;
  /** "any" when the reader asks for the untimed chain while a forward one exists
   * (`chain=any`); null = the forward chain when it exists, else the untimed one */
  chain: "any" | null;
};

export const CAP_MIN = 50;
export const CAP_MAX = 600;
export const DEFAULT_NETWORK: NetworkState = { steps: 2, hubs: "stop", others: "on", widen: [], cap: 300, select: null, years: null, firm: null, to: null, chain: null };
export const YEAR_MIN = 1400;
export const YEAR_MAX = 1810;

/** The three hub states in words (review 2026-10-29 §5). At one step the
 * first two draw the same picture, so the rail shows "drawn" and "left out". */
export const HUB_WORDS: Record<NetworkHubs, string> = {
  stop: "drawn; the second step stops at them",
  through: "drawn; the second step goes through them",
  off: "left out",
};

export const OTHERS_PHRASE = "institutions, estates, shareholder groups and placeholders";

const HUBS: NetworkHubs[] = ["stop", "through", "off"];
const ID = /^\d{1,10}$/;

/** The address as the page writes it; every default is left out. */
export function networkSearch(state: NetworkState): string {
  const query = new URLSearchParams();
  if (state.steps !== DEFAULT_NETWORK.steps) query.set("steps", String(state.steps));
  if (state.hubs !== DEFAULT_NETWORK.hubs) query.set("hubs", state.hubs);
  if (state.others !== DEFAULT_NETWORK.others) query.set("others", state.others);
  if (state.widen.length) query.set("widen", state.widen.join("."));
  if (state.cap !== DEFAULT_NETWORK.cap) query.set("cap", String(state.cap));
  if (state.years) query.set("years", `${state.years[0]}-${state.years[1]}`);
  if (state.firm) query.set("firm", state.firm);
  if (state.to !== null) query.set("to", String(state.to));
  if (state.chain) query.set("chain", state.chain);
  if (state.select) {
    query.set("select", state.select.kind === "pair" ? `pair:${state.select.a}.${state.select.b}` : `${state.select.kind}:${state.select.id}`);
  }
  return query.toString();
}

/** A link read back; whatever no longer fits is left out and named. Keys in
 * `ignore` belong to someone else and are passed over. */
export function parseNetworkSearch(search: string, ignore: string[] = []): { state: NetworkState; dropped: string[] } {
  const query = new URLSearchParams(search);
  const state: NetworkState = { ...DEFAULT_NETWORK, widen: [] };
  const dropped: string[] = [];
  for (const [key, raw] of query.entries()) {
    if (ignore.includes(key)) continue;
    if (key === "steps") {
      if (raw === "1" || raw === "2") state.steps = Number(raw) as 1 | 2;
      else dropped.push(`steps “${raw}”`);
    } else if (key === "hubs") {
      if (HUBS.includes(raw as NetworkHubs)) state.hubs = raw as NetworkHubs;
      else dropped.push(`hubs “${raw}”`);
    } else if (key === "others") {
      if (raw === "on" || raw === "off") state.others = raw;
      else dropped.push(`others “${raw}”`);
    } else if (key === "widen") {
      const parts = raw.split(".").filter(Boolean);
      if (parts.length && parts.every((p) => ID.test(p))) state.widen = [...new Set(parts.map(Number))];
      else dropped.push(`widen “${raw}”`);
    } else if (key === "cap") {
      const n = ID.test(raw) ? Number(raw) : NaN;
      if (n >= CAP_MIN && n <= CAP_MAX) state.cap = n;
      else dropped.push(`cap “${raw}”`);
    } else if (key === "years") {
      const span = /^(\d{4})-(\d{4})$/.exec(raw);
      const from = span ? Number(span[1]) : NaN;
      const to = span ? Number(span[2]) : NaN;
      if (span && from >= YEAR_MIN && to <= YEAR_MAX && from <= to) state.years = [from, to];
      else dropped.push(`years “${raw}”`);
    } else if (key === "firm") {
      // The stored name, trimmed of ASCII and non-breaking space at the ends as the server trims it.
      const name = raw.replace(/^[\s\u00a0]+|[\s\u00a0]+$/g, "");
      if (name) state.firm = name;
      else dropped.push(`firm “${raw}”`);
    } else if (key === "to") {
      if (ID.test(raw)) state.to = Number(raw);
      else dropped.push(`to “${raw}”`);
    } else if (key === "chain") {
      if (raw === "any") state.chain = raw;
      else dropped.push(`chain “${raw}”`);
    } else if (key === "select") {
      const match = /^(person|contract):(\d{1,10})$/.exec(raw);
      const pair = /^pair:(\d{1,10})\.(\d{1,10})$/.exec(raw);
      if (match) state.select = { kind: match[1] as "person" | "contract", id: Number(match[2]) };
      else if (pair && pair[1] !== pair[2]) state.select = pairSelect(Number(pair[1]), Number(pair[2]));
      else dropped.push(`select “${raw}”`);
    } else {
      dropped.push(`“${key}”`);
    }
  }
  return { state, dropped };
}

/** The part of the state the server answers to: a change here refetches. */
export function circleKey(personId: number, state: NetworkState): string {
  return JSON.stringify([personId, state.steps, state.hubs, state.others, state.widen, state.cap]);
}
