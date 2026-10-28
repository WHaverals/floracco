/* The views over a Find set (review 2026-10-27, 12.3; build path 12.8 step 3):
 * which view is open and how, as the page holds it and as the address writes it
 * beside the question, and the arithmetic the charts and tables share.
 *
 *   /analysis/find?place=25&partner=any.jewish-either&view=time&period=half&show=share&base=1
 *
 * `view` list | count | time | compare; Count: `by`, `by2` (groupings); Over
 * time and Compare: `period` decade | half, `show` count | share, `base` (the
 * question's earlier step the share is of: 0 is all contracts); Compare: `split`
 * (a grouping), or `beside` (repeated): the same question with one condition
 * changed, written as where~condition ("c0~place-16", "p0.1~role-lp"); See
 * their partners: `view=partners` with `partners` every | matched; `saved`,
 * the saved question this address was opened from; and `all=1`, the whole
 * corpus as a set, where a question with no condition would otherwise show the
 * landing (the corpus and its standing figures, review 12.1).
 *
 * Kept free of React and of other imports so tests/test_find.py can run it
 * under Node. */

export type ViewKind = "list" | "count" | "time" | "compare" | "partners";
export type Beside = { target: string; token: string };
export type FindView = {
  view: ViewKind;
  by: string;
  by2: string;
  period: "decade" | "half";
  show: "count" | "share";
  base: number;
  split: string;
  beside: Beside[];
  partners: "every" | "matched";
  saved: string;
  /** with no condition: the corpus as a set, in place of the landing */
  all: boolean;
};

export const VIEW_KEYS = ["view", "by", "by2", "period", "show", "base", "split", "beside", "partners", "saved", "all"];
export const DEFAULT_VIEW: FindView = {
  view: "list",
  by: "decade",
  by2: "",
  period: "decade",
  show: "count",
  base: 0,
  split: "",
  beside: [],
  partners: "every",
  saved: "",
  all: false,
};
export const MAX_BESIDE = 3;
const SAVED = /^[0-9a-f]{12}$/;
const KEY = /^[a-z]{1,20}$/;
const TARGET = /^(c\d{1,2}|p\d{1,2}\.\d{1,2})$/;
const TOKEN = /^[^~&=]{1,300}$/;

/** The view as the address writes it: only what differs from the default. */
export function viewSearch(view: FindView): string {
  const query = new URLSearchParams();
  if (view.view !== "list") query.append("view", view.view);
  if (view.by !== DEFAULT_VIEW.by) query.append("by", view.by);
  if (view.by2) query.append("by2", view.by2);
  if (view.period !== DEFAULT_VIEW.period) query.append("period", view.period);
  if (view.show !== DEFAULT_VIEW.show) query.append("show", view.show);
  if (view.base) query.append("base", String(view.base));
  if (view.split) query.append("split", view.split);
  for (const b of view.beside) query.append("beside", `${b.target}~${b.token}`);
  if (view.partners !== DEFAULT_VIEW.partners) query.append("partners", view.partners);
  if (view.saved) query.append("saved", view.saved);
  if (view.all) query.append("all", "1");
  return query.toString();
}

/** The view read from an address; anything that does not fit falls back to the default. */
export function parseView(search: string): FindView {
  const query = new URLSearchParams(search);
  const view: FindView = { ...DEFAULT_VIEW, beside: [] };
  const kind = query.get("view");
  if (kind === "count" || kind === "time" || kind === "compare" || kind === "partners") view.view = kind;
  if (query.get("partners") === "matched") view.partners = "matched";
  const saved = query.get("saved");
  if (saved && SAVED.test(saved)) view.saved = saved;
  if (query.get("all") === "1") view.all = true;
  const by = query.get("by");
  if (by && KEY.test(by)) view.by = by;
  const by2 = query.get("by2");
  if (by2 && KEY.test(by2) && by2 !== view.by) view.by2 = by2;
  if (query.get("period") === "half") view.period = "half";
  if (query.get("show") === "share") view.show = "share";
  const base = Number(query.get("base"));
  if (Number.isInteger(base) && base > 0 && base < 20) view.base = base;
  const split = query.get("split");
  if (split && KEY.test(split)) view.split = split;
  for (const raw of query.getAll("beside").slice(0, MAX_BESIDE)) {
    const at = raw.indexOf("~");
    const target = raw.slice(0, at);
    const token = raw.slice(at + 1);
    if (at > 0 && TARGET.test(target) && TOKEN.test(token)) view.beside.push({ target, token });
  }
  return view;
}

/** A view the server names in part (a starting question, a standing figure),
 * the rest left as the default. */
export function viewOf(partial: Partial<FindView>): FindView {
  return { ...DEFAULT_VIEW, beside: [], ...partial };
}

/** An address without the view's keys: the question alone. */
export function questionPart(search: string): string {
  const query = new URLSearchParams(search);
  for (const key of VIEW_KEYS) query.delete(key);
  return query.toString();
}

/** The question's address and the view's, as one. */
export function joinSearch(question: string, view: string): string {
  return [question, view].filter(Boolean).join("&");
}

/** Where a chip sits, written as the address writes it, and back. */
export function targetOf(t: { level: "contract"; index: number } | { level: "partner"; line: number; index: number }): string {
  return t.level === "contract" ? `c${t.index}` : `p${t.line}.${t.index}`;
}

export function readTarget(text: string): { level: "contract"; index: number } | { level: "partner"; line: number; index: number } | null {
  const contract = /^c(\d+)$/.exec(text);
  if (contract) return { level: "contract", index: Number(contract[1]) };
  const partner = /^p(\d+)\.(\d+)$/.exec(text);
  return partner ? { level: "partner", line: Number(partner[1]), index: Number(partner[2]) } : null;
}

/** A count as a share of its base, or null where the base holds nothing. */
export function share(count: number, base: number): number | null {
  return base > 0 ? (100 * count) / base : null;
}

export function percentText(value: number | null): string {
  if (value === null) return "–";
  return `${value < 10 && value > 0 ? value.toFixed(1) : value.toFixed(value >= 99.95 ? 0 : 1)}%`;
}

/** Shares under this base are hatched and named as resting on few contracts. */
export const LOW_BASE = 10;

/** Round ticks from 0 to at least `max`: 0, 50, 100 … (four to six of them). */
export function niceTicks(max: number): number[] {
  if (!(max > 0)) return [0, 1];
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const ticks: number[] = [];
  for (let t = 0; t < max + step * 0.999; t += step) ticks.push(Number(t.toFixed(10)));
  return ticks;
}

/** The label of a period on the axis and in the table: "1750–1759", "1440s"
 * on a crowded axis; a partial first or last period by the years it holds. */
export function periodLabel(start: number, width: number, partial: { first?: string; last?: string }, periods: number[]): string {
  if (partial.first && start === periods[0]) return partial.first;
  if (partial.last && start === periods[periods.length - 1]) return partial.last;
  return `${start}–${start + width - 1}`;
}
