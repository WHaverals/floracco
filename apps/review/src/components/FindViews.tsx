import { useEffect, useMemo, useRef, useState } from "react";
import { AddMenu, ConditionChip, contractsWord } from "./FindChips";
import { findCount, findTime } from "../api";
import { useLatest } from "../utils/latest";
import type { FindCountResult, FindGroup, FindKind, FindRegistry, FindTimeResult } from "../types";
import {
  complete,
  conditionOf,
  conditionToken,
  isYears,
  replaceAt,
  toSpec,
  type Condition,
  type FindSpec,
  type FindState,
  type Kinds,
} from "../utils/findSpec";
import {
  LOW_BASE,
  MAX_BESIDE,
  niceTicks,
  percentText,
  periodLabel,
  readTarget,
  share,
  targetOf,
  type FindView,
} from "../utils/findView";

/* Find, step 3 (review 2026-10-27, 12.3; build path 12.8): Count, Over time and
 * Compare, views over the same set as the List. Count is a table with one or two
 * groupings, each count opening its contracts in the List. Over time and Compare
 * draw on the corpus's decade (or half-century) axis, every period kept, with a
 * y-axis and its unit, the base named for a share and hatched under 10, and the
 * question, the base and a source line drawn into the figure, which saves as
 * SVG or PNG (recommendation 4). Series colours: the validated categorical
 * order (blue, orange, aqua, yellow) on the chart surface; "all others" grey. */

const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
const OTHERS = "#8f8a80";
const SURFACE = "#fffdf9";
const INK = "#241d18";
const INK_2 = "#5e5248";
const MUTED = "#7d7064";
const GRID = "#ece4d8";
const AXIS = "#c9bba6";
const FONT = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";

