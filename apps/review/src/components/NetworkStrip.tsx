import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { NetworkAct, NetworkCircle } from "../types";
import { stripAxis } from "../utils/networkLayout";
import { YEAR_MAX, YEAR_MIN } from "../utils/networkSpec";

/* The time strip under the stage (docs/network/next_2026-11-01.md §4.2,
 * step2a_contract.md §4.1–4.2): the acts in view as bars per year on the house
 * decade axis (CareerSpanRibbon's decade-snapped axis of at least 60 years;
 * DistributionRibbon's bar form, 96 px tall), the centre's own acts in the dot
 * colour under the other acts in view in the ring colour, an act the centre is
 * on only through heirs marked by the hollow diamond above its bar, and the
 * undated acts as a labelled column at the right. Hover on a year: the dark
 * balloon and, through `onHoverYear`, the entries on that year's acts lit on
 * the figure. No click on a bar.
 *
 * The window: two handles dragged on the strip (pointer events, so touch too).
 * They stand at the axis ends with no window; a drag writes `[from, to]`
 * through `onYears` (null again when both handles are back at the ends). The
 * tint lies between them and a bar outside is drawn as an outline. The strip
 * never reads the mask: the figure and the rail do, from windowTies. */

const H = 96;
const PAD_X = 4;
const PLOT_TOP = 12;
const AXIS_Y = 70;
const PLOT_H = AXIS_Y - PLOT_TOP;
const LABEL_Y = 86;
/** the undated column with its label, when there is one */
const UNDATED_W = 132;
const UNDATED_BAR = 12;
const HANDLE_HIT = 14;
const DIAMOND = 4.5;
const TIP_EDGE = 4;

const num = (n: number) => n.toLocaleString("en-US");

type YearBin = { centre: number; other: number; heirs: number; acts: number[] };

function yearOf(act: NetworkAct): number | null {
  return act.year && act.year > 0 ? act.year : null;
}

