import { useState } from "react";

type Bin = { decade: number; count: number };

/* Accomandite vs. later acts registered per decade, 1445–1808. Two series shown
 * as paired bars (not stacked) so each stays readable on its own. Hovering a
 * decade shows its exact counts; a native <title> mirrors that for accessibility.
 * The x-axis stops at the 1800s decade — registration was abolished in 1808, so
 * the handful of stray post-1808 sub-acts in the data are out of scope here. */
export default function DistributionRibbon({ contract, sub }: { contract: Bin[]; sub: Bin[] }) {
  const FROM = 1440;
  const TO = 1800;
  const decades: number[] = [];
  for (let d = FROM; d <= TO; d += 10) decades.push(d);

  const cMap = new Map(contract.map((b) => [b.decade, b.count]));
  const sMap = new Map(sub.map((b) => [b.decade, b.count]));
  const max = Math.max(1, ...decades.flatMap((d) => [cMap.get(d) ?? 0, sMap.get(d) ?? 0]));

  const W = 720;
  const H = 96;
  const padX = 4;
  const padTop = 8;
  const plotH = 58;
  const axisY = padTop + plotH;
  const slot = (W - padX * 2) / decades.length;
  const barW = Math.max(2, slot / 2 - 1.4);

  const [hover, setHover] = useState<number | null>(null);
  const tipDecade = hover !== null ? decades[hover] : null;
  const tipLeft =
    hover !== null ? Math.max(12, Math.min(88, ((padX + hover * slot + slot / 2) / W) * 100)) : 0;

  return (
    <div className="home-ribbon-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label="Accomandite and later acts registered per decade, 1445 to 1808"
        preserveAspectRatio="xMidYMid meet"
      >
        <line x1={padX} y1={axisY} x2={W - padX} y2={axisY} stroke="#e0d4c0" strokeWidth="1" />
        {decades.map((d, i) => {
          const x = padX + i * slot;
          const c = cMap.get(d) ?? 0;
          const s = sMap.get(d) ?? 0;
          const ch = (c / max) * plotH;
          const sh = (s / max) * plotH;
          return (
            <g key={d}>
              {hover === i && (
                <rect x={x} y={padTop} width={slot} height={plotH} fill="#9d7355" opacity="0.09" />
              )}
              <rect x={x + slot / 2 - barW - 0.7} y={axisY - ch} width={barW} height={ch} rx="1" fill="#b07a47" />
              <rect x={x + slot / 2 + 0.7} y={axisY - sh} width={barW} height={sh} rx="1" fill="#7d8c6a" />
              <rect
                x={x}
                y={padTop}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <title>{`${d}s — ${c} accomandite, ${s} later acts`}</title>
              </rect>
            </g>
          );
        })}
        {[1450, 1500, 1550, 1600, 1650, 1700, 1750, 1800].map((y) => {
          const x = padX + ((y - FROM) / 10) * slot + slot / 2;
          return (
            <text key={y} x={x} y={H - 4} fontSize="10" fill="#9a8a78" textAnchor="middle" fontFamily="Georgia, serif">
              {y}
            </text>
          );
        })}
      </svg>

      {tipDecade !== null && (
        <div className="ribbon-tip" style={{ left: `${tipLeft}%` }}>
          <span className="ribbon-tip-decade">{tipDecade}s</span>
          <span className="ribbon-tip-row">
            <span className="ribbon-tip-dot is-acc" />
            {(cMap.get(tipDecade) ?? 0).toLocaleString()} accomandite
          </span>
          <span className="ribbon-tip-row">
            <span className="ribbon-tip-dot is-acts" />
            {(sMap.get(tipDecade) ?? 0).toLocaleString()} later acts
          </span>
        </div>
      )}
    </div>
  );
}
