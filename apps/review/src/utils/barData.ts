/* What an Analysis bar chart draws from a result (review 2026-10-27, 4.1 #1).
 *
 * A time grouping (year, decade) draws every period from the first to the last,
 * in date order: a period the result lacks gets an empty bar, marked 0 for a
 * count and "no contracts" for an amount, so gaps keep their place. Ranked
 * categories draw the largest BAR_LIMIT and say so ("Top 20 of 315"); the rest
 * become one "all others" bar only when their values can be added up: never for
 * capital, averages or percentages, nor for categories that overlap (one
 * contract can name several places). The scale always covers every row.
 *
 * Kept free of React and of other imports so tests/test_analysis_silent_errors.py
 * can run it under Node. */

export type Cell = string | number | null;
/** `text` replaces the number shown beside the bar (a period with no contracts). */
export type Bar = { label: string; value: number; others?: number; text?: string };
export type BarPlan = { bars: Bar[]; max: number; note: string };

export const BAR_LIMIT = 20;

const TIME_STEP: Record<string, number> = { year: 1, decade: 10 };
const MAX_FILLED_PERIODS = 1000;
const CAPITAL_COLUMNS = new Set(["total_capital", "average_capital", "avg_capital"]);
const AVERAGES = /^(average_capital|avg_capital|avg|min|max|pct_.*)$/;

function toNumber(cell: Cell): number {
  return typeof cell === "number" ? cell : cell == null || cell === "" ? NaN : Number(cell);
}

export function planBars(
  columns: string[],
  rows: Cell[][],
  options: {
    truncated?: boolean;
    totalRows?: number | null;
    /** Categories can share a contract, so their counts cannot be added up. */
    overlapping?: boolean;
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

  const step = TIME_STEP[labelAlias];
  if (step) {
    const byPeriod = new Map<number, Bar>();
    for (const row of rows) {
      const period = toNumber(row[0]);
      const value = toNumber(row[valueIndex]);
      if (Number.isFinite(period) && Number.isFinite(value)) byPeriod.set(period, { label: label(labelAlias, row[0]), value });
    }
    const periods = [...byPeriod.keys()].sort((a, b) => a - b);
    const first = periods[0];
    const last = periods[periods.length - 1];
    if (periods.length === 0 || (last - first) / step > MAX_FILLED_PERIODS) {
      return { bars: all, max: scaleOf(all), note: "" };
    }
    const bars: Bar[] = [];
    for (let period = first; period <= last; period += step) {
      bars.push(
        byPeriod.get(period) ??
          (amount
            ? { label: label(labelAlias, period), value: 0, text: "no contracts" }
            : { label: label(labelAlias, period), value: 0 }),
      );
    }
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
  const head = `Top ${BAR_LIMIT} of ${of}.`;
  const listed = (reason: string) => ({ bars: shown, max: scaleOf(all), note: `${head} The others are in the table below${reason}` });

  if (AVERAGES.test(valueAlias)) return listed("; averages and percentages cannot be added together.");
  if (CAPITAL_COLUMNS.has(valueAlias)) {
    // Grouped by currency, each row is its own money; within one currency the rows
    // could be added, but a capital total is left to the table.
    return listed(labelAlias === "currency" ? "; amounts in different currencies are never added together." : ".");
  }
  if (options.overlapping) return listed("; one contract can count under several of them, so they are listed rather than added up.");
  if (options.truncated) {
    // Only part of the result reached the screen, so a sum of "the rest" would be short.
    return { bars: shown, max: scaleOf(all), note: `${head} The others are in the download.` };
  }
  const others: Bar = {
    label: `All others (${rest.length.toLocaleString()})`,
    value: rest.reduce((sum, bar) => sum + bar.value, 0),
    others: rest.length,
  };
  return {
    bars: [...shown, others],
    max: scaleOf([...all, others]),
    note: `${head} The last bar adds up the other ${rest.length.toLocaleString()}.`,
  };
}
