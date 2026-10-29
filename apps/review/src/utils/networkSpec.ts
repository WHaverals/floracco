/* The Network view's address (docs/network/step1_contract.md §3): the state of
 * the circle in the query string, defaults omitted, written 350 ms after a
 * change with `replace` and never re-read, parsed back with the parts that no
 * longer fit named on screen (as Find's links are, utils/findSpec.ts):
 *
 *   /network/person/5308?steps=1&hubs=through&others=off&widen=11477.3594&cap=300&select=person:11477
 *
 * Kept free of React and of other imports so it can run under Node. The server
 * checks every value again. */

export type NetworkHubs = "stop" | "through" | "off";
export type NetworkSelect = { kind: "person" | "contract"; id: number };

export type NetworkState = {
  steps: 1 | 2;
  hubs: NetworkHubs;
  others: "on" | "off";
  /** entries widened by one more step from themselves, in order */
  widen: number[];
  cap: number;
  /** what the side table shows */
  select: NetworkSelect | null;
};

export const CAP_MIN = 50;
export const CAP_MAX = 600;
export const DEFAULT_NETWORK: NetworkState = { steps: 2, hubs: "stop", others: "on", widen: [], cap: 300, select: null };

/** The three hub states in words (contract §6). */
export const HUB_WORDS: Record<NetworkHubs, string> = {
  stop: "drawn but not widened through",
  through: "widened through",
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
  if (state.select) query.set("select", `${state.select.kind}:${state.select.id}`);
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
    } else if (key === "select") {
      const match = /^(person|contract):(\d{1,10})$/.exec(raw);
      if (match) state.select = { kind: match[1] as "person" | "contract", id: Number(match[2]) };
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
