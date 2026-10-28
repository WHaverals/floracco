/* A Find question (review 2026-10-27, section 12) as the page holds it, as the
 * server receives it, and as it is written in the address, so a question can
 * be linked to, reopened and downloaded with its link:
 *
 *   /analysis/find?place=25&years=1700-1799&partner=any.jewish-either.residence-25
 *
 * Contract conditions, one key each, values separated by ".": `place` (place
 * ids), `activity` and `currency` (ids), `register` (as stored), `discretion` (a
 * choice), and `years` (a first and last year, either may be left empty:
 * "1700-", "-1799"). The value "none" is "not recorded"; a key written
 * `not-place` is the condition with "is not". `partner` holds one partner line:
 * its quantifier, then each condition about that same partner, separated by
 * ".": kind-choice ("role-gp"), a condition with one choice by its kind alone
 * ("woman"), one with values as kind-values joined by "_" ("residence-25_387",
 * "word-mercant"), each with "not-" before it for "is not". Repeated keys are
 * further conditions, in order. Two earlier sets combined (the set history) are
 * `combine` (and | or | not) with two `set` keys, each holding that set's own
 * address: "combine=and&set=place%3D25&set=years%3D1750-1799".
 *
 * Kept free of React and of other imports so tests/test_find.py can run it
 * under Node. The server checks every key and value again (find_normalize). */

export type Quantifier = "any" | "no" | "every";
/** A chosen value: an id, a register or a word; null is "not recorded". */
export type Value = number | string | null;
export type YearsCondition = { kind: "years"; from: string; to: string };
/** A condition with a choice (`choice`) or with values from a picker (`values`). */
export type Condition = { kind: string; not: boolean; choice?: string; values?: Value[] };
export type ContractCondition = YearsCondition | Condition;
export type PartnerLine = { quantifier: Quantifier; conditions: Condition[] };
export type Combine = "and" | "or" | "not";
export type FindState = {
  contract: ContractCondition[];
  partners: PartnerLine[];
  /** two earlier sets, combined; the conditions above apply on top */
  combine?: { op: Combine; sets: [FindState, FindState] };
};

/** What the server receives: years as whole numbers or null. */
export type FindSpec = {
  contract: ({ kind: "years"; from: number | null; to: number | null } | Condition)[];
  partners: PartnerLine[];
  combine?: Combine;
  sets?: [FindSpec, FindSpec];
};

const COMBINES: Combine[] = ["and", "or", "not"];
const MAX_DEPTH = 3;

/** Each condition kind as the registry describes it: its choices, or the kind
 * of values its picker offers ("place", "activity", "currency", "register",
 * "word"), and the words asking for a value ("Choose a place."). */
export type KindTable = Record<string, { choices?: string[]; values?: string; pick?: string }>;
export type Kinds = { contract: KindTable; partner: KindTable };

/** The kinds table, from the registry the server sends (find_registry). */
export function kindsFrom(registry: {
  contract_conditions: { kind: string; choices?: { key: string }[]; values?: string | null; pick?: string | null }[];
  partner_conditions: { kind: string; choices?: { key: string }[]; values?: string | null; pick?: string | null }[];
}): Kinds {
  const table = (list: typeof registry.contract_conditions): KindTable =>
    Object.fromEntries(
      list.map((k) => [
        k.kind,
        k.choices ? { choices: k.choices.map((c) => c.key) } : k.values ? { values: k.values, pick: k.pick ?? undefined } : {},
      ]),
    );
  return { contract: table(registry.contract_conditions), partner: table(registry.partner_conditions) };
}

/** Where a condition sits in the question. */
export type Target = { level: "contract"; index: number } | { level: "partner"; line: number; index: number };

