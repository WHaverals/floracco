import type { PersonAppearance, PersonSeamSide } from "../../types";
import { roleListLabel } from "../../utils/roles";

/** A mark on one appearance: a side, or not marked yet (added since the marks). */
export type SeamMark = PersonSeamSide | "unmarked";

export const SEAM_LANES: Array<{ side: PersonSeamSide; label: string; note: string }> = [
  { side: "this", label: "This entry", note: "the man the entry names" },
  { side: "unsure", label: "Unsure", note: "read, cannot tell" },
  { side: "other", label: "Someone else", note: "another man" },
];

export function appearanceYear(appearance: PersonAppearance): number | null {
  const raw = String(appearance.registration_date ?? "");
  if (!raw || raw === "0000-00-00") return null;
  const year = Number(raw.slice(0, 4));
  return Number.isFinite(year) && year > 0 ? year : null;
}

/** Dated living years per side, and the band between the two men when their
 *  acts separate in time (the server derives the same on read). */
export function seamSummary(appearances: PersonAppearance[], marks: Record<number, SeamMark>) {
  const years: Record<PersonSeamSide, number[]> = { this: [], other: [], unsure: [] };
  const counts: Record<SeamMark, number> = { this: 0, other: 0, unsure: 0, unmarked: 0 };
  appearances.forEach((appearance) => {
    const side = marks[appearance.investor_id] ?? "unmarked";
    counts[side] += 1;
    const year = appearanceYear(appearance);
    if (side !== "unmarked" && year !== null && !appearance.heirs_of) years[side].push(year);
  });
  let band: [number, number] | null = null;
  let interleaved = false;
  if (years.this.length && years.other.length) {
    const thisMax = Math.max(...years.this);
    const thisMin = Math.min(...years.this);
    const otherMax = Math.max(...years.other);
    const otherMin = Math.min(...years.other);
    if (thisMax < otherMin) band = [thisMax, otherMin];
    else if (otherMax < thisMin) band = [otherMax, thisMin];
    else interleaved = true;
  }
  const range = (side: PersonSeamSide) =>
    years[side].length
      ? Math.min(...years[side]) === Math.max(...years[side])
        ? String(years[side][0])
        : `${Math.min(...years[side])}–${Math.max(...years[side])}`
      : "";
  return { years, counts, band, interleaved, range };
}

/* The time chart of a one-entry case while its acts are being marked, or
 * once marks stand: three lanes on one axis, one dot per year and lane, a
 * posthumous appearance as the hollow diamond the other chart uses, and the
 * change drawn as a band between the last act of one man and the first of
 * the other, only when the marks separate in time. Position carries the
 * meaning; colour is secondary. */
