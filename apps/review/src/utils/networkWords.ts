/* The sentences the Network page says over the data it already loads
 * (docs/network/next_2026-11-01.md §4.1, docs/network/step2a_contract.md §3):
 * who recurs, the silence, each act's "from earlier acts / new", the centre's
 * two roles, the Jewish marks as the Database record's own words, and the
 * sort by acts together. Every count comes from `data.ties`, `data.acts` and
 * `data.appearances`; the centre's name is the caller's `nodeLabel`.
 *
 * Pure functions over `NetworkCircle`, free of React and of other imports so
 * they run under Node (the measurements in the doc comments were taken that
 * way against the API, read-only, on 1 November 2026). Numbers are digits,
 * except that a count of one inside a sentence is "one" and a sentence that
 * starts with it says "One". */

import type { NetworkAct, NetworkAppearance, NetworkCircle, NetworkNode } from "../types";

export type CosignerOrder = "first" | "together";

/** One co-signer of the centre with the acts the two share, in the response's
 * tie order. */
export type Cosigner = { node: NetworkNode; acts: number[] };

/** One of the centre's acts in the career order: dated acts by date, then the
 * undated ones; `earlier` and `fresh` are the co-signers on it who were, or were
 * not, on an earlier dated act of the centre. */
export type ActRow = { act: NetworkAct; cosigners: number[]; earlier: number[]; fresh: number[] };

