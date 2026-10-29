import { useMemo, useRef, useState, type ReactNode } from "react";
import type { NetworkAct, NetworkAppearance, NetworkCircle, NetworkKind } from "../types";
import type { NetworkLayout, PlacedLine, PlacedNode } from "../utils/networkLayout";
import { HUB_WORDS, type NetworkSelect } from "../utils/networkSpec";
import { roleLabel } from "../utils/roles";

/* The circle of one entry as a figure (docs/network/step1_contract.md §3): the
 * ring layout of utils/networkLayout.ts drawn as an SVG at width 100%, with
 * the house marks (a dot sized by acts in view; a square for an institution,
 * estate, shareholder group or placeholder; a thin outer ring for an entry on
 * more than 20 acts; a hollow diamond for an entry that appears only through
 * heirs), the spokes and parent lines always, the other ties light at one
 * step and on hover or selection at two, labels at rest for the centre, the
 * hubs and the best-connected entries, and the house dark balloon on hover
 * with one plain sentence per act. A click selects; the page keeps the
 * selection and fills the side table. */

export const KIND_WORDS: Record<NetworkKind, string> = {
  person: "person entry",
  institution: "institution",
  estate: "estate",
  collective: "shareholder group",
  placeholder: "placeholder",
};

/** The role of a stake in running prose: "general partner", "external investor
 * (accomandante)", both, or "" with no live stake. */
export function roleWords(role: string | null | undefined): string {
  if (!role) return "";
  if (role === "both") return `${roleLabel("gp").toLowerCase()} and ${roleLabel("lp").toLowerCase()}`;
  return roleLabel(role).toLowerCase();
}

/** The qualifiers of an appearance, after the role: through heirs, through a proxy, on a joint stake. */
export function appearanceNotes(appearance: NetworkAppearance): string[] {
  const notes: string[] = [];
  if (appearance.heirs_of) notes.push("through heirs");
  if (appearance.proxy) notes.push("through a proxy");
  if (appearance.joint) notes.push("on a joint stake");
  return notes;
}

/** "in 1690", or the set phrase for an act with no registration date. */
export function actYearWords(act: NetworkAct | undefined): string {
  return act?.year ? `in ${act.year}` : "with no registration date";
}

/** One plain sentence for an appearance: "Signed act 4364 together in 1690, in
 * the firm Mario Morelli e compagni, as external investor (accomandante)." */
export function appearanceSentence(appearance: NetworkAppearance, act: NetworkAct | undefined): string {
  const firm = act?.firm_name ? `in the firm ${act.firm_name}` : "with no firm name recorded";
  const role = roleWords(appearance.role);
  const stake = [role ? `as ${role}` : "with no live stake recorded", ...appearanceNotes(appearance)].join(", ");
  return `Signed act ${appearance.act} together ${actYearWords(act)}, ${firm}, ${stake}.`;
}

/** Labelled at rest: the centre, the hubs, and the best-connected entries (ten
 * or more co-signers in view), at most LABEL_MOST of them so a hub's circle
 * stays readable; the rest label on hover, on selection, or with Label all. */
const LABEL_DEGREE = 10;
const LABEL_MOST = 20;
const TIP_ACTS = 5;

type Hover = { kind: "node"; id: number } | { kind: "line"; key: string };

function sortByDate(ids: number[], actsById: Map<number, NetworkAct>): number[] {
  const date = (id: number) => {
    const d = actsById.get(id)?.date ?? "";
    return !d || d.startsWith("0000") ? "9999-99-99" : d;
  };
  return [...ids].sort((p, q) => {
    const dp = date(p);
    const dq = date(q);
    return dp < dq ? -1 : dp > dq ? 1 : p - q;
  });
}