export default function SeamRibbon({
  appearances,
  marks,
  caption,
}: {
  appearances: PersonAppearance[];
  marks: Record<number, SeamMark>;
  caption?: string;
}) {
  const { band, interleaved, counts, range } = seamSummary(appearances, marks);
  const dated = appearances.map(appearanceYear).filter((year): year is number => year !== null);
  if (dated.length === 0) {
    return <p className="muted">No dated appearances to draw.</p>;
  }
  const first = Math.min(...dated);
  const last = Math.max(...dated);
  const groupSpan = last - first;
  const targetSpan = Math.max(60, Math.ceil((groupSpan + 10) / 10) * 10);
  const midpoint = (first + last) / 2;
  let axisStart = Math.floor((midpoint - targetSpan / 2) / 10) * 10;
  let axisEnd = axisStart + targetSpan;
  if (axisEnd < last) axisEnd = Math.ceil(last / 10) * 10;
  if (axisStart > first) axisStart = Math.floor(first / 10) * 10;
  const axisSpan = axisEnd - axisStart;
  const ticks = Array.from({ length: Math.floor(axisSpan / 10) + 1 }, (_, index) => axisStart + index * 10);
  const W = 760;
  const labelW = 165;
  const plotW = W - labelW - 18;
  const rowH = 42;
  const H = SEAM_LANES.length * rowH + 34;
  const x = (year: number) => labelW + ((year - axisStart) / axisSpan) * plotW;

  // Dots per lane and year; a year with acts on two sides splits by lane.
  const byLane = new Map<PersonSeamSide, Map<number, PersonAppearance[]>>(
    SEAM_LANES.map((lane) => [lane.side, new Map()]),
  );
  const unmarkedByYear = new Map<number, PersonAppearance[]>();
  appearances.forEach((appearance) => {
    const year = appearanceYear(appearance);
    if (year === null) return;
    const side = marks[appearance.investor_id] ?? "unmarked";
    const target = side === "unmarked" ? unmarkedByYear : (byLane.get(side) as Map<number, PersonAppearance[]>);
    target.set(year, [...(target.get(year) ?? []), appearance]);
  });

  const tip = (items: PersonAppearance[]) =>
    items
      .map(
        (item) =>
          `${item.registration_date ?? ""} · act ${item.contract_id}`
          + `${item.firm_name ? ` · ${item.firm_name}` : ""}`
          + `${item.title ? ` · ${item.title}` : ""}`
          + `${item.roles ? ` · ${roleListLabel(item.roles)}` : ""}`
          + `${item.heirs_of ? " · through heirs" : ""}`,
      )
      .join("\n");

  const summaryLine = [
    `${SEAM_LANES[0].label.toLowerCase()} ${counts.this}${range("this") ? ` (${range("this")})` : ""}`,
    `someone else ${counts.other}${range("other") ? ` (${range("other")})` : ""}`,
    `unsure ${counts.unsure}${range("unsure") ? ` (${range("unsure")})` : ""}`,
    counts.unmarked ? `not yet marked ${counts.unmarked}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const bandLine = band
    ? `Nearest acts of the two men: ${band[1] - band[0]} years apart (${band[0]} → ${band[1]})`
    : interleaved
      ? "The two men's acts interleave in time"
      : "";

  return (
    <figure className="pl-career pl-seam-ribbon">
      <div className="pl-career-summary">
        <strong>{summaryLine}</strong>
        {bandLine ? <span>{bandLine}</span> : null}
        {caption ? <span>{caption}</span> : null}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`The entry's dated appearances in three lanes: this entry, unsure, someone else. ${summaryLine}.${bandLine ? ` ${bandLine}.` : ""}`}
      >
        {band ? (
          <g className="pl-seam-band">
            <rect x={x(band[0])} y={4} width={Math.max(2, x(band[1]) - x(band[0]))} height={H - 26} />
            <text x={(x(band[0]) + x(band[1])) / 2} y={H - 24} textAnchor="middle">
              the change falls between {band[0]} and {band[1]}, from the marks
            </text>
          </g>
        ) : null}
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="pl-career-tick" x1={x(tick)} y1="2" x2={x(tick)} y2={H - 20} />
            <text className="pl-career-axis-year" x={x(tick)} y={H - 4} textAnchor="middle">
              {tick}
            </text>
          </g>
        ))}
        <line className="pl-career-axis" x1={labelW} y1={H - 20} x2={W - 18} y2={H - 20} />
        {SEAM_LANES.map((lane, index) => {
          const y = 10 + index * rowH;
          const years = byLane.get(lane.side) as Map<number, PersonAppearance[]>;
          const sorted = [...years.keys()].sort((a, b) => a - b);
          const extra = lane.side === "this" ? unmarkedByYear : null;
          return (
            <g key={lane.side} className={`pl-seam-lane is-${lane.side}`}>
              <text className="pl-career-name" x="0" y={y + 12}>{lane.label}</text>
              <text className="pl-career-range" x="0" y={y + 27}>
                {lane.note}
                {range(lane.side) ? ` · ${range(lane.side)}` : ""}
              </text>
              <line className="pl-career-guide" x1={labelW} y1={y + 17} x2={W - 18} y2={y + 17} />
              {sorted.length > 1 ? (
                <line
                  className="pl-career-extent"
                  x1={x(sorted[0])}
                  y1={y + 17}
                  x2={x(sorted[sorted.length - 1])}
                  y2={y + 17}
                />
              ) : null}
              {sorted.map((year) => {
                const items = years.get(year) ?? [];
                const living = items.filter((item) => !item.heirs_of);
                const heirs = items.filter((item) => item.heirs_of);
                const px = x(year);
                const py = y + 17;
                return (
                  <g className="pl-career-event" key={`${lane.side}-${year}`} tabIndex={0} role="img" aria-label={`${lane.label}: ${items.length} appearance${items.length === 1 ? "" : "s"} in ${year}`}>
                    <title>{tip(items)}</title>
                    {living.length ? (
                      <circle className="pl-career-point pl-seam-point" cx={px} cy={py} r={living.length > 1 ? 7 : 5} />
                    ) : null}
                    {heirs.length ? (
                      <polygon
                        className="pl-career-posthumous"
                        points={`${px},${py - 6} ${px + 6},${py} ${px},${py + 6} ${px - 6},${py}`}
                      />
                    ) : null}
                    {items.length > 1 ? (
                      <text className="pl-career-count" x={px} y={py + 2.5} textAnchor="middle">{items.length}</text>
                    ) : null}
                  </g>
                );
              })}
              {extra
                ? [...extra.keys()].sort((a, b) => a - b).map((year) => {
                  const items = extra.get(year) ?? [];
                  return (
                    <g className="pl-career-event" key={`unmarked-${year}`} tabIndex={0} role="img" aria-label={`Not yet marked: ${items.length} appearance${items.length === 1 ? "" : "s"} in ${year}`}>
                      <title>Not yet marked (added since the marks){"\n"}{tip(items)}</title>
                      <circle className="pl-seam-unmarked" cx={x(year)} cy={y + 17} r={5} />
                    </g>
                  );
                })
                : null}
            </g>
          );
        })}
      </svg>
      <figcaption className="pl-career-legend">
        <span><i className="pl-career-legend-event" /> dated appearance</span>
        <span><i className="pl-career-legend-posthumous" /> posthumous (heirs of)</span>
        {band ? <span><i className="pl-seam-legend-band" /> where the change falls</span> : null}
        <span><i className="pl-career-legend-extent" /> span from first to last dated appearance</span>
      </figcaption>
    </figure>
  );
}
