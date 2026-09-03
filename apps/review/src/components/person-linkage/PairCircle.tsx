import { useMemo, useRef, useState, type ReactNode } from "react";
import type { CircleAct, CircleData, CirclePartner } from "./corsiniCircle";

/* The business circle of one pair. Company, not identity: the two candidate
 * entries are pinned apart and never joined by an edge (review_lane.md §7).
 *
 * The layout is deterministic, so no two lines can ever lie on top of each
 * other: the partners both entries share stand on an evenly spaced ladder in
 * the middle, ordered by family name; each entry's other partners fan out
 * from it on distinct rays, from its earliest act at the top round to its
 * latest at the bottom, on two alternating radii. Hover (or focus) shows the
 * name as entered and one sentence per act. */

type CircleNode = {
  key: string;
  kind: "candidate" | "partner";
  id: number;
  label: string;
  short: string;
  x: number;
  y: number;
  partner?: CirclePartner;
  candidate?: CircleData["candidates"][number];
};

type CircleLink = {
  key: string;
  source: CircleNode;
  target: CircleNode;
  acts: CircleAct[];
  shared: boolean;
  firm: boolean;
  candidateId: number;
};

const W = 920;
const H = 380;
const CAND_X = [200, 720];
const CAND_Y = H / 2;
const LADDER_TOP = 54;
const FAN_SPAN = 220; // degrees, centred on the side facing away from the middle
const FAN_RADII = [140, 166];

function shortName(name: string): string {
  const parts = name.split(" ");
  return parts.length > 2 ? `${parts[0]} ${parts[parts.length - 1]}` : name;
}

function familyName(name: string): string {
  return name.split(" ").slice(-1)[0];
}

function layout(data: CircleData): { nodes: CircleNode[]; links: CircleLink[] } {
  const candidates: CircleNode[] = data.candidates.map((candidate, index) => ({
    key: `c${candidate.id}`,
    kind: "candidate",
    id: candidate.id,
    label: `entry ${candidate.id}`,
    short: candidate.name,
    candidate,
    x: CAND_X[index],
    y: CAND_Y,
  }));
  const byId = new Map(candidates.map((node) => [node.id, node]));
  const nodes: CircleNode[] = [...candidates];
  const links: CircleLink[] = [];

  const makePartner = (partner: CirclePartner, x: number, y: number): CircleNode => ({
    key: `p${partner.id}`,
    kind: "partner",
    id: partner.id,
    label: partner.name,
    short: shortName(partner.name),
    partner,
    x,
    y,
  });
  const link = (node: CircleNode, candidateId: number) => {
    const acts = node.partner!.acts.filter((act) => act.with === candidateId);
    links.push({
      key: `${node.key}-c${candidateId}`,
      source: node,
      target: byId.get(candidateId)!,
      acts,
      shared: node.partner!.shared,
      firm: acts.some((act) => act.firm === data.shared_firm),
      candidateId,
    });
  };

  // The shared ladder.
  const shared = data.partners
    .filter((partner) => partner.shared)
    .sort((a, b) => familyName(a.name).localeCompare(familyName(b.name)) || a.name.localeCompare(b.name));
  const step = shared.length > 1 ? (H - 2 * LADDER_TOP) / (shared.length - 1) : 0;
  shared.forEach((partner, rung) => {
    const node = makePartner(partner, W / 2, LADDER_TOP + rung * step);
    nodes.push(node);
    data.candidates.forEach((candidate) => link(node, candidate.id));
  });

  // Each entry's own partners, on a fan facing away from the middle.
  data.candidates.forEach((candidate, index) => {
    const own = data.partners
      .filter((partner) => !partner.shared && partner.acts.some((act) => act.with === candidate.id))
      .sort((a, b) => {
        const ya = Math.min(...a.acts.map((act) => act.year));
        const yb = Math.min(...b.acts.map((act) => act.year));
        return ya - yb || a.name.localeCompare(b.name);
      });
    const n = own.length;
    own.forEach((partner, k) => {
      const t = n > 1 ? k / (n - 1) : 0.5;
      // Left entry: sweep from up-right (290°) over the top and round to
      // down-right (70°). Right entry: the mirror image.
      const degrees = index === 0 ? 290 - t * FAN_SPAN : 250 + t * FAN_SPAN;
      const theta = (degrees * Math.PI) / 180;
      const radius = FAN_RADII[k % 2];
      const node = makePartner(partner, CAND_X[index] + radius * Math.cos(theta), CAND_Y + radius * Math.sin(theta));
      nodes.push(node);
      link(node, candidate.id);
    });
  });

  return { nodes, links };
}