function sortableDate(act: NetworkAct | undefined): string {
  const date = act?.date ?? "";
  return !date || date.startsWith("0000") ? "9999-99-99" : date;
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** "one" inside a sentence, digits otherwise. */
function few(n: number): string {
  return n === 1 ? "one" : String(n);
}

/** "One" at the head of a sentence, "No" for none, digits otherwise. */
function lead(n: number): string {
  return n === 1 ? "One" : n === 0 ? "No" : String(n);
}

/** The acts each co-signer shares with the centre, over the ties in the response. */
export function sharedWithCentre(data: NetworkCircle): Map<number, number[]> {
  const shared = new Map<number, number[]>();
  for (const tie of data.ties) {
    if (tie.a === data.centre) shared.set(tie.b, tie.acts);
    else if (tie.b === data.centre) shared.set(tie.a, tie.acts);
  }
  return shared;
}

/** The centre's acts in the career order: by registration date, undated last,
 * then by id. Every act of the centre is in view by construction. */
export function centreActs(data: NetworkCircle): NetworkAct[] {
  const byId = new Map(data.acts.map((act) => [act.id, act]));
  const mine = new Set(data.appearances.filter((a) => a.person === data.centre).map((a) => a.act));
  return [...mine]
    .map((id) => byId.get(id))
    .filter((act): act is NetworkAct => Boolean(act))
    .sort((p, q) => {
      const d = sortableDate(p);
      const e = sortableDate(q);
      return d < e ? -1 : d > e ? 1 : p.id - q.id;
    });
}

/** Who kept signing with the centre and who once; null on a one-act centre,
 * where the answer line already carries the count.
 *
 * Checked against the API on 1 November 2026: 4166 at one step, "37 co-signers:
 * 3 on three or more acts with Leonardo Tempi, 3 on two, 31 on one."; 5308,
 * "8 co-signers: 2 on two acts with Giuseppe Frescobaldi, 6 on one."; 11311 at
 * one step, "160 co-signers: 28 on three or more acts with Benedetto Tempi, 29
 * on two, 103 on one."; 5732 at one step, "34 co-signers: one on two acts with
 * Ferdinando Ximenes Aragona, 33 on one." (6804 Piero Lucchi is on two of his
 * acts, so the note's "Every co-signer is on one act with …" form, kept for
 * the entries whose every co-signer is met once, does not hold for 5732). */
export function careerLine(data: NetworkCircle, centreName: string): string | null {
  if (centreActs(data).length < 2) return null;
  const shared = sharedWithCentre(data);
  const total = shared.size;
  if (total === 0) return null;
  let three = 0;
  let two = 0;
  let one = 0;
  for (const acts of shared.values()) {
    if (acts.length >= 3) three += 1;
    else if (acts.length === 2) two += 1;
    else one += 1;
  }
  if (total === 1) {
    const n = [...shared.values()][0].length;
    const acts = n >= 3 ? "three or more acts" : n === 2 ? "two acts" : "one act";
    return `One co-signer, on ${acts} with ${centreName}.`;
  }
  const buckets: [number, string][] = [
    [three, "three or more"],
    [two, "two"],
    [one, "one"],
  ];
  const present = buckets.filter(([n]) => n > 0);
  if (present.length === 1) {
    const [, words] = present[0];
    return `Every co-signer is on ${words} ${words === "one" ? "act" : "acts"} with ${centreName}.`;
  }
  const parts = present.map(([n, words], index) =>
    index === 0 ? `${few(n)} on ${words} ${words === "one" ? "act" : "acts"} with ${centreName}` : `${few(n)} on ${words}`,
  );
  return `${total} co-signers: ${parts.join(", ")}.`;
}

/** The silence: the largest gap of 20 or more years between two consecutive
 * dated acts of the centre, with its appearances through heirs left out of the
 * sequence; null when there is none.
 *
 * Checked against the API on 1 November 2026: 11298 at one step, "59 years pass
 * between the act of 1540 and the next, of 1599."; null for 4166, 5308, 11311
 * and 5732. */
export function gapLine(data: NetworkCircle): string | null {
  const heirs = new Set(data.appearances.filter((a) => a.person === data.centre && a.heirs_of).map((a) => a.act));
  const dated = centreActs(data).filter((act) => act.year !== null && !heirs.has(act.id));
  let best: { years: number; from: number; to: number } | null = null;
  for (let i = 1; i < dated.length; i += 1) {
    const from = dated[i - 1].year as number;
    const to = dated[i].year as number;
    const years = to - from;
    if (years >= 20 && (best === null || years > best.years)) best = { years, from, to };
  }
  if (best === null) return null;
  return `${best.years} years pass between the act of ${best.from} and the next, of ${best.to}.`;
}

/** The centre's acts as rows, in the career order, each with the co-signers on
 * it split into those met on an earlier dated act of the centre and the new. */
export function actRows(data: NetworkCircle): ActRow[] {
  const acts = centreActs(data);
  const onAct = new Map<number, number[]>();
  for (const a of data.appearances) {
    if (a.person === data.centre) continue;
    const list = onAct.get(a.act) ?? [];
    list.push(a.person);
    onAct.set(a.act, list);
  }
  const met = new Set<number>();
  const rows: ActRow[] = [];
  for (const act of acts) {
    const cosigners = [...(onAct.get(act.id) ?? [])].sort((p, q) => p - q);
    const earlier = cosigners.filter((id) => met.has(id));
    const fresh = cosigners.filter((id) => !met.has(id));
    rows.push({ act, cosigners, earlier, fresh });
    // only a dated act counts as "earlier"; the undated acts stand last
    if (act.year !== null) for (const id of cosigners) met.add(id);
  }
  return rows;
}

/** One act row's words, for the centre's acts only (null for any other act):
 * "10 co-signers, the first act." / "7 co-signers: 2 from earlier acts, 5 new." /
 * "4 co-signers, all from earlier acts." / "3 co-signers, all new." / "One
 * co-signer, new."
 *
 * Checked against the API on 1 November 2026, 4166 at one step: act 2614 "10
 * co-signers, the first act."; 3223 "7 co-signers: 2 from earlier acts, 5 new.";
 * 3550 "4 co-signers, all from earlier acts."; 3698 "3 co-signers, all new." */
export function actRowLine(data: NetworkCircle, actId: number): string | null {
  const rows = actRows(data);
  const index = rows.findIndex((row) => row.act.id === actId);
  if (index < 0) return null;
  const { cosigners, earlier, fresh } = rows[index];
  const n = cosigners.length;
  const head = `${lead(n)} ${plural(n, "co-signer", "co-signers")}`;
  if (index === 0) return `${head}, the first act.`;
  if (n === 0) return `${head}.`;
  if (earlier.length === 0) return `${head}, ${n === 1 ? "new" : "all new"}.`;
  if (fresh.length === 0) return `${head}, ${n === 1 ? "from earlier acts" : "all from earlier acts"}.`;
  return `${head}: ${few(earlier.length)} from earlier acts, ${few(fresh.length)} new.`;
}

/** The co-signers the centre met in each of its two roles, counted per role the
 * centre held on the shared act (an entry counts under each role it met the
 * centre in); null when the centre held one role on every act, since the career
 * line and the answer already say the count.
 *
 * Checked against the API on 1 November 2026: 4166 at one step, "As general
 * partner, Leonardo Tempi signed with 6 co-signers; as external investor
 * (accomandante), with 34." (general partner on act 2599 alone; 3 entries met
 * him in both roles, so 6 + 34 exceeds the 37); 11298, "As general partner,
 * Inghilese Lotti signed with 2 co-signers; as external investor (accomandante),
 * with 10."; null for 5308, 11311 and 5732, one role throughout. */
export function roleLine(data: NetworkCircle, centreName: string): string | null {
  const roleOn = new Map<number, string | null>();
  for (const a of data.appearances) if (a.person === data.centre) roleOn.set(a.act, a.role);
  const held = new Set<string>();
  for (const role of roleOn.values()) {
    if (role === "gp" || role === "lp") held.add(role);
    else if (role === "both") held.add("gp").add("lp");
  }
  if (held.size < 2) return null;
  const asGp = new Set<number>();
  const asLp = new Set<number>();
  for (const [entry, acts] of sharedWithCentre(data)) {
    for (const act of acts) {
      const role = roleOn.get(act);
      if (role === "gp" || role === "both") asGp.add(entry);
      if (role === "lp" || role === "both") asLp.add(entry);
    }
  }
  const n = asGp.size;
  const m = asLp.size;
  const withN = n === 0 ? "none" : `${few(n)} ${plural(n, "co-signer", "co-signers")}`;
  const withM = m === 0 ? "none" : few(m);
  return `As general partner, ${centreName} signed with ${withN}; as external investor (accomandante), with ${withM}.`;
}

/** The Jewish marks in the Database page's own words, trimmed to a row's muted
 * line: "Jewish, stated in the act" / "Jewish, editorial attribution" / "".
 *
 * Checked on 1 November 2026 through the server's circle builder, read-only
 * (the five example circles carry no mark): entry 162 Isach Pardo on act 118,
 * "Jewish, stated in the act"; entry 1175 Salamone Conigliano on act 594,
 * "Jewish, editorial attribution". */
export function jewishWords(appearance: Pick<NetworkAppearance, "jewish">): string {
  if (appearance.jewish === "stated") return "Jewish, stated in the act";
  if (appearance.jewish === "attributed") return "Jewish, editorial attribution";
  return "";
}

/** The co-signers of the centre in one of two orders: "first", the ring order
 * the caller passes (the circle's clockwise order from the top, by first shared
 * act); "together", by acts shared with the centre descending, then the first
 * shared act, then id. Entries on ring 1 only, each with its shared acts.
 *
 * Checked against the API on 1 November 2026, 4166 at one step: "together"
 * opens with 6463 Ottaviano Acciaioli and 6465 Marco Martelli (6 acts each, the
 * lower id first on the same first act), then 11631 Bartolomeo Corsini (3). */
export function sortCosigners(data: NetworkCircle, order: CosignerOrder, ringOrder: number[]): Cosigner[] {
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  const actsById = new Map(data.acts.map((act) => [act.id, act]));
  const shared = sharedWithCentre(data);
  const firstActOf = (acts: number[]) =>
    acts.length ? acts.map((id) => `${sortableDate(actsById.get(id))}.${String(id).padStart(10, "0")}`).sort()[0] : "9999-99-99";
  const ids = order === "first" ? ringOrder : [...byId.keys()];
  const rows = ids
    .map((id) => byId.get(id))
    .filter((node): node is NetworkNode => Boolean(node) && node!.step === 1)
    .map((node) => ({ node, acts: shared.get(node.id) ?? [] }));
  if (order === "first") return rows;
  return rows.sort((p, q) => {
    if (q.acts.length !== p.acts.length) return q.acts.length - p.acts.length;
    const d = firstActOf(p.acts);
    const e = firstActOf(q.acts);
    return d < e ? -1 : d > e ? 1 : p.node.id - q.node.id;
  });
}
