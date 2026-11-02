import { useEffect, useMemo, useRef, useState } from "react";
import type { NetworkChain, NetworkChainAct, NetworkChainEntry, NetworkChainRun } from "../types";
import { labelWidth } from "../utils/networkLayout";
import { duplicateNames, nodeLabel } from "../utils/networkSpec";

/* The chain strip (docs/network/step4_contract.md §4; the chain between two
 * entries, next_2026-11-01.md §4.4): the chain shown as marks left to right at
 * an even pitch, the name under each mark by the namesake rule over the chain's
 * entries with the entry number in amber under it, the two ends in bold, each
 * hop as a line with the year of its act written above it ("no date" for an
 * act with no registration date), the hub ring on an entry on more than 20
 * acts. The strip is 110 px tall and scrolls sideways when the chain is longer
 * than the frame (the longest in the corpus is 23 hops). Hover on a mark or a
 * hop reports what the page feeds the reading card: the entries to light on
 * the circle, the card's head and its line. A click on a mark is the page's
 * (select it when the circle draws it, else open its circle); a click on a
 * hop opens the contract drawer. */

const H = 110;
const LINE_Y = 40;
const YEAR_Y = 27;
const NAME_Y = 66;
const ENTRY_Y = 82;
const R = 5;
const RING = 3.5;
/** the gap between a mark and the hop line at either end */
const LINE_GAP = 3;
/** the pitch never falls under this, nor under the longest label plus LABEL_GAP */
const PITCH_MIN = 96;
const LABEL_GAP = 16;
const PAD_MIN = 24;
const HUB_ACTS = 20;
const HOP_HIT = 16;

const num = (n: number) => n.toLocaleString("en-US");

/** What the page feeds the card and the circle from a hover on the strip. */
export type ChainHover = { ids: Set<number>; head: string; line: string };

function dateWords(act: Pick<NetworkChainAct, "date">): string {
  return !act.date || act.date.startsWith("0000") ? "no registration date" : act.date;
}

/** The stored firm name trimmed of ASCII and non-breaking space at the ends; "not recorded" when blank. */
function firmWords(act: Pick<NetworkChainAct, "firm_name">): string {
  const name = (act.firm_name ?? "").replace(/^[\s ]+|[\s ]+$/g, "");
  return name || "not recorded";
}

/** "5 acts in the corpus, 1743–1784." / "One act in the corpus, 1676." /
 * "2 acts in the corpus, with no registration date." */
export function chainEntryLine(entry: NetworkChainEntry): string {
  const acts = entry.acts_total === 1 ? "One act" : `${num(entry.acts_total)} acts`;
  const span =
    entry.first_year !== null && entry.last_year !== null
      ? entry.first_year === entry.last_year
        ? String(entry.first_year)
        : `${entry.first_year}–${entry.last_year}`
      : "with no registration date";
  return `${acts} in the corpus, ${span}.`;
}

/** The entries of a chain by id, from the response's list; an entry the list
 * lacks reads as its number alone. */
export function chainEntries(data: NetworkChain): Map<number, NetworkChainEntry> {
  const map = new Map<number, NetworkChainEntry>();
  for (const entry of data.entries ?? [data.a, data.b]) map.set(entry.id, entry);
  map.set(data.a.id, data.a);
  map.set(data.b.id, data.b);
  return map;
}

/** The entries a run passes through, in order from its first mark to its last. */
export function chainOrder(run: NetworkChainRun): number[] {
  if (!run.hops.length) return [];
  return [run.hops[0].from, ...run.hops.map((hop) => hop.to)];
}