export default function NetworkStrip({
  data,
  centreName,
  years,
  onYears,
  onHoverYear,
}: {
  data: NetworkCircle;
  /** the centre's name as the page writes it (nodeLabel) */
  centreName: string;
  years: [number, number] | null;
  onYears: (years: [number, number] | null) => void;
  /** the entries on the hovered year's acts, or null when the pointer leaves */
  onHoverYear: (ids: Set<number> | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  /** the hovered year, or "undated" for the column at the right */
  const [hover, setHover] = useState<number | "undated" | null>(null);
  const [drag, setDrag] = useState<"from" | "to" | null>(null);
  const [tipDx, setTipDx] = useState({ key: "", dx: 0 });

  // The strip's own width, measured; the drawing is in true pixels.
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const measure = (w: number) => setWidth((old) => (old === Math.round(w) ? old : Math.round(w)));
    measure(el.clientWidth);
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) measure(box.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // The acts in view by year: the centre's own (and how many of those through
  // heirs) under the others; the undated ones in their own bin.
  const bins = useMemo(() => {
    const centreActs = new Map<number, boolean>();
    for (const appearance of data.appearances) {
      if (appearance.person !== data.centre) continue;
      centreActs.set(appearance.act, (centreActs.get(appearance.act) ?? true) && appearance.heirs_of);
    }
    const byYear = new Map<number, YearBin>();
    const undated: YearBin = { centre: 0, other: 0, heirs: 0, acts: [] };
    for (const act of data.acts) {
      const year = yearOf(act);
      const bin = year === null ? undated : (byYear.get(year) ?? { centre: 0, other: 0, heirs: 0, acts: [] });
      if (year !== null) byYear.set(year, bin);
      bin.acts.push(act.id);
      if (centreActs.has(act.id)) {
        bin.centre += 1;
        if (centreActs.get(act.id)) bin.heirs += 1;
      } else {
        bin.other += 1;
      }
    }
    return { byYear, undated };
  }, [data]);
  const signersByAct = useMemo(() => {
    const map = new Map<number, number[]>();
    for (const appearance of data.appearances) (map.get(appearance.act) ?? map.set(appearance.act, []).get(appearance.act)!).push(appearance.person);
    return map;
  }, [data]);

  const hasUndated = bins.undated.acts.length > 0;
  // The axis spans the dated acts in view; a window lying outside it (one
  // carried in from another circle's address) extends it by whole decades, so
  // the handles always stand on the strip and a drag never moves the axis.
  const axis = useMemo(() => {
    const dated = [...bins.byYear.keys()];
    if (!dated.length && !years) return null;
    const own = dated.length ? stripAxis(Math.min(...dated), Math.max(...dated)) : stripAxis(years![0], years![1]);
    if (!years) return own;
    return {
      start: Math.min(own.start, Math.max(YEAR_MIN, Math.floor(years[0] / 10) * 10)),
      end: Math.max(own.end, Math.min(YEAR_MAX, Math.floor(years[1] / 10) * 10 + 10)),
    };
  }, [bins, years]);

  const plotX0 = PAD_X;
  const plotX1 = Math.max(plotX0 + 1, width - PAD_X - (hasUndated ? UNDATED_W : 0));
  const slot = axis ? (plotX1 - plotX0) / (axis.end - axis.start) : 0;
  const x = (year: number) => (axis ? plotX0 + (year - axis.start) * slot : plotX0);
  const barGap = Math.min(2, slot * 0.25);
  const barW = Math.max(1.5, slot - barGap);
  const max = Math.max(1, ...[...bins.byYear.values()].map((bin) => bin.centre + bin.other), bins.undated.centre + bins.undated.other);
  const barH = (n: number) => (n / max) * PLOT_H;
  const undatedX = plotX1 + UNDATED_W / 2;

  const from = years ? years[0] : axis ? axis.start : 0;
  const to = years ? years[1] : axis ? axis.end - 1 : 0;
  const inside = (year: number | null) => year === null || !years || (year >= years[0] && year <= years[1]);

  // Ticks every decade; labels every 50 years and at the axis ends.
  const ticks: number[] = [];
  if (axis) for (let year = axis.start; year <= axis.end; year += 10) ticks.push(year);
  const labelled = (year: number) => year % 50 === 0 || year === axis?.start || year === axis?.end;

  // The balloon's words for the hovered year or the undated column.
  const tipWords = (bin: YearBin, head: string): string => {
    const total = bin.centre + bin.other;
    const acts = (n: number) => (n === 1 ? "one act" : `${num(n)} acts`);
    let text: string;
    if (bin.other === 0) text = `${head}: ${acts(total)} of ${centreName}`;
    else if (bin.centre === 0) text = `${head}: ${acts(total)} in this circle`;
    else text = `${head}: ${acts(total)} in this circle, ${num(bin.centre)} of ${centreName}`;
    if (bin.heirs > 0) text += bin.centre === bin.heirs && bin.heirs === 1 ? ", through heirs" : `, ${num(bin.heirs)} through heirs`;
    return text;
  };
  const tip = (() => {
    if (hover === null || drag) return null;
    if (hover === "undated") {
      if (!hasUndated) return null;
      return { key: "u", x: undatedX, y: AXIS_Y - barH(bins.undated.centre + bins.undated.other) - 6, text: tipWords(bins.undated, "No registration date") };
    }
    const bin = bins.byYear.get(hover);
    if (!bin) return null;
    return { key: String(hover), x: x(hover) + slot / 2, y: AXIS_Y - barH(bin.centre + bin.other) - (bin.heirs ? 2 * DIAMOND + 4 : 0) - 6, text: tipWords(bin, String(hover)) };
  })();

  // The balloon stays inside the strip's width.
  useLayoutEffect(() => {
    const el = tipRef.current;
    const host = hostRef.current;
    if (!el || !host || !tip) return;
    const box = el.getBoundingClientRect();
    const frame = host.getBoundingClientRect();
    const current = tipDx.key === tip.key ? tipDx.dx : 0;
    let dx = current;
    if (box.left < frame.left + TIP_EDGE) dx += frame.left + TIP_EDGE - box.left;
    else if (box.right > frame.right - TIP_EDGE) dx -= box.right - (frame.right - TIP_EDGE);
    if (tipDx.key !== tip.key || Math.abs(dx - tipDx.dx) > 0.5) setTipDx({ key: tip.key, dx });
  });

  const enter = (year: number | "undated") => {
    if (drag) return;
    setHover(year);
    const acts = year === "undated" ? bins.undated.acts : (bins.byYear.get(year)?.acts ?? []);
    const ids = new Set<number>();
    for (const act of acts) for (const person of signersByAct.get(act) ?? []) ids.add(person);
    onHoverYear(ids);
  };
  const leave = () => {
    setHover(null);
    onHoverYear(null);
  };

  // The drag: the pointer's year, snapped, kept in order; both handles back at
  // the axis ends clear the window.
  const yearAt = (clientX: number): number => {
    const host = hostRef.current;
    if (!host || !axis) return from;
    const rel = clientX - host.getBoundingClientRect().left;
    return Math.round((rel - plotX0) / slot) + axis.start;
  };
  const startDrag = (handle: "from" | "to") => (event: ReactPointerEvent<SVGRectElement>) => {
    if (!axis) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag(handle);
    leave();
  };
  const moveDrag = (event: ReactPointerEvent<SVGRectElement>) => {
    if (!drag || !axis) return;
    const year = yearAt(event.clientX);
    let nextFrom = from;
    let nextTo = to;
    if (drag === "from") nextFrom = Math.max(axis.start, Math.min(to, year));
    else nextTo = Math.min(axis.end - 1, Math.max(from, year - 1));
    if (nextFrom === from && nextTo === to) return;
    onYears(nextFrom === axis.start && nextTo === axis.end - 1 ? null : [nextFrom, nextTo]);
  };
  const endDrag = (event: ReactPointerEvent<SVGRectElement>) => {
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDrag(null);
  };

  const bar = (key: string, bx: number, w: number, bin: YearBin, isInside: boolean) => {
    const hCentre = barH(bin.centre);
    const hOther = barH(bin.other);
    const top = AXIS_Y - hCentre - hOther;
    const cls = isInside ? "" : " is-outside";
    return (
      <g key={key}>
        {bin.centre > 0 && <rect className={`nw-strip-bar is-centre${cls}`} x={bx} y={AXIS_Y - hCentre} width={w} height={hCentre} />}
        {bin.other > 0 && <rect className={`nw-strip-bar is-other${cls}`} x={bx} y={top} width={w} height={hOther} />}
        {bin.heirs > 0 && (
          <polygon
            className={`nw-strip-heirs${cls}`}
            points={[
              `${bx + w / 2},${top - 2 - 2 * DIAMOND}`,
              `${bx + w / 2 + DIAMOND},${top - 2 - DIAMOND}`,
              `${bx + w / 2},${top - 2}`,
              `${bx + w / 2 - DIAMOND},${top - 2 - DIAMOND}`,
            ].join(" ")}
          />
        )}
      </g>
    );
  };

  const handle = (which: "from" | "to") => {
    if (!axis) return null;
    const hx = which === "from" ? x(from) : x(to + 1);
    const cls = years ? "nw-strip-handle is-set" : "nw-strip-handle";
    return (
      <g className={cls}>
        <line className="nw-strip-handle-line" x1={hx} y1={PLOT_TOP} x2={hx} y2={AXIS_Y} />
        <rect className="nw-strip-grip" x={hx - 4} y={AXIS_Y - 8} width={8} height={16} rx={2} />
        <rect
          className="nw-strip-handle-hit"
          x={hx - HANDLE_HIT / 2}
          y={PLOT_TOP - 4}
          width={HANDLE_HIT}
          height={AXIS_Y + 12 - PLOT_TOP}
          onPointerDown={startDrag(which)}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
      </g>
    );
  };

  const undatedTotal = bins.undated.centre + bins.undated.other;

  return (
    <div ref={hostRef} className={drag ? "nw-strip is-dragging" : "nw-strip"} onMouseLeave={leave}>
      {width > 0 && (
        <svg viewBox={`0 0 ${width} ${H}`} width={width} height={H} role="img" aria-label="The acts in view by year of registration">
          {axis && years && <rect className="nw-strip-window" x={x(from)} y={PLOT_TOP} width={x(to + 1) - x(from)} height={PLOT_H} />}
          {ticks.map((year) => (
            <g key={year}>
              <line className="nw-strip-tick" x1={x(year)} y1={PLOT_TOP} x2={x(year)} y2={AXIS_Y} />
              {labelled(year) && (
                <text className="nw-strip-year" x={x(year)} y={LABEL_Y} textAnchor={year === axis?.start ? "start" : year === axis?.end ? "end" : "middle"}>
                  {year}
                </text>
              )}
            </g>
          ))}
          <line className="nw-strip-axis" x1={plotX0} y1={AXIS_Y} x2={plotX1} y2={AXIS_Y} />
          {axis && [...bins.byYear.entries()].map(([year, bin]) => bar(String(year), x(year) + barGap / 2, barW, bin, inside(year)))}
          {hasUndated && (
            <>
              {bar("undated", undatedX - UNDATED_BAR / 2, UNDATED_BAR, bins.undated, true)}
              <text className="nw-strip-count" x={undatedX} y={AXIS_Y - barH(undatedTotal) - (bins.undated.heirs ? 2 * DIAMOND + 4 : 0) - 4} textAnchor="middle">
                {num(undatedTotal)}
              </text>
              <text className="nw-strip-year" x={undatedX} y={LABEL_Y} textAnchor="middle">
                no registration date
              </text>
              <rect
                className="nw-strip-hit"
                x={plotX1 + 8}
                y={PLOT_TOP - 10}
                width={UNDATED_W - 8}
                height={AXIS_Y + 10 - PLOT_TOP}
                onMouseEnter={() => enter("undated")}
                onMouseLeave={leave}
              />
            </>
          )}
          {axis &&
            [...bins.byYear.keys()].map((year) => (
              <rect
                key={year}
                className="nw-strip-hit"
                x={x(year)}
                y={PLOT_TOP - 10}
                width={slot}
                height={AXIS_Y + 10 - PLOT_TOP}
                onMouseEnter={() => enter(year)}
                onMouseLeave={leave}
              />
            ))}
          {hover !== null && hover !== "undated" && axis && !drag && (
            <rect className="nw-strip-hover" x={x(hover)} y={PLOT_TOP} width={slot} height={PLOT_H} />
          )}
          {handle("from")}
          {handle("to")}
        </svg>
      )}
      {tip && (
        <div ref={tipRef} className="nw-tip nw-strip-tip is-above" style={{ left: tip.x + (tipDx.key === tip.key ? tipDx.dx : 0), top: tip.y }} role="tooltip">
          <span>{tip.text}</span>
        </div>
      )}
    </div>
  );
}
