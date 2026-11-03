import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { NetworkFirm, NetworkFirmAct, NetworkFirmCell, NetworkFirmLater, NetworkFirmRow } from "../types";
import { labelWidth } from "../utils/networkLayout";
import { duplicateNames, nodeLabel, sameSelect, type NetworkSelect } from "../utils/networkSpec";
import { jewishWords } from "../utils/networkWords";
import { roleLabel } from "../utils/roles";
import { appearanceNotes, roleWords } from "./NetworkFigure";

/* The firm view, step 3 (docs/network/step3_contract.md §4; scope §3.11): the
 * registrations that carry one stored firm name as columns in date order, the
 * entries on them as rows, a mark in each cell for the role on the stake, the
 * later acts of each registration as ticks under its head, and between
 * consecutive dated columns the change in words. A faint line joins a row's
 * first and last cell, so a gap in the middle reads as "absent on that
 * registration". The drawing is laid out in the stage's own pixels for the
 * measured frame (a ResizeObserver, as the circle); columns at an even pitch
 * whatever the dates, rows at ROW_PITCH, shrunk to ROW_PITCH_LEAST when the
 * frame is short and then the figure grows and the stage scrolls.
 *
 * The reading card of step 2c in the same band form at the stage's top: at rest
 * the firm's sentence and answer; a cell under the pointer, a column head, a
 * tick or a row label fills it; a side-column row (`highlight`) and the
 * selection (while the pointer is over nothing) do the same. A click on a cell
 * or a row label selects the entry, on a head the act; the page keeps the
 * selection and fills the side column.
 *
 * Still (docs/network/explainer_contract.md §1): with `still` the figure is a
 * picture in the explainer's prose. No reading card and no band reserved for
 * it, no hover, no click, no selection, no hit targets; the marks, ticks,
 * change words and labels as drawn. The frame is fixed: the caller may give
 * the height; without one the frame is the drawing's own height, rows at
 * ROW_PITCH, so nothing shrinks or scrolls. The label column may take a larger
 * share of the width (LABEL_SHARE_STILL): no card gives a cut name in full. */

const ROW_PITCH = 22;
const ROW_PITCH_LEAST = 18;
/** the column of row labels: the longest label as measured after the first
 * paint (estimated before it), within a share of the width; a name that would
 * still pass the column is cut with an ellipsis */
const LABEL_PAD = 14;
const LABEL_SHARE = 0.42;
const LABEL_SHARE_STILL = 0.5;
const LABEL_LEAST = 120;
const LABEL_ESTIMATE = 1.07;
const RIGHT_PAD = 18;
const TOP_PAD = 14;
const BOTTOM_PAD = 12;
/** the heads: the year row, then the act number row */
const HEAD_YEAR_H = 15;
const HEAD_ACT_H = 15;
/** the strip under the heads: the ticks row, then the change words */
const TICK_ROW_H = 16;
const TICK_H = 6;
const TICK_GAP = 5;
const WORDS_ROW_H = 13;
const WORDS_CHAR_W = 5.6;
const CELL_R = 4.5;
const ENTRY_LABEL_W = 5.3;

/** The card's band, as the circle reserves it: fixed per width, never per content. */
const CARD_EDGE = 12;
const CARD_FRAME = 2 + 8 + 9 + 21 + 3;
const CARD_ROW = 18.2;
const CARD_CHARS = 280;
const CARD_CHAR_W = 6.2;
const CARD_PAD_X = 22;

function cardBand(width: number): number {
  const rows = Math.max(1, Math.ceil(CARD_CHARS / ((width - 2 * CARD_EDGE - CARD_PAD_X) / CARD_CHAR_W)));
  return Math.round(2 * CARD_EDGE + CARD_FRAME + rows * CARD_ROW);
}

type Hover =
  | { kind: "cell"; row: number; act: number }
  | { kind: "row"; row: number }
  | { kind: "head"; act: number }
  | { kind: "tick"; act: number; later: number };