function saveFile(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Fetch a view's data when its key changes; the last answer stays on screen,
 * marked stale, until the new one arrives. */
function useViewData<T>(key: string | null, load: (key: string) => Promise<T>): { data: T | null; error: string; stale: boolean } {
  const [found, setFound] = useState<{ key: string; data: T } | null>(null);
  const [error, setError] = useState("");
  const begin = useLatest();
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    if (!key) return;
    const fresh = begin();
    const handle = window.setTimeout(() => {
      setError("");
      loader.current(key)
        .then((data) => fresh() && setFound({ key, data }))
        .catch((e: Error) => fresh() && setError(e.message));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [key, begin]);
  return { data: found?.data ?? null, error, stale: Boolean(found && found.key !== key) };
}

// ---- Count -----------------------------------------------------------------------

type CountShow = "count" | "row" | "column" | "set";
const COUNT_SHOWS: { key: CountShow; label: string; base: (total: number) => string }[] = [
  { key: "count", label: "counts", base: () => "" },
  { key: "row", label: "% of its row", base: () => "Each cell as a share of its row's total, in the last column." },
  { key: "column", label: "% of its column", base: () => "Each cell as a share of its column's total, in the last row." },
  { key: "set", label: "% of the set", base: (total) => `Each cell as a share of all ${contractsWord(total)} in the set.` },
];

export function CountView({
  specKey,
  groupings,
  view,
  setView,
  onOpen,
}: {
  specKey: string;
  groupings: FindGroup[];
  view: FindView;
  setView: (next: Partial<FindView>) => void;
  onOpen: (ids: number[], label: string) => void;
}) {
  const [show, setShow] = useState<CountShow>("count");
  const by = groupings.some((g) => g.key === view.by) ? view.by : (groupings[0]?.key ?? "decade");
  const by2 = view.by2 && view.by2 !== by && groupings.some((g) => g.key === view.by2) ? view.by2 : "";
  const { data, error, stale } = useViewData<FindCountResult>(JSON.stringify([specKey, by, by2]), (key) => {
    const [spec, first, second] = JSON.parse(key);
    return findCount(JSON.parse(spec), first, second);
  });
  const controls = (
    <div className="find-view-controls">
      <label>
        Count contracts by{" "}
        <select value={by} onChange={(e) => setView({ by: e.target.value, by2: by2 === e.target.value ? "" : by2 })}>
          {groupings.map((g) => (
            <option key={g.key} value={g.key}>
              {g.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        and by{" "}
        <select value={by2} onChange={(e) => setView({ by2: e.target.value })}>
          <option value="">nothing more</option>
          {groupings
            .filter((g) => g.key !== by)
            .map((g) => (
              <option key={g.key} value={g.key}>
                {g.label}
              </option>
            ))}
        </select>
      </label>
      {by2 && (
        <span className="find-segmented" role="group" aria-label="Show">
          {COUNT_SHOWS.map((s) => (
            <button key={s.key} type="button" className={show === s.key ? "is-active" : ""} onClick={() => setShow(s.key)}>
              {s.label}
            </button>
          ))}
        </span>
      )}
    </div>
  );
  if (error) {
    return (
      <div className="find-view">
        {controls}
        <p className="error-text">{error}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="find-view">
        {controls}
        <p className="muted">Loading…</p>
      </div>
    );
  }
  const total = data.total;
  return (
    <div className={stale ? "find-view is-updating" : "find-view"}>
      {controls}
      {!data.columns ? (
        <table className="an-table find-count-table">
          <thead>
            <tr>
              <th>{data.by.label}</th>
              <th className="num">Contracts</th>
              <th>Share of the {contractsWord(total)} in the set</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => {
              const pct = share(row.ids.length, total);
              return (
                <tr key={String(row.value)} className={row.value === "others" || row.value === null ? "is-aside" : ""}>
                  <td>{row.name}</td>
                  <td className="num">
                    <button type="button" className="find-cell" onClick={() => onOpen(row.ids, row.name)} disabled={!row.ids.length}>
                      {row.ids.length.toLocaleString()}
                    </button>
                  </td>
                  <td>
                    <span className="find-share">
                      <span className="find-share-bar" style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
                    </span>
                    <span className="find-share-text">{percentText(pct)}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <CrossTable data={data} show={show} onOpen={onOpen} />
      )}
      {(by2 ? [COUNT_SHOWS.find((s) => s.key === show)!.base(total)] : []).concat(data.notes).filter(Boolean).map((note) => (
        <p key={note} className="muted an-note">
          {note}
        </p>
      ))}
      {data.notes.some((n) => n.includes("counted under each")) && !by2 && (
        <p className="muted an-note">So the shares add up to more than 100%.</p>
      )}
    </div>
  );
}

function CrossTable({ data, show, onOpen }: { data: FindCountResult; show: CountShow; onOpen: (ids: number[], label: string) => void }) {
  const columns = data.columns ?? [];
  const text = (n: number, row: number, column: number) =>
    show === "count"
      ? n.toLocaleString()
      : percentText(share(n, show === "row" ? row : show === "column" ? column : data.total));
  return (
    <div className="an-table-wrap">
      <table className="an-table find-count-table find-cross">
        <caption>
          Rows: {data.by.label}. Columns: {data.by2?.label}.
        </caption>
        <thead>
          <tr>
            <th />
            {columns.map((c) => (
              <th key={String(c.value)} className="num">
                {c.name}
              </th>
            ))}
            <th className="num">Row total</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={String(row.value)} className={row.value === "others" || row.value === null ? "is-aside" : ""}>
              <td>{row.name}</td>
              {(row.cells ?? []).map((cell, k) => (
                <td key={k} className="num">
                  <button
                    type="button"
                    className="find-cell"
                    disabled={!cell.ids.length}
                    onClick={() => onOpen(cell.ids, `${row.name} · ${columns[k].name}`)}
                  >
                    {text(cell.ids.length, row.ids.length, columns[k].ids.length)}
                  </button>
                </td>
              ))}
              <td className="num">
                <button type="button" className="find-cell" onClick={() => onOpen(row.ids, row.name)}>
                  {show === "row" ? "100%" : text(row.ids.length, row.ids.length, data.total)}
                </button>
              </td>
            </tr>
          ))}
          <tr className="find-total-row">
            <td>Column total</td>
            {columns.map((c) => (
              <td key={String(c.value)} className="num">
                <button type="button" className="find-cell" onClick={() => onOpen(c.ids, c.name)}>
                  {show === "column" ? "100%" : text(c.ids.length, data.total, c.ids.length)}
                </button>
              </td>
            ))}
            <td className="num">{show === "count" ? data.total.toLocaleString() : show === "set" ? "100%" : ""}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ---- the figure on the decade axis ------------------------------------------------

export type ChartSeries = {
  label: string;
  color: string;
  values: (number | null)[];
  /** the share at this period rests on fewer than LOW_BASE contracts */
  low: boolean[];
  parts?: { label: string; color: string; values: (number | null)[] }[];
};

type TipRow = { color: string; value: string; label: string; note?: string };

function wrap(text: string, max: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    if (line && (line + " " + word).length > max) {
      lines.push(line);
      line = word;
    } else line = line ? `${line} ${word}` : word;
  }
  if (line) lines.push(line);
  return lines;
}

function barPath(x: number, y: number, w: number, h: number, round: boolean): string {
  const r = round ? Math.min(4, w / 2, h) : 0;
  return `M${x},${y + h}V${y + r}${r ? `Q${x},${y} ${x + r},${y}` : ""}H${x + w - r}${r ? `Q${x + w},${y} ${x + w},${y + r}` : ""}V${y + h}Z`;
}

/** A figure on the decade axis: bars for one series (stacked by its parts) or
 * lines for several. The question, what is drawn, the notes and the source are
 * drawn into it, so the saved SVG or PNG carries them. */
export function TimeChart({
  title,
  subtitle,
  yTitle,
  periods,
  width,
  partial,
  series,
  form,
  percent,
  notes,
  source,
  tip,
  fileName,
}: {
  title: string;
  subtitle: string;
  yTitle: string;
  periods: number[];
  width: number;
  partial: { first?: string; last?: string };
  series: ChartSeries[];
  form: "bars" | "lines";
  percent: boolean;
  notes: string[];
  source: string;
  tip: (index: number) => { title: string; rows: TipRow[] };
  fileName: string;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 960;
  const left = 72;
  const right = 24;
  const titleLines = wrap(title, 104);
  let y = 30;
  const titleTop = y;
  y += titleLines.length * 20;
  const subtitleLines = wrap(subtitle, 128);
  const subtitleTop = y + 2;
  y += subtitleLines.length * 17 + 4;
  const legend = form === "lines" ? series : series.length === 1 ? (series[0].parts ?? []) : series;
  const legendItems: { label: string; color: string; x: number; row: number }[] = [];
  {
    let x = left;
    let row = 0;
    for (const item of legend) {
      const wide = 34 + item.label.length * 6.6;
      if (x + wide > W - right && x > left) {
        row += 1;
        x = left;
      }
      legendItems.push({ label: item.label, color: item.color, x, row });
      x += wide;
    }
  }
  const legendTop = y + 8;
  if (legendItems.length) y += 8 + (legendItems[legendItems.length - 1].row + 1) * 22;
  const plotTop = y + 14;
  const plotH = 260;
  const plotBottom = plotTop + plotH;
  const plotW = W - left - right;
  const slot = plotW / Math.max(1, periods.length);
  const stacked = (s: ChartSeries, i: number) => s.values[i] ?? 0;
  const max = Math.max(0, ...series.flatMap((s) => periods.map((_p, i) => stacked(s, i))));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const yOf = (v: number) => plotBottom - (v / top) * plotH;
  const xMid = (i: number) => left + slot * i + slot / 2;
  const labelled = periods.map((p) => (width === 10 ? p % 50 === 0 : true));
  const partialAt = new Set<number>();
  if (partial.first) partialAt.add(0);
  if (partial.last) partialAt.add(periods.length - 1);
  const footnotes = [
    ...(partialAt.size
      ? [
          `* A partial period: ${[partial.first, partial.last].filter(Boolean).join(" and ")} only, the first and last years of registration.`,
        ]
      : []),
    ...notes,
  ].flatMap((note) => wrap(note, 140));
  const notesTop = plotBottom + 46;
  const H = notesTop + footnotes.length * 16 + 26;
  const barW = Math.min(24, slot * 0.72);
  const tipData = hover !== null ? tip(hover) : null;
  const tipLeft = hover !== null ? Math.max(14, Math.min(86, (xMid(hover) / W) * 100)) : 0;

  const save = (kind: "svg" | "png") => {
    const svg = svgRef.current;
    if (!svg) return;
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.querySelectorAll('[data-export="no"]').forEach((n) => n.remove());
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(W));
    clone.setAttribute("height", String(H));
    const text = new XMLSerializer().serializeToString(clone);
    if (kind === "svg") {
      saveFile(`${fileName}.svg`, new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
      return;
    }
    const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = W * 2;
      canvas.height = H * 2;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(2, 2);
      context.fillStyle = SURFACE;
      context.fillRect(0, 0, W, H);
      context.drawImage(image, 0, 0, W, H);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => blob && saveFile(`${fileName}.png`, blob), "image/png");
    };
    image.src = url;
  };

  return (
    <figure className="find-figure">
      <div className="find-figure-frame">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${title}. ${subtitle}`}
          fontFamily={FONT}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            {[...SERIES, OTHERS].map((color, k) => (
              <pattern key={k} id={`find-hatch-${k}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" fill={SURFACE} />
                <line x1="1" y1="0" x2="1" y2="6" stroke={color} strokeWidth="2.4" />
              </pattern>
            ))}
          </defs>
          <rect width={W} height={H} fill={SURFACE} />
          {titleLines.map((line, i) => (
            <text key={i} x={left - 48} y={titleTop + i * 20} fontSize="15" fontWeight="600" fill={INK}>
              {line}
            </text>
          ))}
          {subtitleLines.map((line, i) => (
            <text key={i} x={left - 48} y={subtitleTop + i * 17} fontSize="12.5" fill={INK_2}>
              {line}
            </text>
          ))}
          {legendItems.map((item) => (
            <g key={item.label} transform={`translate(${item.x}, ${legendTop + item.row * 22})`}>
              {form === "lines" ? (
                <line x1="0" y1="0" x2="18" y2="0" stroke={item.color} strokeWidth="2" strokeLinecap="round" />
              ) : (
                <rect x="2" y="-6" width="12" height="12" rx="2" fill={item.color} />
              )}
              <text x="26" y="4" fontSize="12" fill={INK}>
                {item.label}
              </text>
            </g>
          ))}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={W - right} y1={yOf(t)} y2={yOf(t)} stroke={t === 0 ? AXIS : GRID} strokeWidth="1" />
              <text x={left - 8} y={yOf(t) + 4} fontSize="11" fill={MUTED} textAnchor="end" style={{ fontVariantNumeric: "tabular-nums" }}>
                {percent ? `${t}%` : t.toLocaleString()}
              </text>
            </g>
          ))}
          <text
            x={16}
            y={(plotTop + plotBottom) / 2}
            fontSize="11.5"
            fill={INK_2}
            textAnchor="middle"
            transform={`rotate(-90 16 ${(plotTop + plotBottom) / 2})`}
          >
            {yTitle}
          </text>
          {hover !== null && (
            <line data-export="no" x1={xMid(hover)} x2={xMid(hover)} y1={plotTop} y2={plotBottom} stroke={AXIS} strokeWidth="1" />
          )}
          {form === "bars"
            ? series.slice(0, 1).flatMap((s, k) =>
                periods.map((_p, i) => {
                  const x = xMid(i) - barW / 2;
                  const pieces = s.parts ?? [{ label: s.label, color: s.color, values: s.values }];
                  let baseY = plotBottom;
                  const drawn = pieces.filter((piece) => (piece.values[i] ?? 0) > 0);
                  return (
                    <g key={`${k}-${i}`}>
                      {drawn.map((piece, n) => {
                        const h = ((piece.values[i] ?? 0) / top) * plotH;
                        const gap = n > 0 ? 2 : 0;
                        const yTop = baseY - h;
                        const path = barPath(x, yTop, barW, Math.max(0, h - gap), n === drawn.length - 1);
                        baseY = yTop;
                        const colorIndex = Math.max(0, SERIES.indexOf(piece.color));
                        return (
                          <path
                            key={n}
                            d={path}
                            fill={s.low[i] ? `url(#find-hatch-${piece.color === OTHERS ? SERIES.length : colorIndex})` : piece.color}
                          />
                        );
                      })}
                    </g>
                  );
                }),
              )
            : series.map((s, k) => {
                const runs: string[] = [];
                let run = "";
                s.values.forEach((v, i) => {
                  if (v === null) {
                    if (run) runs.push(run);
                    run = "";
                  } else run += `${run ? "L" : "M"}${xMid(i)},${yOf(v)}`;
                });
                if (run) runs.push(run);
                return (
                  <g key={k}>
                    {runs.map((d, n) => (
                      <path key={n} d={d} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                    ))}
                    {s.values.map((v, i) =>
                      v === null ? null : s.low[i] ? (
                        <circle key={i} cx={xMid(i)} cy={yOf(v)} r="4" fill={SURFACE} stroke={s.color} strokeWidth="2" />
                      ) : (
                        <circle key={i} cx={xMid(i)} cy={yOf(v)} r="4" fill={s.color} stroke={SURFACE} strokeWidth="2" />
                      ),
                    )}
                  </g>
                );
              })}
          {periods.map((p, i) =>
            labelled[i] ? (
              <text key={p} x={xMid(i)} y={plotBottom + 18} fontSize="11" fill={MUTED} textAnchor="middle">
                {p}
              </text>
            ) : null,
          )}
          {[...partialAt].map((i) => (
            <text key={`partial-${i}`} x={xMid(i)} y={plotBottom + 32} fontSize="12" fill={INK_2} textAnchor="middle">
              *
            </text>
          ))}
          {footnotes.map((line, i) => (
            <text key={i} x={left - 48} y={notesTop + i * 16} fontSize="11.5" fill={INK_2}>
              {line}
            </text>
          ))}
          <text x={left - 48} y={H - 12} fontSize="11" fill={MUTED}>
            {source}
          </text>
          <g data-export="no">
            {periods.map((p, i) => (
              <rect
                key={p}
                x={left + slot * i}
                y={plotTop}
                width={slot}
                height={plotH + 24}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            ))}
          </g>
        </svg>
        {tipData && (
          <div className="ribbon-tip find-tip" style={{ left: `${tipLeft}%` }}>
            <span className="ribbon-tip-decade">{tipData.title}</span>
            {tipData.rows.map((row, k) => (
              <span key={k} className="ribbon-tip-row">
                <span className="find-tip-key" style={{ background: row.color }} />
                <strong>{row.value}</strong> {row.label}
                {row.note && <span className="muted"> · {row.note}</span>}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="an-toolbar find-figure-tools">
        <button type="button" className="pill-button" onClick={() => save("svg")}>
          Save as SVG
        </button>
        <button type="button" className="pill-button" onClick={() => save("png")}>
          Save as PNG
        </button>
      </div>
    </figure>
  );
}

// ---- Over time and Compare --------------------------------------------------------

/** The base a share is of, in words: step 0 is all contracts, step k the contracts
 * that meet the question's first k conditions. */
function baseWords(labels: string[], k: number): string {
  return k === 0 ? "all contracts" : `contracts ${labels.slice(1, k + 1).join(", ")}`;
}

/** A chip's words, from the registry and the stored names, for the Compare menu. */
function chipWords(entry: FindKind, condition: Condition, names: Record<string, string>): string {
  if (entry.choices) {
    const choice = entry.choices.find((c) => c.key === condition.choice);
    return (condition.not ? choice?.reading_not : choice?.reading) ?? entry.menu;
  }
  const values = (condition.values ?? []).map((v) =>
    v === null ? (entry.none ?? "") : entry.values === "word" ? `“${v}”` : (names[`${entry.values}:${v}`] ?? String(v)),
  );
  return `${condition.not ? entry.label_not : entry.label} ${values.join(" or ")}`;
}

export function TimeView({
  compare,
  spec,
  specKey,
  state,
  registry,
  kinds,
  groupings,
  names,
  view,
  setView,
  sentence,
  link,
  onName,
}: {
  compare: boolean;
  spec: FindSpec;
  specKey: string;
  state: FindState;
  registry: FindRegistry;
  kinds: Kinds;
  groupings: FindGroup[];
  names: Record<string, string>;
  view: FindView;
  setView: (next: Partial<FindView>) => void;
  sentence: string;
  link: string;
  onName: (key: string, name: string) => void;
}) {
  const splits = groupings.filter((g) => g.key !== "decade" && g.key !== "half");
  const split = compare && splits.some((g) => g.key === view.split) ? view.split : "";
  // the same question with one chip changed, each as the server will read it
  const beside = useMemo(() => {
    if (!compare || split) return [];
    return view.beside.flatMap((b, index) => {
      const target = readTarget(b.target);
      if (!target) return [];
      const current =
        target.level === "contract" ? state.contract[target.index] : state.partners[target.line]?.conditions[target.index];
      if (!current || isYears(current)) return [];
      const table = target.level === "contract" ? kinds.contract : kinds.partner;
      const condition = conditionOf(b.token, table);
      if (!condition || condition.kind !== current.kind) return [];
      return [{ index, target, condition, spec: toSpec(complete(replaceAt(state, target, condition))) }];
    });
  }, [compare, split, view.beside, state, kinds]);
  const requestKey = JSON.stringify([specKey, view.period, split, beside.map((b) => b.spec)]);
  const { data, error, stale } = useViewData<FindTimeResult>(requestKey, (key) => {
    const [question, period, by, others] = JSON.parse(key);
    return findTime(JSON.parse(question), period, by, others);
  });

  useEffect(() => {
    for (const [kind, byValue] of Object.entries(data?.names ?? {})) {
      for (const [value, name] of Object.entries(byValue)) onName(`${kind}:${value}`, name);
    }
  }, [data, onName]);

  const chips = useMemo(() => {
    const out: { target: string; words: string; entry: FindKind; condition: Condition }[] = [];
    state.contract.forEach((c, i) => {
      const entry = registry.contract_conditions.find((k) => k.kind === c.kind);
      if (entry && !isYears(c)) out.push({ target: targetOf({ level: "contract", index: i }), words: chipWords(entry, c, names), entry, condition: c });
    });
    state.partners.forEach((line, l) =>
      line.conditions.forEach((c, k) => {
        const entry = registry.partner_conditions.find((p) => p.kind === c.kind);
        if (entry) out.push({ target: targetOf({ level: "partner", line: l, index: k }), words: chipWords(entry, c, names), entry, condition: c });
      }),
    );
    return out;
  }, [state, registry, names]);

  const baseLabels = data?.series[0]?.bases.map((b) => b.label) ?? [];
  const base = view.show === "share" ? Math.min(view.base, Math.max(0, baseLabels.length - 1)) : 0;
  const shareOn = view.show === "share" && baseLabels.length > 0;
  const unitWord = view.period === "half" ? "half-century" : "decade";

  const controls = (
    <div className="find-view-controls">
      <label>
        Per{" "}
        <select value={view.period} onChange={(e) => setView({ period: e.target.value as FindView["period"] })}>
          <option value="decade">decade</option>
          <option value="half">half-century</option>
        </select>
      </label>
      <label>
        Show{" "}
        <select
          value={shareOn ? `share-${base}` : "count"}
          onChange={(e) =>
            e.target.value === "count"
              ? setView({ show: "count", base: 0 })
              : setView({ show: "share", base: Number(e.target.value.slice(6)) })
          }
        >
          <option value="count">the number of contracts</option>
          {baseLabels.map((_l, k) => (
            <option key={k} value={`share-${k}`}>
              the share of {baseWords(baseLabels, k)}
            </option>
          ))}
        </select>
      </label>
      {compare && (
        <label>
          Split by{" "}
          <select value={split} onChange={(e) => setView({ split: e.target.value })}>
            <option value="">nothing: set questions beside it instead</option>
            {splits.map((g) => (
              <option key={g.key} value={g.key}>
                {g.label}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );

  const besideControls = compare && !split && (
    <div className="find-beside">
      {view.beside.map((b, index) => {
        const chip = chips.find((c) => c.target === b.target);
        const target = readTarget(b.target);
        const table = target?.level === "contract" ? kinds.contract : kinds.partner;
        const condition = conditionOf(b.token, table);
        if (!chip || !target || !condition) return null;
        const set = (next: Condition | null) =>
          setView({
            beside: next
              ? view.beside.map((x, j) => (j === index ? { ...x, token: conditionToken(next, table) } : x))
              : view.beside.filter((_x, j) => j !== index),
          });
        return (
          <div key={index} className="find-row">
            <span className="find-staged-title">Beside it, the same question with “{chip.words}” as</span>
            <ConditionChip
              entry={chip.entry}
              condition={condition}
              state={state}
              target={target}
              names={names}
              partner={target.level === "partner"}
              onChange={(next) => set(next)}
              onRemove={() => set(null)}
              onName={onName}
            />
          </div>
        );
      })}
      {view.beside.length < MAX_BESIDE && chips.length > 0 && (
        <AddMenu
          label="+ set beside it the same question with one condition changed"
          sections={[
            {
              title: "Change",
              items: chips.map((chip) => ({
                key: chip.target,
                label: chip.words,
                onPick: () =>
                  setView({
                    beside: [
                      ...view.beside,
                      {
                        target: chip.target,
                        token: conditionToken(chip.condition, chip.target.startsWith("c") ? kinds.contract : kinds.partner),
                      },
                    ],
                  }),
              })),
            },
          ]}
        />
      )}
    </div>
  );

  if (error || !data) {
    return (
      <div className="find-view">
        {controls}
        {besideControls}
        {error ? <p className="error-text">{error}</p> : <p className="muted">Loading…</p>}
      </div>
    );
  }

  const valuesOf = (counts: number[], bases: { counts: number[] }[]) =>
    shareOn ? counts.map((n, i) => share(n, bases[base]?.counts[i] ?? 0)) : counts;
  const lowOf = (bases: { counts: number[] }[]) =>
    data.periods.map((_p, i) => shareOn && (bases[base]?.counts[i] ?? 0) > 0 && (bases[base]?.counts[i] ?? 0) < LOW_BASE);
  const series: ChartSeries[] = data.series.map((s, k) => ({
    label: s.label,
    color: s.label.startsWith("all others") ? OTHERS : SERIES[k % SERIES.length],
    values: valuesOf(s.counts, s.bases),
    low: lowOf(s.bases),
    parts: s.parts?.map((part, j) => ({ label: part.label, color: SERIES[j], values: valuesOf(part.counts, s.bases) })),
  }));
  const baseText = shareOn ? baseWords(baseLabels, base) : "";
  const subtitle = [
    shareOn
      ? `Share of ${baseText}, per ${unitWord} of registration, in percent.`
      : `Contracts per ${unitWord} of registration.`,
    split ? `Split by ${groupings.find((g) => g.key === split)?.label}.` : "",
    !split && data.series.length > 1 ? "Beside it, the same question with one condition changed." : "",
    shareOn && series.some((s) => s.low.some((low, i) => low && (compare ? s.values[i] !== null : (s.values[i] ?? 0) > 0)))
      ? `${compare ? "An open circle" : "A hatched bar"}: the base holds fewer than ${LOW_BASE} contracts in that ${unitWord}.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");
  const undated = data.series.filter((s) => s.undated > 0);
  const notes = [
    ...undated.map((s) =>
      data.series.length > 1
        ? `${s.label}: ${contractsWord(s.undated)} with no registration date, not on the axis.`
        : `${contractsWord(s.undated)} with no registration date ${s.undated === 1 ? "is" : "are"} not on the axis.`,
    ),
    ...data.notes,
  ];
  const today = new Date();
  const source = `FlorAcco review platform · ${window.location.origin}${link} · ${today.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  })}`;
  const tip = (i: number) => {
    const rows: TipRow[] = [];
    data.series.forEach((s, k) => {
      const baseCount = s.bases[base]?.counts[i] ?? 0;
      const pieces = s.parts ?? [{ label: data.series.length > 1 ? s.label : "contracts", counts: s.counts }];
      pieces.forEach((piece, j) => {
        const n = piece.counts[i];
        rows.push({
          color: s.parts ? SERIES[j] : series[k].color,
          value: shareOn ? percentText(share(n, baseCount)) : n.toLocaleString(),
          label: shareOn ? `${piece.label}: ${n.toLocaleString()} of ${baseCount.toLocaleString()}` : piece.label,
          note: shareOn && baseCount > 0 && baseCount < LOW_BASE ? `fewer than ${LOW_BASE} in the base` : undefined,
        });
      });
      if (s.parts && shareOn) rows.push({ color: "transparent", value: percentText(share(s.counts[i], baseCount)), label: "together" });
    });
    return { title: periodLabel(data.periods[i], data.width, data.partial, data.periods), rows };
  };

  return (
    <div className={stale ? "find-view is-updating" : "find-view"}>
      {controls}
      {besideControls}
      {compare && !split && data.series.length === 1 && (
        <p className="muted an-note">Split the set by a grouping, or set beside it the same question with one condition changed.</p>
      )}
      <TimeChart
        title={!split && data.series.length > 1 ? `${sentence}; beside it: ${data.series.slice(1).map((x) => x.label).join("; ")}` : sentence}
        subtitle={subtitle}
        yTitle={shareOn ? `% of ${baseText}` : "contracts"}
        periods={data.periods}
        width={data.width}
        partial={data.partial}
        series={series}
        form={compare ? "lines" : "bars"}
        percent={shareOn}
        notes={notes}
        source={source}
        tip={tip}
        fileName={compare ? "find-compare" : "find-over-time"}
      />
      <details className="find-figures">
        <summary>The figures as a table</summary>
        <div className="an-table-wrap">
          <table className="an-table">
            <thead>
              <tr>
                <th>{view.period === "half" ? "Half-century" : "Decade"}</th>
                {data.series.flatMap((s) => [
                  ...(s.parts ?? []).map((part) => <th key={`${s.label}-${part.label}`} className="num">{part.label}</th>),
                  <th key={s.label} className="num">
                    {data.series.length > 1 ? s.label : "Contracts"}
                  </th>,
                  ...(shareOn
                    ? [
                        <th key={`${s.label}-base`} className="num">
                          Base: {data.series.length > 1 ? `${s.bases[base]?.label}` : baseText}
                        </th>,
                        <th key={`${s.label}-share`} className="num">
                          Share
                        </th>,
                      ]
                    : []),
                ])}
              </tr>
            </thead>
            <tbody>
              {data.periods.map((p, i) => (
                <tr key={p}>
                  <td>{periodLabel(p, data.width, data.partial, data.periods)}</td>
                  {data.series.flatMap((s) => {
                    const baseCount = s.bases[base]?.counts[i] ?? 0;
                    return [
                      ...(s.parts ?? []).map((part) => (
                        <td key={`${s.label}-${part.label}`} className="num">
                          {part.counts[i].toLocaleString()}
                        </td>
                      )),
                      <td key={s.label} className="num">
                        {s.counts[i].toLocaleString()}
                      </td>,
                      ...(shareOn
                        ? [
                            <td key={`${s.label}-base`} className="num">
                              {baseCount.toLocaleString()}
                            </td>,
                            <td key={`${s.label}-share`} className="num">
                              {percentText(share(s.counts[i], baseCount))}
                              {baseCount > 0 && baseCount < LOW_BASE ? " *" : ""}
                            </td>,
                          ]
                        : []),
                    ];
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