const QUANTIFIERS: Quantifier[] = ["any", "no", "every"];
const YEAR = /^\d{1,4}$/;
const ID = /^\d{1,10}$/;
const REGISTER = /^[0-9A-Za-z]{1,20}$/;
/** One word: letters and digits, apostrophes inside (as the server's _FIND_WORD). */
export const WORD = /^[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*$/u;
const NONE = "none";

export const EMPTY_FIND: FindState = { contract: [], partners: [] };

export function isYears(c: ContractCondition): c is YearsCondition {
  return c.kind === "years";
}

/** A year box as typed: "" (empty), a whole year, or neither. */
function yearOf(text: string): number | null | undefined {
  const t = text.trim();
  if (!t) return null;
  return YEAR.test(t) ? Number(t) : undefined;
}

/** Why the question cannot run yet, in words, or "" when it can. */
export function incompleteReason(state: FindState, kinds: Kinds): string {
  for (const set of state.combine?.sets ?? []) {
    const reason = incompleteReason(set, kinds);
    if (reason) return reason;
  }
  for (const c of state.contract) {
    if (isYears(c)) {
      const from = yearOf(c.from);
      const to = yearOf(c.to);
      if (from === undefined || to === undefined) return "Enter years as whole numbers, such as 1700.";
      if (from === null && to === null) return "Enter a first year, a last year, or both.";
      if (from !== null && to !== null && from > to) return "The first year is after the last year.";
    } else if (c.values && c.values.length === 0) {
      return kinds.contract[c.kind]?.pick ?? "Choose a value.";
    }
  }
  for (const line of state.partners) {
    for (const c of line.conditions) {
      if (c.values && c.values.length === 0) return kinds.partner[c.kind]?.pick ?? "Choose a value.";
    }
  }
  return "";
}

function copyCondition(c: Condition): Condition {
  const out: Condition = { kind: c.kind, not: Boolean(c.not) };
  if (c.choice !== undefined) out.choice = c.choice;
  if (c.values !== undefined) out.values = [...c.values];
  return out;
}

export function toSpec(state: FindState): FindSpec {
  const spec: FindSpec = {
    contract: state.contract.map((c) =>
      isYears(c) ? { kind: "years" as const, from: yearOf(c.from) ?? null, to: yearOf(c.to) ?? null } : copyCondition(c),
    ),
    partners: state.partners.map((line) => ({ quantifier: line.quantifier, conditions: line.conditions.map(copyCondition) })),
  };
  if (state.combine) {
    spec.combine = state.combine.op;
    spec.sets = [toSpec(state.combine.sets[0]), toSpec(state.combine.sets[1])];
  }
  return spec;
}

/** A question the server wrote (a near miss) as the page holds it. */
export function fromSpec(spec: FindSpec): FindState {
  const state: FindState = {
    contract: spec.contract.map((c) =>
      c.kind === "years"
        ? { kind: "years", from: (c as { from: number | null }).from?.toString() ?? "", to: (c as { to: number | null }).to?.toString() ?? "" }
        : copyCondition(c as Condition),
    ),
    partners: spec.partners.map((line) => ({ quantifier: line.quantifier, conditions: line.conditions.map(copyCondition) })),
  };
  if (spec.combine && spec.sets) state.combine = { op: spec.combine, sets: [fromSpec(spec.sets[0]), fromSpec(spec.sets[1])] };
  return state;
}

/** The question without the conditions still being filled in (no value chosen,
 * no year typed), which the address and the counts leave out until they have one. */
export function complete(state: FindState): FindState {
  const contract = state.contract.filter((c) =>
    isYears(c) ? yearOf(c.from) !== undefined && yearOf(c.to) !== undefined && (yearOf(c.from) !== null || yearOf(c.to) !== null) : !c.values || c.values.length > 0,
  );
  const partners = state.partners
    .map((line) => ({ ...line, conditions: line.conditions.filter((c) => !c.values || c.values.length > 0) }))
    .filter((line) => line.conditions.length > 0);
  return state.combine ? { contract, partners, combine: state.combine } : { contract, partners };
}

function valueToken(v: Value): string {
  return v === null ? NONE : String(v);
}

function valueOf(token: string, kind: string | undefined): Value | undefined {
  if (token === NONE) return kind === "word" ? undefined : null;
  if (kind === "place" || kind === "activity" || kind === "currency") return ID.test(token) ? Number(token) : undefined;
  if (kind === "register") return REGISTER.test(token) ? token : undefined;
  if (kind === "word") return WORD.test(token) ? token : undefined;
  return undefined;
}

/** One condition as the partner line writes it ("not-residence-25_none"). */
export function conditionToken(c: Condition, table: KindTable): string {
  const head = `${c.not ? "not-" : ""}${c.kind}`;
  if (c.values) return `${head}-${c.values.map(valueToken).join("_")}`;
  return (table[c.kind]?.choices ?? []).length > 1 ? `${head}-${c.choice}` : head;
}

/** The question as the address writes it. A condition still being filled in
 * (no value chosen yet, both years empty) is left out until it has a value. */
export function findSearch(state: FindState, kinds: Kinds): string {
  const query = new URLSearchParams();
  if (state.combine) {
    query.append("combine", state.combine.op);
    for (const set of state.combine.sets) query.append("set", findSearch(set, kinds));
  }
  for (const c of complete(state).contract) {
    if (isYears(c)) query.append("years", `${c.from.trim()}-${c.to.trim()}`);
    else query.append(`${c.not ? "not-" : ""}${c.kind}`, c.values ? c.values.map(valueToken).join(".") : (c.choice ?? ""));
  }
  for (const line of complete(state).partners) {
    query.append("partner", [line.quantifier, ...line.conditions.map((c) => conditionToken(c, kinds.partner))].join("."));
  }
  return query.toString();
}

/** One condition read from the address against today's registry, or null. */
function readCondition(kind: string, not: boolean, rest: string | null, separator: string, table: KindTable): Condition | null {
  const entry = table[kind];
  if (!entry) return null;
  if (entry.values) {
    if (!rest) return null;
    const values = rest.split(separator).map((t) => valueOf(t, entry.values));
    if (values.some((v) => v === undefined)) return null;
    return { kind, not, values: [...new Set(values as Value[])] };
  }
  const options = entry.choices ?? [];
  const choice = rest ?? (options.length === 1 ? options[0] : "");
  return options.includes(choice) ? { kind, not, choice } : null;
}

/** A condition read from its token (conditionToken), or null when it no longer fits. */
export function conditionOf(token: string, table: KindTable): Condition | null {
  const not = token.startsWith("not-");
  const body = not ? token.slice(4) : token;
  const dash = body.indexOf("-");
  return readCondition(dash < 0 ? body : body.slice(0, dash), not, dash < 0 ? null : body.slice(dash + 1), "_", table);
}

/** A Find link read back against today's registry; whatever no longer fits is
 * left out and named, as the builder does with its links. Keys in `ignore` (the
 * view's, findView.ts) belong to someone else and are passed over. */
export function parseFind(search: string, kinds: Kinds, ignore: string[] = [], depth = 0): { state: FindState; dropped: string[] } {
  const query = new URLSearchParams(search);
  const state: FindState = { contract: [], partners: [] };
  const dropped: string[] = [];
  let combine: Combine | null = null;
  const sets: FindState[] = [];
  for (const [key, raw] of query.entries()) {
    if (ignore.includes(key)) continue;
    if (key === "combine") {
      if (COMBINES.includes(raw as Combine)) combine = raw as Combine;
      else dropped.push(`combine “${raw}”`);
    } else if (key === "set") {
      if (depth + 1 >= MAX_DEPTH + 1 || sets.length >= 2) dropped.push("a combined set");
      else {
        const inner = parseFind(raw, kinds, ignore, depth + 1);
        sets.push(inner.state);
        dropped.push(...inner.dropped);
      }
    } else if (key === "years") {
      const match = /^(\d{0,4})-(\d{0,4})$/.exec(raw);
      if (match && (match[1] || match[2])) state.contract.push({ kind: "years", from: match[1], to: match[2] });
      else dropped.push(`years “${raw}”`);
    } else if (key === "partner") {
      const [quantifier, ...parts] = raw.split(".");
      const conditions: Condition[] = [];
      let ok = QUANTIFIERS.includes(quantifier as Quantifier) && parts.length > 0;
      for (const part of parts) {
        const read = conditionOf(part, kinds.partner);
        if (read) conditions.push(read);
        else ok = false;
      }
      if (ok) state.partners.push({ quantifier: quantifier as Quantifier, conditions });
      else dropped.push(`partner line “${raw}”`);
    } else {
      const not = key.startsWith("not-");
      const kind = not ? key.slice(4) : key;
      const read = kinds.contract[kind] && kind !== "years" ? readCondition(kind, not, raw, ".", kinds.contract) : null;
      if (read) state.contract.push(read);
      else dropped.push(kinds.contract[kind] ? `${kind} “${raw}”` : `“${key}”`);
    }
  }
  if (combine && sets.length === 2) state.combine = { op: combine, sets: [sets[0], sets[1]] };
  else if (combine || sets.length) dropped.push("the combined sets");
  return { state, dropped };
}

/** The opening words of each partner line, as the server writes them in the
 * sentence (build_find_sql): the first line's reading, a later line's, and for a
 * later "at least one" after an earlier "at least one" or "every", the reading
 * that says the partner may be the same one or another. */
export function lineOpenings(
  lines: { quantifier: Quantifier }[],
  readings: Record<Quantifier, { reading: string; later: string }>,
  sameOrAnother: string,
): string[] {
  let seenAny = false;
  return lines.map((line, i) => {
    const words = readings[line.quantifier];
    const opening = i === 0 ? words.reading : line.quantifier === "any" && seenAny ? sameOrAnother : words.later;
    if (line.quantifier !== "no") seenAny = true;
    return opening;
  });
}

// ---- the counts inside the pickers (12.2) ----

/** The question with the condition at `target` replaced. */
export function replaceAt(state: FindState, target: Target, condition: Condition): FindState {
  if (target.level === "contract") {
    return { ...state, contract: state.contract.map((c, i) => (i === target.index ? condition : c)) };
  }
  return {
    ...state,
    partners: state.partners.map((line, i) =>
      i === target.line ? { ...line, conditions: line.conditions.map((c, k) => (k === target.index ? condition : c)) } : line,
    ),
  };
}

/** The condition at `target` on its own: the count "in the corpus". */
export function aloneAt(state: FindState, target: Target, condition: Condition): FindState {
  if (target.level === "contract") return { contract: [condition], partners: [] };
  return { contract: [], partners: [{ quantifier: state.partners[target.line]?.quantifier ?? "any", conditions: [condition] }] };
}

/** The question with a condition about a partner added to line `line`, the
 * same partner; or, with `line` null, as a further "at least one" line: another
 * partner, or the same one. */
export function placeCondition(state: FindState, condition: Condition, line: number | null): FindState {
  if (line === null) return { ...state, partners: [...state.partners, { quantifier: "any", conditions: [condition] }] };
  return {
    ...state,
    partners: state.partners.map((l, i) => (i === line ? { ...l, conditions: [...l.conditions, condition] } : l)),
  };
}

/** For each option of the condition at `target`, the questions whose counts the
 * picker shows: this question with the option in its place ("here"), and the
 * option on its own ("alone"). With `target` null the condition is about to be
 * added as a further partner line. Conditions still being filled in elsewhere
 * are left out. */
export function optionSpecs(state: FindState, target: Target | null, options: Condition[]): { here: FindSpec; alone: FindSpec }[] {
  return options.map((option) => ({
    here: toSpec(complete(target ? replaceAt(state, target, option) : placeCondition(state, option, null))),
    alone: toSpec(target ? aloneAt(state, target, option) : { contract: [], partners: [{ quantifier: "any", conditions: [option] }] }),
  }));
}

/** The answers to "about which partner?": the same partner as each existing
 * line, then another partner (a further line), each as the question it makes. */
export function placementSpecs(state: FindState, condition: Condition): { line: number | null; spec: FindSpec }[] {
  const lines: (number | null)[] = [...state.partners.map((_l, i) => i), null];
  return lines.map((line) => ({ line, spec: toSpec(complete(placeCondition(state, condition, line))) }));
}

/** A question with no condition and no combined sets: the whole corpus, which
 * the pane shows as its landing (review 12.1) until a condition is added. */
export function isEmpty(state: FindState): boolean {
  return !state.combine && state.contract.length === 0 && state.partners.length === 0;
}