export default function NetworkFigure({
  data,
  layout,
  hubs,
  selected,
  labelAll,
  onSelect,
}: {
  data: NetworkCircle;
  layout: NetworkLayout;
  /** the hub state in force, for the balloon's words */
  hubs: NetworkCircle["hubs"];
  selected: NetworkSelect | null;
  labelAll: boolean;
  onSelect: (select: NetworkSelect) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const actsById = useMemo(() => new Map(data.acts.map((act) => [act.id, act])), [data]);
  const appearancesByPerson = useMemo(() => {
    const map = new Map<number, NetworkAppearance[]>();
    for (const appearance of data.appearances) {
      const list = map.get(appearance.person) ?? [];
      list.push(appearance);
      map.set(appearance.person, list);
    }
    return map;
  }, [data]);
  const neighbours = useMemo(() => {
    const map = new Map<number, Set<number>>();
    for (const line of layout.lines) {
      (map.get(line.a) ?? map.set(line.a, new Set()).get(line.a)!).add(line.b);
      (map.get(line.b) ?? map.set(line.b, new Set()).get(line.b)!).add(line.a);
    }
    return map;
  }, [layout]);
  const linesByKey = useMemo(() => new Map(layout.lines.map((line) => [line.key, line])), [layout]);
  const signersOfSelectedAct = useMemo(() => {
    if (selected?.kind !== "contract") return new Set<number>();
    return new Set(data.appearances.filter((a) => a.act === selected.id).map((a) => a.person));
  }, [data, selected]);

  const hoveredNode = hover?.kind === "node" ? hover.id : null;
  const hoveredLine = hover?.kind === "line" ? linesByKey.get(hover.key) ?? null : null;
  const selectedPerson = selected?.kind === "person" ? selected.id : null;
  const selectedAct = selected?.kind === "contract" ? selected.id : null;
  const oneStep = data.steps === 1;

  // The entries whose ties are drawn at two steps: the hovered and the selected.
  const active = new Set<number>();
  if (hoveredNode !== null) active.add(hoveredNode);
  if (selectedPerson !== null) active.add(selectedPerson);

  const touches = (line: PlacedLine, id: number | null) => id !== null && (line.a === id || line.b === id);
  const drawn = layout.lines.filter(
    (line) =>
      line.kind !== "tie" ||
      oneStep ||
      touches(line, hoveredNode) ||
      touches(line, selectedPerson) ||
      (selectedAct !== null && line.acts.includes(selectedAct)),
  );

  const restLabelled = useMemo(() => {
    const best = layout.nodes
      .filter((placed) => placed.ring !== 0 && !placed.node.hub && placed.degree >= LABEL_DEGREE)
      .sort((p, q) => q.degree - p.degree || p.node.id - q.node.id)
      .slice(0, LABEL_MOST);
    return new Set(best.map((placed) => placed.node.id));
  }, [layout]);

  const labelled = (placed: PlacedNode): boolean => {
    const id = placed.node.id;
    if (labelAll || placed.ring === 0 || placed.node.hub || restLabelled.has(id)) return true;
    if (id === selectedPerson || signersOfSelectedAct.has(id)) return true;
    if (hoveredNode !== null && (id === hoveredNode || neighbours.get(hoveredNode)?.has(id))) return true;
    if (hoveredLine && (hoveredLine.a === id || hoveredLine.b === id)) return true;
    return false;
  };

  // Balloons flip below a node in the upper part of the stage and hug the
  // near edge for nodes close to either side, so none is clipped (PairCircle).
  const place = (x: number, y: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    const scale = rect ? rect.width / layout.width : 1;
    const side: "left" | "center" | "right" = x < 200 ? "left" : x > layout.width - 200 ? "right" : "center";
    return { left: x * scale, top: y * scale, below: y < layout.height * 0.5, side };
  };

  const nodeTip = (placed: PlacedNode): ReactNode => {
    const node = placed.node;
    const appearances = sortByDate(
      (appearancesByPerson.get(node.id) ?? []).map((a) => a.act),
      actsById,
    ).map((actId) => (appearancesByPerson.get(node.id) ?? []).find((a) => a.act === actId)!);
    const shown = appearances.slice(0, TIP_ACTS);
    return (
      <>
        <strong>
          {node.name} <span className="nw-tip-id">entry {node.id}</span>
        </strong>
        {node.kind !== "person" && <span>Recorded as {node.kind === "institution" || node.kind === "estate" ? "an" : "a"} {KIND_WORDS[node.kind]}.</span>}
        {node.woman && <span>Recorded as woman.</span>}
        {node.hub && (
          <span>
            On {node.acts_total.toLocaleString("en-US")} acts in the corpus; {HUB_WORDS[hubs]}.
          </span>
        )}
        {placed.heirsOnly && <span>Appears here only through heirs.</span>}
        {shown.map((appearance) => (
          <span key={appearance.act}>{appearanceSentence(appearance, actsById.get(appearance.act))}</span>
        ))}
        {appearances.length > shown.length && (
          <span className="nw-tip-more">And {(appearances.length - shown.length).toLocaleString("en-US")} more acts in view.</span>
        )}
      </>
    );
  };

  const lineTip = (line: PlacedLine): ReactNode => {
    const a = layout.byId.get(line.a)?.node;
    const b = layout.byId.get(line.b)?.node;
    const acts = sortByDate(line.acts, actsById);
    const shown = acts.slice(0, TIP_ACTS);
    return (
      <>
        <strong>
          {a?.name} <span className="nw-tip-id">entry {line.a}</span> and {b?.name} <span className="nw-tip-id">entry {line.b}</span>
        </strong>
        <span>
          Signed {acts.length === 1 ? "one act" : `${acts.length.toLocaleString("en-US")} acts`} together:{" "}
          {shown.map((id) => `act ${id} ${actYearWords(actsById.get(id))}`).join("; ")}
          {acts.length > shown.length ? `; and ${(acts.length - shown.length).toLocaleString("en-US")} more` : ""}.
        </span>
      </>
    );
  };

  const tip = (() => {
    if (!hover) return null;
    if (hover.kind === "node") {
      const placed = layout.byId.get(hover.id);
      if (!placed) return null;
      const p = place(placed.x, placed.y + (placed.y < layout.height * 0.5 ? placed.r : -placed.r));
      return { ...p, body: nodeTip(placed) };
    }
    const line = linesByKey.get(hover.key);
    if (!line) return null;
    const a = layout.byId.get(line.a);
    const b = layout.byId.get(line.b);
    if (!a || !b) return null;
    const p = place((a.x + b.x) / 2, (a.y + b.y) / 2);
    return { ...p, body: lineTip(line) };
  })();

  const mark = (placed: PlacedNode): ReactNode => {
    const { x, y, r, node } = placed;
    if (placed.heirsOnly) {
      const d = r * 1.35;
      return <polygon className="nw-mark is-heirs" points={`${x},${y - d} ${x + d},${y} ${x},${y + d} ${x - d},${y}`} />;
    }
    if (node.kind !== "person") return <rect className="nw-mark is-other" x={x - r} y={y - r} width={2 * r} height={2 * r} />;
    return <circle className="nw-mark" cx={x} cy={y} r={r} />;
  };

  const label = (placed: PlacedNode): ReactNode => {
    const { x, y, r, angle, ring, node } = placed;
    if (ring === 0) {
      return (
        <text key={node.id} className="nw-label is-centre" x={x} y={y + r + 15} textAnchor="middle">
          {node.name}
        </text>
      );
    }
    const s = Math.sin(angle);
    const c = Math.cos(angle);
    const anchor = s > 0.3 ? "start" : s < -0.3 ? "end" : "middle";
    return (
      <text
        key={node.id}
        className={node.hub ? "nw-label is-hub" : "nw-label"}
        x={x + s * (r + 5)}
        y={y - c * (r + 5) + (4 - 6 * c)}
        textAnchor={anchor}
      >
        {node.name}
      </text>
    );
  };

  return (
    <div className="nw-stage" onMouseLeave={() => setHover(null)}>
      <svg ref={svgRef} viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" role="img" aria-label={data.sentence}>
        <g>
          {drawn.map((line) => {
            const a = layout.byId.get(line.a)!;
            const b = layout.byId.get(line.b)!;
            const hovered = hover?.kind === "line" ? hover.key === line.key : touches(line, hoveredNode);
            const chosen = touches(line, selectedPerson) || (selectedAct !== null && line.acts.includes(selectedAct));
            const cls = [
              "nw-line",
              `is-${line.kind}`,
              hovered ? "is-active" : "",
              chosen ? "is-selected" : "",
              hover && !hovered && !chosen ? "is-dim" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <g key={line.key}>
                <line className={cls} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={line.width} />
                {line.kind !== "tie" && (
                  <line
                    className="nw-hit-line"
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    onMouseEnter={() => setHover({ kind: "line", key: line.key })}
                    onMouseLeave={() => setHover(null)}
                  />
                )}
              </g>
            );
          })}
        </g>
        <g>
          {layout.nodes.map((placed) => {
            const id = placed.node.id;
            const near =
              hoveredNode !== null
                ? id === hoveredNode || Boolean(neighbours.get(hoveredNode)?.has(id))
                : hoveredLine
                  ? hoveredLine.a === id || hoveredLine.b === id
                  : true;
            const cls = [
              "nw-node",
              placed.ring === 0 ? "is-centre" : "",
              id === selectedPerson ? "is-selected" : "",
              signersOfSelectedAct.has(id) ? "is-signer" : "",
              id === hoveredNode ? "is-hover" : "",
              hover && !near && id !== selectedPerson ? "is-dim" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <g
                key={id}
                className={cls}
                data-id={id}
                onMouseEnter={() => setHover({ kind: "node", id })}
                onMouseLeave={() => setHover(null)}
                onClick={() => onSelect({ kind: "person", id })}
              >
                {placed.node.hub && <circle className="nw-hub-ring" cx={placed.x} cy={placed.y} r={placed.r + 3.5} />}
                {mark(placed)}
                <circle className="nw-hit" cx={placed.x} cy={placed.y} r={Math.max(9, placed.r + 3)} />
              </g>
            );
          })}
        </g>
        <g>{layout.nodes.filter(labelled).map(label)}</g>
      </svg>
      {tip && (
        <div
          className={["nw-tip", tip.below ? "is-below" : "", `is-${tip.side}`].filter(Boolean).join(" ")}
          style={{ left: tip.left, top: tip.top }}
          role="tooltip"
        >
          {tip.body}
        </div>
      )}
    </div>
  );
}

/** The marks, for a key under the figure. */
export function NetworkKey({ data }: { data: NetworkCircle }) {
  const others = data.nodes.some((node) => node.kind !== "person");
  const byPerson = new Map<number, boolean>();
  for (const appearance of data.appearances) byPerson.set(appearance.person, (byPerson.get(appearance.person) ?? true) && appearance.heirs_of);
  const heirs = [...byPerson.values()].some(Boolean);
  return (
    <p className="nw-key">
      <span>
        <i className="nw-key-dot" /> a person entry, sized by its acts in view
      </span>
      {data.counts.hubs > 0 && (
        <span>
          <i className="nw-key-ring" /> on more than 20 acts
        </span>
      )}
      {others && (
        <span>
          <i className="nw-key-square" /> an institution, estate, shareholder group or placeholder
        </span>
      )}
      {heirs && (
        <span>
          <i className="nw-key-diamond" /> appears here only through heirs
        </span>
      )}
      <span>
        <i className="nw-key-line" /> an act signed together; wider with more acts
      </span>
    </p>
  );
}