type Card = { state: "rest" | "entry" | "act" | "tick" | "pinned"; head: ReactNode; lines: string[] };

const num = (n: number) => n.toLocaleString("en-US");

function undated(date: string): boolean {
  return !date || date.startsWith("0000");
}

/** "Renewal", "Termination", "Later act": the stored kind with a capital. */
function kindWords(later: NetworkFirmLater): string {
  const kind = later.kind.trim() || "later act";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** "Act 3694, 1659-09-04 · Firenze · negozio di arte di seta · 6 signers" */
export function firmActHead(act: NetworkFirmAct): string {
  const places = act.places.length ? `${act.places.join("; ")}${act.places_inferred ? " (marked as inferred)" : ""}` : "not recorded";
  const activity = act.activity?.trim() || "not recorded";
  return `Act ${act.id}, ${undated(act.date) ? "no registration date" : act.date} · ${places} · ${activity} · ${num(act.signers)} ${act.signers === 1 ? "signer" : "signers"}`;
}

/** "general partner on act 3694 (1659), 2,000 scudi, through a proxy, on a joint stake; Jewish, stated in the act." */
export function firmCellLine(cell: NetworkFirmCell, act: NetworkFirmAct, rowId: number): string {
  const role = cell.role ? roleWords(cell.role) : "no role recorded";
  const year = act.year ? String(act.year) : "no registration date";
  const parts = [`${role} on act ${act.id} (${year})`];
  if (cell.cash !== null) parts.push(`${num(cell.cash)}${act.currency ? ` ${act.currency}` : ""}`);
  if (cell.non_cash) parts.push(`non-cash: ${cell.non_cash}`);
  parts.push(...appearanceNotes({ ...cell, person: rowId, act: act.id }));
  const jewish = jewishWords(cell);
  return `${parts.join(", ")}${jewish ? `; ${jewish}` : ""}.`;
}

/** "3 acts under this firm name, 1669–1690; general partner on all 3." */
function rowLine(row: NetworkFirmRow, acts: NetworkFirmAct[]): string {
  const cells = acts.filter((act) => row.cells[String(act.id)]);
  const total = cells.length;
  let gp = 0;
  let lp = 0;
  let none = 0;
  for (const act of cells) {
    const role = row.cells[String(act.id)].role;
    if (role === "gp" || role === "both") gp += 1;
    if (role === "lp" || role === "both") lp += 1;
    if (!role) none += 1;
  }
  const pairs: [string, number][] = [
    [roleLabel("lp").toLowerCase(), lp],
    [roleLabel("gp").toLowerCase(), gp],
    ["no role recorded", none],
  ];
  const counted = pairs.filter((pair) => pair[1] > 0).sort((p, q) => q[1] - p[1]);
  const roles =
    counted.length === 0
      ? "no role recorded"
      : total === 1
        ? counted[0][0]
        : counted.length === 1 && counted[0][1] >= total
          ? `${counted[0][0]} on all ${num(total)}`
          : counted.map(([word, n]) => `${word} on ${num(n)}`).join(", ");
  const years = cells.map((act) => act.year).filter((y): y is number => typeof y === "number" && y > 0);
  const span = years.length ? (Math.min(...years) === Math.max(...years) ? String(years[0]) : `${Math.min(...years)}–${Math.max(...years)}`) : "";
  const undatedCount = total - years.length;
  if (total === 1) return `One act under this firm name, ${span || "with no registration date"}; ${roles}.`;
  const rest = undatedCount === 0 ? "" : span ? `; ${num(undatedCount)} with no registration date` : "all with no registration date";
  return `${num(total)} acts under this firm name, ${span}${rest}; ${roles}.`;
}

/** "4 stayed · 1 left · 1 joined" on one line when the pitch holds it, else three. */
function changeWords(change: { stayed: number; left: number; joined: number }, pitch: number): string[] {
  const parts = [`${num(change.stayed)} stayed`, `${num(change.left)} left`, `${num(change.joined)} joined`];
  const one = parts.join(" · ");
  return one.length * WORDS_CHAR_W <= pitch - 10 ? [one] : parts;
}

const NOOP = () => {};

export default function FirmFigure({
  data,
  selected: selectedProp,
  highlight: highlightProp,
  still = false,
  height,
  onSelect = NOOP,
  onHover = NOOP,
}: {
  data: NetworkFirm;
  selected: NetworkSelect | null;
  /** an entry lit from a side-column row, as hover lights it */
  highlight: number | null;
  /** a picture: no card, no band, no hover, no click, no selection */
  still?: boolean;
  /** the stage's height in pixels while `still` is set; the drawing's own when left out */
  height?: number;
  /** null clears: the selected cell or head clicked again, or empty stage */
  onSelect?: (select: NetworkSelect | null) => void;
  /** the entry under the pointer, for the side column */
  onHover?: (id: number | null) => void;
}) {
  // A still figure has no selection and nothing lit from outside.
  const selected = still ? null : selectedProp;
  const highlight = still ? null : highlightProp;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });
  const [hover, setHover] = useState<Hover | null>(null);
  /** the widest row label as drawn, over its estimate: 1 until measured */
  const [labelScale, setLabelScale] = useState(1);

  // The scrolling box under the card's band, measured; the layout fits its
  // width and, when the rows allow, its height.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = (width: number, height: number) => {
      const w = Math.round(width);
      const h = Math.round(height);
      setFrame((old) => (old.width === w && old.height === h ? old : { width: w, height: h }));
    };
    measure(el.clientWidth, el.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) measure(box.width, box.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const band = !still && frame.width > 0 ? cardBand(frame.width) : 0;
  /** the height the layout fits: the measured box's; a still figure's is the
   * caller's, or none, so the drawing takes its own and never feeds back */
  const frameHeight = still ? (height ?? 0) : frame.height;
  const actsById = useMemo(() => new Map(data.acts.map((act) => [act.id, act])), [data]);
  const rowsById = useMemo(() => new Map(data.rows.map((row) => [row.id, row])), [data]);
  const duplicates = useMemo(() => duplicateNames(data.rows), [data]);
  const fullLabels = useMemo(() => new Map(data.rows.map((row) => [row.id, nodeLabel(row, duplicates)])), [data, duplicates]);
  const estimate = (label: string, id: number) => (labelWidth(label) + ENTRY_LABEL_W * (7 + String(id).length)) * LABEL_ESTIMATE * labelScale;
  /** the label column's width and the labels, cut where the column cannot hold them */
  const { labelW, labels } = useMemo(() => {
    const cap = Math.max(LABEL_LEAST, Math.round(frame.width * (still ? LABEL_SHARE_STILL : LABEL_SHARE)));
    const longest = Math.max(0, ...data.rows.map((row) => estimate(fullLabels.get(row.id) ?? row.name, row.id)));
    const labelW = Math.min(Math.round(longest) + LABEL_PAD, cap);
    const labels = new Map<number, string>();
    for (const row of data.rows) {
      let label = fullLabels.get(row.id) ?? row.name;
      while (label.length > 8 && estimate(label, row.id) + LABEL_PAD > cap) label = `${label.slice(0, -2).trimEnd()}…`;
      labels.set(row.id, label);
    }
    return { labelW, labels };
  }, [data, fullLabels, frame.width, labelScale, still]);
  const changeByFrom = useMemo(() => new Map(data.changes.map((change) => [change.from, change])), [data]);

  // The layout in pixels.
  const layout = useMemo(() => {
    if (frame.width <= 0 || !data.acts.length) return null;
    const n = data.acts.length;
    const pitch = (frame.width - labelW - RIGHT_PAD) / n;
    const x = (i: number) => labelW + pitch * (i + 0.5);
    const wordsRows = Math.max(1, ...data.changes.map((change) => changeWords(change, pitch).length));
    const headsTop = TOP_PAD;
    const ticksTop = headsTop + HEAD_YEAR_H + HEAD_ACT_H;
    const wordsTop = ticksTop + TICK_ROW_H;
    const rowsTop = wordsTop + wordsRows * WORDS_ROW_H + 6;
    const rows = data.rows.length;
    const room = frameHeight - rowsTop - BOTTOM_PAD;
    const rowPitch = rows * ROW_PITCH <= room || (still && !frameHeight) ? ROW_PITCH : Math.max(ROW_PITCH_LEAST, Math.floor(room / Math.max(1, rows)));
    const height = Math.max(frameHeight, rowsTop + rows * rowPitch + BOTTOM_PAD);
    const y = (r: number) => rowsTop + rowPitch * (r + 0.5);
    return { labelW, pitch, x, y, headsTop, ticksTop, wordsTop, wordsRows, rowsTop, rowPitch, height };
  }, [frame.width, frameHeight, still, data, labelW]);
  useEffect(() => setHover(null), [layout]);

  // The labels as drawn against their estimate: the widest decides the column
  // (one relayout after the first paint, then only when the rows change).
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !layout) return;
    let scale = 0;
    for (const text of el.querySelectorAll<SVGTextElement>("text.nw-firm-label")) {
      const id = Number(text.getAttribute("data-id"));
      const label = labels.get(id);
      if (!label || label.endsWith("…")) continue;
      const guess = estimate(label, id) / labelScale;
      if (guess > 0) scale = Math.max(scale, text.getBBox().width / guess);
    }
    if (scale > 0 && Math.abs(scale - labelScale) > 0.01) setLabelScale(scale);
  }, [data, layout === null]);

  const selectedPerson = selected?.kind === "person" && rowsById.has(selected.id) ? selected.id : null;
  const selectedAct = selected?.kind === "contract" && actsById.has(selected.id) ? selected.id : null;
  /** the row lit: under the pointer on the figure, or under it on a side-column row */
  const litRow = hover && hover.kind !== "head" && hover.kind !== "tick" ? hover.row : highlight !== null && rowsById.has(highlight) ? highlight : null;
  const litAct = hover?.kind === "head" || hover?.kind === "tick" ? hover.act : hover?.kind === "cell" ? hover.act : null;

  const entryHead = (row: NetworkFirmRow): ReactNode => (
    <>
      {labels.get(row.id)} <span className="nw-side-id">entry {row.id}</span>
    </>
  );
  const rowCard = (row: NetworkFirmRow, state: Card["state"]): Card => {
    const lines = [rowLine(row, data.acts)];
    if (row.woman) lines.push("Recorded as woman.");
    if (row.acts_total > data.acts.filter((act) => row.cells[String(act.id)]).length) lines.push(`On ${num(row.acts_total)} acts in the corpus.`);
    if (duplicates.has(row.name)) {
      const others = data.rows.filter((other) => other.name === row.name && other.id !== row.id).map((other) => `entry ${other.id}`);
      lines.push(`Same name as ${others.join(", ")}, as entered.`);
    }
    return { state, head: entryHead(row), lines };
  };
  const actCard = (act: NetworkFirmAct, state: Card["state"]): Card => ({ state, head: firmActHead(act), lines: [] });

  // What the card reads, first match: the cell, head, tick or row label under
  // the pointer; the side-column row under it; the selection; the firm.
  const card = ((): Card => {
    if (hover?.kind === "cell") {
      const row = rowsById.get(hover.row);
      const act = actsById.get(hover.act);
      const cell = row?.cells[String(hover.act)];
      if (row && act && cell) return { state: "entry", head: entryHead(row), lines: [firmCellLine(cell, act, row.id)] };
    }
    if (hover?.kind === "row") {
      const row = rowsById.get(hover.row);
      if (row) return rowCard(row, "entry");
    }
    if (hover?.kind === "head") {
      const act = actsById.get(hover.act);
      if (act) return actCard(act, "act");
    }
    if (hover?.kind === "tick") {
      const act = actsById.get(hover.act);
      const later = act?.later.find((item) => item.id === hover.later);
      if (act && later) {
        return { state: "tick", head: `${kindWords(later)}, ${undated(later.date) ? "no registration date" : later.date}, of act ${act.id}.`, lines: [] };
      }
    }
    if (highlight !== null && rowsById.has(highlight)) return rowCard(rowsById.get(highlight)!, "entry");
    if (selectedPerson !== null) return rowCard(rowsById.get(selectedPerson)!, "pinned");
    if (selectedAct !== null) return actCard(actsById.get(selectedAct)!, "pinned");
    return { state: "rest", head: data.sentence, lines: [data.answer] };
  })();

  const enter = (next: Hover) => {
    if (still) return;
    setHover(next);
    onHover(next.kind === "cell" || next.kind === "row" ? next.row : null);
  };
  const leave = () => {
    if (still) return;
    setHover(null);
    onHover(null);
  };
  const clickRow = (id: number) => {
    if (still) return;
    const next: NetworkSelect = { kind: "person", id };
    onSelect(sameSelect(selected, next) ? null : next);
  };
  const clickAct = (id: number) => {
    if (still) return;
    const next: NetworkSelect = { kind: "contract", id };
    onSelect(sameSelect(selected, next) ? null : next);
  };

  /** The mark of a cell: the dot for a general partner, the hollow ring for an
   * external investor, the dot in a ring for both, the hollow diamond through
   * heirs, the square for a non-person row; a faint dot with no role recorded. */
  const mark = (row: NetworkFirmRow, cell: NetworkFirmCell, cx: number, cy: number): ReactNode => {
    if (row.kind !== "person") return <rect className="nw-firm-mark is-square" x={cx - CELL_R} y={cy - CELL_R} width={2 * CELL_R} height={2 * CELL_R} />;
    if (cell.heirs_of) {
      const d = CELL_R * 1.35;
      return <polygon className="nw-firm-mark is-heirs" points={`${cx},${cy - d} ${cx + d},${cy} ${cx},${cy + d} ${cx - d},${cy}`} />;
    }
    if (cell.role === "both") {
      return (
        <>
          <circle className="nw-firm-mark is-ring" cx={cx} cy={cy} r={CELL_R + 1.8} />
          <circle className="nw-firm-mark is-dot" cx={cx} cy={cy} r={CELL_R - 1.2} />
        </>
      );
    }
    if (cell.role === "lp") return <circle className="nw-firm-mark is-ring" cx={cx} cy={cy} r={CELL_R} />;
    if (cell.role === "gp") return <circle className="nw-firm-mark is-dot" cx={cx} cy={cy} r={CELL_R} />;
    return <circle className="nw-firm-mark is-none" cx={cx} cy={cy} r={CELL_R - 1} />;
  };

  return (
    <div
      className={still ? "nw-stage nw-firm-stage is-still" : "nw-stage nw-firm-stage"}
      style={still && height ? { height } : undefined}
      onMouseLeave={leave}
    >
      {!still && (
        <div className="nw-firm-band" style={{ height: band }}>
          <div className="nw-card nw-firm-card" data-state={card.state}>
            <p className="nw-card-head">{card.head}</p>
            {card.lines.length > 0 && <p className="nw-card-lines">{card.lines.join(" ")}</p>}
          </div>
        </div>
      )}
      <div ref={scrollRef} className="nw-firm-scroll">
        {layout && (
          <svg
            className="nw-firm-svg"
            viewBox={`0 0 ${frame.width} ${layout.height}`}
            width={frame.width}
            height={layout.height}
            role="img"
            aria-label={data.sentence}
            data-columns={data.acts.length}
            data-rows={data.rows.length}
            data-pitch={layout.pitch.toFixed(1)}
            data-row-pitch={layout.rowPitch}
            data-band={band}
            data-still={still ? "1" : undefined}
          >
            <rect className="nw-stage-bg" x={0} y={0} width={frame.width} height={layout.height} onClick={still ? undefined : () => onSelect(null)} />
            {/* the lit column and row, as bands under everything */}
            {data.acts.map((act, i) => {
              const lit = litAct === act.id;
              const chosen = selectedAct === act.id;
              if (!lit && !chosen) return null;
              return (
                <rect
                  key={act.id}
                  className={["nw-firm-band-col", lit ? "is-lit" : "", chosen ? "is-selected" : ""].filter(Boolean).join(" ")}
                  x={layout.x(i) - layout.pitch / 2 + 1}
                  y={layout.headsTop - 4}
                  width={layout.pitch - 2}
                  height={layout.height - layout.headsTop - 2}
                  rx={4}
                />
              );
            })}
            {data.rows.map((row, r) => {
              const lit = litRow === row.id;
              const chosen = selectedPerson === row.id;
              if (!lit && !chosen) return null;
              return (
                <rect
                  key={row.id}
                  className={["nw-firm-band-row", lit ? "is-lit" : "", chosen ? "is-selected" : ""].filter(Boolean).join(" ")}
                  x={4}
                  y={layout.y(r) - layout.rowPitch / 2}
                  width={frame.width - 8}
                  height={layout.rowPitch}
                  rx={4}
                />
              );
            })}
            {/* the heads: the year, the act number; the ticks and the change words under them */}
            <g>
              {data.acts.map((act, i) => {
                const cx = layout.x(i);
                const change = changeByFrom.get(act.id);
                const nextIndex = change ? data.acts.findIndex((other) => other.id === change.to) : -1;
                const words = change && nextIndex >= 0 ? changeWords(change, layout.pitch) : [];
                const tickTotal = act.later.length * TICK_GAP;
                return (
                  <g key={act.id}>
                    <g
                      className={["nw-firm-head", selectedAct === act.id ? "is-selected" : "", litAct === act.id ? "is-lit" : ""].filter(Boolean).join(" ")}
                      data-act={act.id}
                      onMouseEnter={() => enter({ kind: "head", act: act.id })}
                      onMouseLeave={leave}
                      onClick={() => clickAct(act.id)}
                    >
                      {!still && <rect className="nw-firm-hit" x={cx - layout.pitch / 2} y={layout.headsTop - 4} width={layout.pitch} height={HEAD_YEAR_H + HEAD_ACT_H + 4} />}
                      <text className={act.year ? "nw-firm-year" : "nw-firm-year is-undated"} x={cx} y={layout.headsTop + 10} textAnchor="middle">
                        {act.year ? act.year : "no date"}
                      </text>
                      <text className="nw-firm-act" x={cx} y={layout.headsTop + HEAD_YEAR_H + 10} textAnchor="middle">
                        {act.id}
                      </text>
                    </g>
                    {act.later.map((later, k) => {
                      const tx = cx - tickTotal / 2 + TICK_GAP * (k + 0.5);
                      const ty = layout.ticksTop + 3;
                      const lit = hover?.kind === "tick" && hover.act === act.id && hover.later === later.id;
                      return (
                        <g
                          key={later.id}
                          className={lit ? "nw-firm-tick-group is-lit" : "nw-firm-tick-group"}
                          data-later={later.id}
                          onMouseEnter={() => enter({ kind: "tick", act: act.id, later: later.id })}
                          onMouseLeave={leave}
                        >
                          <line className="nw-firm-tick" x1={tx} y1={ty} x2={tx} y2={ty + TICK_H} />
                          {!still && <rect className="nw-firm-hit" x={tx - TICK_GAP / 2} y={ty - 3} width={TICK_GAP} height={TICK_H + 6} />}
                        </g>
                      );
                    })}
                    {words.map((line, k) => (
                      <text key={k} className="nw-firm-change" x={(cx + layout.x(nextIndex)) / 2} y={layout.wordsTop + 9 + WORDS_ROW_H * k} textAnchor="middle">
                        {line}
                      </text>
                    ))}
                  </g>
                );
              })}
            </g>
            {/* the rows: the label, the span line, the cells */}
            <g>
              {data.rows.map((row, r) => {
                const cy = layout.y(r);
                const present = data.acts.map((act, i) => ({ act, i, cell: row.cells[String(act.id)] })).filter((item) => item.cell);
                const first = present[0];
                const last = present[present.length - 1];
                const lit = litRow === row.id;
                const chosen = selectedPerson === row.id;
                return (
                  <g key={row.id} className={["nw-firm-row", lit ? "is-lit" : "", chosen ? "is-selected" : ""].filter(Boolean).join(" ")} data-id={row.id}>
                    <g
                      className="nw-firm-label-group"
                      onMouseEnter={() => enter({ kind: "row", row: row.id })}
                      onMouseLeave={leave}
                      onClick={() => clickRow(row.id)}
                    >
                      {!still && <rect className="nw-firm-hit" x={0} y={cy - layout.rowPitch / 2} width={layout.labelW} height={layout.rowPitch} />}
                      <text className="nw-firm-label" data-id={row.id} x={layout.labelW - LABEL_PAD} y={cy + 4} textAnchor="end">
                        {labels.get(row.id)} <tspan className="nw-firm-entry">entry {row.id}</tspan>
                      </text>
                    </g>
                    {first && last && first.i !== last.i && (
                      <line className="nw-firm-span" x1={layout.x(first.i)} y1={cy} x2={layout.x(last.i)} y2={cy} />
                    )}
                    {present.map(({ act, i, cell }) => {
                      const cx = layout.x(i);
                      const hovered = hover?.kind === "cell" && hover.row === row.id && hover.act === act.id;
                      return (
                        <g
                          key={act.id}
                          className={hovered ? "nw-firm-cell is-hover" : "nw-firm-cell"}
                          data-act={act.id}
                          onMouseEnter={() => enter({ kind: "cell", row: row.id, act: act.id })}
                          onMouseLeave={leave}
                          onClick={() => clickRow(row.id)}
                        >
                          {mark(row, cell, cx, cy)}
                          {!still && (
                            <rect
                              className="nw-firm-hit"
                              x={cx - Math.min(layout.pitch, 28) / 2}
                              y={cy - layout.rowPitch / 2}
                              width={Math.min(layout.pitch, 28)}
                              height={layout.rowPitch}
                            />
                          )}
                        </g>
                      );
                    })}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
      </div>
    </div>
  );
}

/** The marks and the tick, for a key under the figure in the circle's key style. */
export function FirmKey({ data }: { data: NetworkFirm }) {
  const cells = data.rows.flatMap((row) => Object.values(row.cells).map((cell) => ({ row, cell })));
  const both = cells.some(({ cell }) => cell.role === "both");
  const heirs = cells.some(({ cell }) => cell.heirs_of);
  const others = data.rows.some((row) => row.kind !== "person");
  return (
    <p className="nw-key nw-firm-key">
      <span>
        <i className="nw-key-dot" /> general partner
      </span>
      <span>
        <i className="nw-firm-key-ring" /> external investor (accomandante)
      </span>
      {both && (
        <span>
          <i className="nw-key-ring" /> both
        </span>
      )}
      {heirs && (
        <span>
          <i className="nw-key-diamond" /> through heirs
        </span>
      )}
      {others && (
        <span>
          <i className="nw-key-square" /> an institution, estate, shareholder group or placeholder
        </span>
      )}
      {data.counts.later > 0 && (
        <span>
          <i className="nw-firm-key-tick" /> a later act (termination, variation, balance, renewal)
        </span>
      )}
    </p>
  );
}
