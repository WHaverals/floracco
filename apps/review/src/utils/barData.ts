/* What an Analysis bar chart draws from a result (review 2026-10-27, 4.1 #1).
 *
 * A decade grouping draws every decade from the first to the last, in date
 * order: a decade the result lacks gets an empty bar, marked 0 for a count and
 * "none" for an amount (in a filtered question the decade may hold other
 * contracts, just none of these), so gaps keep their place. Ranked categories
 * draw the largest BAR_LIMIT and say so ("Top 20 of 315"); the rest become one
 * "all others" bar only when their values can be added up: never for capital,
 * averages or percentages, nor for categories that overlap (one contract can
 * name several places). The scale always covers every row.
 *
 * Kept free of React and of other imports so tests/test_analysis_silent_errors.py
 * can run it under Node. */

export type Cell = string | number | null;
/** `text` replaces the number shown beside the bar (a decade with none of these contracts). */
export type Bar = { label: string; value: number; others?: number; text?: string };
export type BarPlan = { bars: Bar[]; max: number; note: string };

export const BAR_LIMIT = 20;

const DECADE = 10;
const MAX_FILLED_DECADES = 60;
const FIRST_FILLED_YEAR = 1000; // a decade of 0 (undated) stays outside the fill
const CAPITAL_COLUMNS = new Set(["total_capital", "average_capital", "avg_capital"]);
const AVERAGES = /^(average_capital|avg_capital|avg|min|max|pct_.*)$/;

function toNumber(cell: Cell): number {
  return typeof cell === "number" ? cell : cell == null || cell === "" ? NaN : Number(cell);
}

/** Every decade from the first to the last, or null when the rows do not sit on
 * a decade grid the chart can fill (then they are drawn as they come). */
function fillDecades(
  rows: Cell[][],
  valueIndex: number,
  amount: boolean,
  label: (alias: string, cell: Cell) => string,
): Bar[] | null {
  const outside: Bar[] = [];
  const byDecade = new Map<number, Bar>();
  for (const row of rows) {
    const decade = toNumber(row[0]);
    const value = toNumber(row[valueIndex]);
    if (!Number.isFinite(value)) continue;
    const bar = { label: label("decade", row[0]), value };
    if (Number.isFinite(decade) && decade < FIRST_FILLED_YEAR) outside.push(bar);
    else if (Number.isInteger(decade) && decade % DECADE === 0) byDecade.set(decade, bar);
    else return null;
  }
  const decades = [...byDecade.keys()].sort((a, b) => a - b);
  if (decades.length === 0) return outside;
  const first = decades[0];
  const last = decades[decades.length - 1];
  if ((last - first) / DECADE + 1 > MAX_FILLED_DECADES) return null;
  const filled: Bar[] = [];
  for (let decade = first; decade <= last; decade += DECADE) {
    filled.push(
      byDecade.get(decade) ??
        (amount ? { label: label("decade", decade), value: 0, text: "none" } : { label: label("decade", decade), value: 0 }),
    );
  }
  return [...outside, ...filled];
}

export function planBars(
  columns: string[],
  rows: Cell[][],
  options: {
    truncated?: boolean;
    totalRows?: number | null;
    /** Set when one contract can count under several rows (places): the sentence
     * that says so. The rows past the top 20 are then never added up. */
    overlapping?: string;
    label?: (alias: string, cell: Cell) => string;
  } = {},
): BarPlan {
  const label = options.label ?? ((_alias: string, cell: Cell) => (cell == null ? "" : String(cell)));
  const labelAlias = columns[0] ?? "";
  const valueIndex = columns.length - 1;
  const valueAlias = columns[valueIndex] ?? "";
  const amount = CAPITAL_COLUMNS.has(valueAlias) || AVERAGES.test(valueAlias);
  const all: Bar[] = rows
    .map((row) => ({ label: row[0] == null ? "" : label(labelAlias, row[0]), value: toNumber(row[valueIndex]) }))
    .filter((bar) => Number.isFinite(bar.value));
  const scaleOf = (bars: Bar[]) => Math.max(1, ...bars.map((bar) => bar.value));

  if (labelAlias === "decade") {
    const bars = fillDecades(rows, valueIndex, amount, label) ?? all;
    return { bars, max: scaleOf(bars), note: "" };
  }

  if (all.length <= BAR_LIMIT) {
    return { bars: all, max: scaleOf(all), note: "" };
  }

  const ranked = [...all].sort((a, b) => b.value - a.value);
  const shown = ranked.slice(0, BAR_LIMIT);
  const rest = ranked.slice(BAR_LIMIT);
  const of = options.truncated
    ? options.totalRows != null
      ? options.totalRows.toLocaleString()
      : `more than ${all.length.toLocaleString()}`
    : all.length.toLocaleString();
  const head = `Top ${BAR_LIMIT} of ${of}`;

  if (amount || options.overlapping || options.truncated) {
    // Amounts in their own currencies, averages and percentages cannot be summed;
    // overlapping categories would count a contract twice; a capped result has
    // only part of the rest on screen.
    const where = options.truncated ? "in the download" : "in the table below";
    return {
      bars: shown,
      max: scaleOf(all),
      note: `${head}; the rest are ${where}.${options.overlapping ? ` ${options.overlapping}` : ""}`,
    };
  }
  const others: Bar = {
    label: `All others (${rest.length.toLocaleString()})`,
    value: rest.reduce((sum, bar) => sum + bar.value, 0),
    others: rest.length,
  };
  return { bars: [...shown, others], max: scaleOf([...all, others]), note: `${head}.` };
}