export default function PairCircle({ data }: { data: CircleData }) {
  const { nodes, links } = useMemo(() => layout(data), [data]);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<{
    key: string; x: number; y: number; below: boolean; side: "left" | "center" | "right"; body: ReactNode;
  } | null>(null);

  // Balloons flip below a node in the upper part of the stage and hug the
  // near edge for nodes close to either side, so none is clipped.
  const place = (x: number, y: number, below: boolean) => {
    const rect = svgRef.current?.getBoundingClientRect();
    const scale = rect ? rect.width / W : 1;
    const side: "left" | "center" | "right" = x < 190 ? "left" : x > W - 190 ? "right" : "center";
    return { left: x * scale, top: y * scale, below, side };
  };

  const actSentence = (act: CircleAct): ReactNode => (
    <span key={`${act.with}-${act.contract}`}>
      Co-occurs with <b className="pl-circle-tip-ref">entry {act.with}</b> on{" "}
      <b className="pl-circle-tip-ref">contract {act.contract}</b> in{" "}
      <b className="pl-circle-tip-year">{act.year}</b>
      {act.firm ? (
        <>
          , in the firm{" "}
          <i className={act.firm === data.shared_firm ? "pl-circle-tip-firm is-shared" : "pl-circle-tip-firm"}>
            {act.firm}
          </i>
          .
        </>
      ) : (
        "; the act names no firm."
      )}
    </span>
  );

  const nameLine = (name: string, id: number): ReactNode => (
    <strong>
      {name} <span className="pl-circle-tip-id">(entry {id})</span>
    </strong>
  );

  const partnerTip = (partner: CirclePartner, acts: CircleAct[] = partner.acts): ReactNode => (
    <>
      {nameLine(partner.name, partner.id)}
      {acts.map(actSentence)}
    </>
  );

  const candidateTip = (candidate: CircleData["candidates"][number]): ReactNode => (
    <>
      {nameLine(candidate.name, candidate.id)}
      <span>
        <b className="pl-circle-tip-year">{candidate.acts}</b> acts from{" "}
        {candidate.active.replace("–", " to ")}, {candidate.role}.
      </span>
    </>
  );

  const activeKeys = new Set<string>();
  if (hover) {
    activeKeys.add(hover.key);
    links.forEach((link) => {
      if (link.key === hover.key || link.source.key === hover.key || link.target.key === hover.key) {
        activeKeys.add(link.key);
        activeKeys.add(link.source.key);
        activeKeys.add(link.target.key);
      }
    });
  }

  return (
    <figure className="pl-circle">
      <div className="pl-circle-stage">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={
            `The business circle of entries ${data.candidates[0].id} and ${data.candidates[1].id}: ` +
            `${data.partners.length} co-investors, of whom ${data.partners.filter((p) => p.shared).length} appear beside both entries. ` +
            `The two entries are drawn apart and never joined; shared partners stand between them.`
          }
        >
          {links.map((link) => {
            const { source: s, target: t } = link;
            const partner = (s.kind === "partner" ? s : t).partner as CirclePartner;
            const cls = [
              "pl-circle-link",
              link.shared ? "is-shared" : "",
              link.firm ? "is-firm" : "",
              hover && !activeKeys.has(link.key) ? "is-dim" : "",
              hover && activeKeys.has(link.key) && hover.key !== link.key ? "is-active" : "",
            ].filter(Boolean).join(" ");
            const width = 1 + Math.min(link.acts.length, 3) * 0.6 + (link.firm ? 0.8 : 0);
            return (
              <g key={link.key}>
                <line className={cls} x1={s.x} y1={s.y} x2={t.x} y2={t.y} strokeWidth={width} />
                <line
                  className="pl-circle-hit"
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  onMouseEnter={() => {
                    const my = (s.y + t.y) / 2;
                    const p = place((s.x + t.x) / 2, my, my < H * 0.42);
                    setHover({ key: link.key, x: p.left, y: p.top, below: p.below, side: p.side, body: partnerTip(partner, link.acts) });
                  }}
                  onMouseLeave={() => setHover(null)}
                />
              </g>
            );
          })}
          {nodes.map((node) => {
            const isCandidate = node.kind === "candidate";
            const r = isCandidate ? 16 : 5 + Math.min(node.partner?.acts.length ?? 1, 4);
            const cls = [
              "pl-circle-node",
              isCandidate ? "is-candidate" : "",
              node.partner?.shared ? "is-shared" : "",
              hover && !activeKeys.has(node.key) ? "is-dim" : "",
            ].filter(Boolean).join(" ");
            const show = () => {
              const below = node.y < H * 0.42;
              const p = place(node.x, node.y + (below ? r : -r), below);
              setHover({
                key: node.key,
                x: p.left,
                y: p.top,
                below: p.below,
                side: p.side,
                body: isCandidate ? candidateTip(node.candidate!) : partnerTip(node.partner!),
              });
            };
            return (
              <g
                key={node.key}
                className={cls}
                tabIndex={0}
                role="img"
                aria-label={isCandidate ? `${node.label}, ${node.short}` : `${node.label}, entry ${node.id}`}
                onMouseEnter={show}
                onMouseLeave={() => setHover(null)}
                onFocus={show}
                onBlur={() => setHover(null)}
              >
                <circle cx={node.x} cy={node.y} r={r} />
                {isCandidate ? (
                  <>
                    <text className="pl-circle-cand-label" x={node.x} y={node.y - 24} textAnchor="middle">{node.label}</text>
                    <text className="pl-circle-cand-name" x={node.x} y={node.y + 32} textAnchor="middle">{node.short}</text>
                  </>
                ) : node.partner?.shared ? (
                  <text
                    className="pl-circle-label"
                    x={node.x}
                    y={node.y <= H / 2 ? node.y - r - 4 : node.y + r + 11}
                    textAnchor="middle"
                  >
                    {node.short}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {hover ? (
          <div
            className={["pl-circle-tip", hover.below ? "is-below" : "", `is-${hover.side}`].filter(Boolean).join(" ")}
            style={{ left: hover.x, top: hover.y }}
            role="tooltip"
          >
            {hover.body}
          </div>
        ) : null}
      </div>
      <figcaption>
        <span><i className="pl-circle-key is-shared" /> partner of both entries</span>
        <span><i className="pl-circle-key" /> partner of one entry only</span>
        <span><i className="pl-circle-key-line is-firm" /> a contract of the renewed firm</span>
        <span className="pl-circle-caption">
          The two entries are drawn apart and never joined: this figure shows
          company, not identity. Each entry&rsquo;s own partners fan out from
          it, from its earliest contract at the top to its latest at the
          bottom. Hover over a partner or a line to see the contracts behind
          it.
        </span>
      </figcaption>
    </figure>
  );
}