export default function ChainStrip({
  data,
  run,
  forward,
  drawn,
  selected,
  onHover,
  onMark,
  onHop,
}: {
  data: NetworkChain;
  /** the chain shown: the forward one or the untimed one */
  run: NetworkChainRun;
  /** whether the run shown is the forward one */
  forward: boolean;
  /** the entries the circle draws, for the mark's dress */
  drawn: ReadonlySet<number>;
  /** the selected entry, when one is */
  selected: number | null;
  onHover: (hover: ChainHover | null) => void;
  onMark: (id: number) => void;
  onHop: (actId: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<{ kind: "mark"; id: number } | { kind: "hop"; index: number } | null>(null);

  // The frame's width, measured; the drawing is in true pixels and may be wider.
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

  const entries = useMemo(() => chainEntries(data), [data]);
  const order = useMemo(() => chainOrder(run), [run]);
  const duplicates = useMemo(() => duplicateNames(order.map((id) => entries.get(id)).filter((e): e is NetworkChainEntry => Boolean(e))), [order, entries]);
  const labels = useMemo(
    () =>
      order.map((id) => {
        const entry = entries.get(id);
        return entry ? nodeLabel(entry, duplicates) : `entry ${id}`;
      }),
    [order, entries, duplicates],
  );
  const label = (id: number): string => labels[order.indexOf(id)] ?? `entry ${id}`;

  // An even pitch: the frame shared out over the hops, never under the
  // longest label plus a gap; the ends stand half a label in from the edges.
  const longest = Math.max(0, ...labels.map((text) => labelWidth(text)), ...order.map((id) => labelWidth(`entry ${id}`)));
  const pad = Math.max(PAD_MIN, longest / 2 + 8);
  const n = order.length;
  const fit = n > 1 ? (width - 2 * pad) / (n - 1) : 0;
  const pitch = Math.max(PITCH_MIN, longest + LABEL_GAP, fit);
  const svgWidth = Math.max(width, Math.round(2 * pad + (n - 1) * pitch));
  const x = (i: number) => pad + i * pitch;

  const enterMark = (id: number) => {
    setHover({ kind: "mark", id });
    const entry = entries.get(id);
    onHover({
      ids: new Set([id]),
      head: `${label(id)} entry ${id}`,
      line: entry ? chainEntryLine(entry) : "",
    });
  };
  const enterHop = (index: number) => {
    const hop = run.hops[index];
    setHover({ kind: "hop", index });
    onHover({
      ids: new Set([hop.from, hop.to]),
      head: `Act ${hop.act.id}`,
      line: `${dateWords(hop.act)} · ${firmWords(hop.act)} · signed by ${label(hop.from)} and ${label(hop.to)}.`,
    });
  };
  const leave = () => {
    setHover(null);
    onHover(null);
  };

  const direction = forward ? "forward in time" : "through any acts, in any order";

  return (
    <div ref={hostRef} className="nw-chain" onMouseLeave={leave}>
      {width > 0 && n > 0 && (
        <svg
          viewBox={`0 0 ${svgWidth} ${H}`}
          width={svgWidth}
          height={H}
          role="img"
          aria-label={`The chain of ${num(run.hops.length)} ${run.hops.length === 1 ? "act" : "acts"} ${direction}, from ${labels[0]} to ${labels[n - 1]}`}
          data-hops={run.hops.length}
          data-pitch={Math.round(pitch)}
        >
          <g>
            {run.hops.map((hop, i) => {
              const x1 = x(i) + R + LINE_GAP;
              const x2 = x(i + 1) - R - LINE_GAP;
              const lit = hover?.kind === "hop" && hover.index === i;
              const year = hop.act.year && hop.act.year > 0 ? String(hop.act.year) : "no date";
              return (
                <g key={`${hop.from}.${hop.to}`} className={lit ? "nw-chain-hop is-hover" : "nw-chain-hop"}>
                  <line className="nw-chain-line" x1={x1} y1={LINE_Y} x2={x2} y2={LINE_Y} />
                  <text className={year === "no date" ? "nw-chain-year is-undated" : "nw-chain-year"} x={(x1 + x2) / 2} y={YEAR_Y} textAnchor="middle">
                    {year}
                  </text>
                  <line
                    className="nw-chain-hop-hit"
                    x1={x1}
                    y1={LINE_Y}
                    x2={x2}
                    y2={LINE_Y}
                    strokeWidth={HOP_HIT}
                    onMouseEnter={() => enterHop(i)}
                    onMouseLeave={leave}
                    onClick={() => onHop(hop.act.id)}
                  />
                </g>
              );
            })}
          </g>
          <g>
            {order.map((id, i) => {
              const entry = entries.get(id);
              const end = i === 0 || i === n - 1;
              const cls = [
                "nw-chain-mark",
                end ? "is-end" : "",
                drawn.has(id) ? "is-drawn" : "",
                selected === id ? "is-selected" : "",
                hover?.kind === "mark" && hover.id === id ? "is-hover" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <g key={id} className={cls} data-id={id} onMouseEnter={() => enterMark(id)} onMouseLeave={leave} onClick={() => onMark(id)}>
                  {entry && entry.acts_total > HUB_ACTS && <circle className="nw-chain-ring" cx={x(i)} cy={LINE_Y} r={R + RING} />}
                  <circle className="nw-chain-dot" cx={x(i)} cy={LINE_Y} r={R} />
                  <text className="nw-chain-name" x={x(i)} y={NAME_Y} textAnchor="middle">
                    {labels[i]}
                  </text>
                  <text className="nw-chain-entry" x={x(i)} y={ENTRY_Y} textAnchor="middle">
                    entry {id}
                  </text>
                  <rect className="nw-chain-hit" x={x(i) - R - RING - 4} y={LINE_Y - 14} width={2 * (R + RING + 4)} height={28} />
                  <rect className="nw-chain-hit" x={x(i) - longest / 2 - 4} y={NAME_Y - 12} width={longest + 8} height={ENTRY_Y + 6 - (NAME_Y - 12)} />
                </g>
              );
            })}
          </g>
        </svg>
      )}
    </div>
  );
}
