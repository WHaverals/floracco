import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { loadReference, loadReferenceRecords } from "../api";
import type {
  ReferenceKind,
  ReferenceListResponse,
  ReferenceRecordsResponse,
  ReferenceTerm,
} from "../types";

/* The four controlled vocabularies, as a reading surface. The system shows only
 * facts — the verbatim term and how many records reference it — plus text search.
 * It never categorises, normalises, or merges. Interpretation is a human act,
 * reserved for later phases. (See docs/reference/scope.md.)
 */

const VOCABS: { kind: ReferenceKind; label: string; noun: string; quick: string[] }[] = [
  { kind: "place", label: "Places", noun: "place", quick: ["popolo", "fiere", "parti di"] },
  { kind: "title", label: "Titles", noun: "title", quick: ["senatore", "cavaliere", "quondam", "signora"] },
  { kind: "currency", label: "Currencies", noun: "currency", quick: ["scudi", "ducati", "fiorini", "lire", "pezze"] },
  { kind: "activity", label: "Economic activities", noun: "economic activity", quick: ["seta", "lana", "cambi", "panni", "spezieria"] },
];

/** Horizontal usage bars — the overview "shape" of a vocabulary (pure counts). */
function UsageBars({
  top,
  onPick,
}: {
  top: { value: string; count: number }[];
  onPick: (value: string) => void;
}) {
  const max = Math.max(1, ...top.map((t) => t.count));
  return (
    <div className="ref-bars">
      {top.map((t) => (
        <button type="button" className="ref-bar-row" key={t.value} onClick={() => onPick(t.value)}>
          <span className="ref-bar-label" title={t.value}>
            {t.value}
          </span>
          <span className="ref-bar-track">
            <span className="ref-bar-fill" style={{ width: `${Math.round((t.count / max) * 100)}%` }} />
          </span>
          <span className="ref-bar-count">{t.count.toLocaleString()}</span>
        </button>
      ))}
    </div>
  );
}

/** "Attested by decade" for a selected term — pure counts, with a year axis and a
 *  hover tooltip so the timeline is readable (1445–1808; registration ended 1808). */
function DecadeBars({ data }: { data: { decade: number; count: number }[] }) {
  const FROM = 1440;
  const TO = 1800;
  const decades: number[] = [];
  for (let d = FROM; d <= TO; d += 10) decades.push(d);
  const map = new Map(data.map((d) => [d.decade, d.count]));
  const max = Math.max(1, ...data.map((d) => d.count));
  const W = 720;
  const H = 84;
  const padX = 4;
  const padTop = 6;
  const plotH = 50;
  const axisY = padTop + plotH;
  const slot = (W - padX * 2) / decades.length;
  const barW = Math.max(2, slot - 1.6);
  const [hover, setHover] = useState<number | null>(null);
  const tipDecade = hover !== null ? decades[hover] : null;
  const tipLeft =
    hover !== null ? Math.max(12, Math.min(88, ((padX + hover * slot + slot / 2) / W) * 100)) : 0;

  if (data.length === 0) return null;
  return (
    <div className="ref-decade-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label="Contracts referencing this term, per decade, 1445 to 1808"
        preserveAspectRatio="xMidYMid meet"
      >
        <line x1={padX} y1={axisY} x2={W - padX} y2={axisY} stroke="#e0d4c0" strokeWidth="1" />
        {decades.map((d, i) => {
          const c = map.get(d) ?? 0;
          const h = (c / max) * plotH;
          const x = padX + i * slot;
          return (
            <g key={d}>
              {hover === i && <rect x={x} y={padTop} width={slot} height={plotH} fill="#9d7355" opacity="0.09" />}
              <rect x={x + (slot - barW) / 2} y={axisY - h} width={barW} height={h} rx="1" fill="#c79a6b" />
              <rect
                x={x}
                y={padTop}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseOver={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <title>{`${d}s · ${c}`}</title>
              </rect>
            </g>
          );
        })}
        {[1450, 1500, 1550, 1600, 1650, 1700, 1750, 1800].map((y) => {
          const x = padX + ((y - FROM) / 10) * slot + slot / 2;
          return (
            <text key={y} x={x} y={H - 3} fontSize="10" fill="#9a8a78" textAnchor="middle" fontFamily="Georgia, serif">
              {y}
            </text>
          );
        })}
      </svg>
      {tipDecade !== null && (
        <div className="ribbon-tip" style={{ left: `${tipLeft}%` }}>
          <span className="ribbon-tip-decade">{tipDecade}s</span>
          <span className="ribbon-tip-row">
            {(map.get(tipDecade) ?? 0).toLocaleString()} contract{(map.get(tipDecade) ?? 0) === 1 ? "" : "s"}
          </span>
        </div>
      )}
    </div>
  );
}

export default function Reference() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<ReferenceKind>("place");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("usage");
  const [orphansOnly, setOrphansOnly] = useState(false);
  const [list, setList] = useState<ReferenceListResponse | null>(null);
  const [terms, setTerms] = useState<ReferenceTerm[]>([]);
  const [selected, setSelected] = useState<ReferenceTerm | null>(null);
  const [detail, setDetail] = useState<ReferenceRecordsResponse | null>(null);
  const [error, setError] = useState("");
  const debounce = useRef<number | undefined>(undefined);

  const vocab = useMemo(() => VOCABS.find((v) => v.kind === kind)!, [kind]);

  const runList = useCallback(
    (k: ReferenceKind, q: string, s: string, orphans: boolean, offset: number) => {
      loadReference(k, { q, sort: s, orphansOnly: orphans, offset })
        .then((res) => {
          setList(res);
          setTerms((prev) => (offset > 0 ? [...prev, ...res.terms] : res.terms));
          setError("");
        })
        .catch((err: Error) => setError(err.message));
    },
    [],
  );

  useEffect(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => runList(kind, search, sort, orphansOnly, 0), 200);
    return () => window.clearTimeout(debounce.current);
  }, [kind, search, sort, orphansOnly, runList]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    loadReferenceRecords(kind, selected.id)
      .then(setDetail)
      .catch(() => setDetail(null));
  }, [kind, selected]);

  const switchKind = (k: ReferenceKind) => {
    setKind(k);
    setSearch("");
    setOrphansOnly(false);
    setSelected(null);
  };

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Reference</p>
          <div className="db-tabs ref-tabs">
            {VOCABS.map((v) => (
              <button
                key={v.kind}
                type="button"
                className={v.kind === kind ? "db-tab is-active" : "db-tab"}
                onClick={() => switchKind(v.kind)}
              >
                {v.label}
              </button>
            ))}
          </div>

          <input
            className="db-search"
            type="search"
            placeholder={`Search ${vocab.label.toLowerCase()}…`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />

          <div className="ref-quick">
            {vocab.quick.map((term) => (
              <button
                key={term}
                type="button"
                className={search === term ? "ref-chip is-active" : "ref-chip"}
                onClick={() => setSearch(search === term ? "" : term)}
                title={`Show terms whose text contains “${term}”`}
              >
                {term}
              </button>
            ))}
          </div>

          <div className="db-controls">
            <label className="db-sort">
              <span className="db-sort-label">Sort</span>
              <select value={sort} onChange={(event) => setSort(event.target.value)}>
                <option value="usage">Most used</option>
                <option value="name">A–Z</option>
              </select>
            </label>
            <label
              className="db-show-hidden"
              title="Terms that no current record references (usage 0) — kept verbatim, surfaced for review"
            >
              <input type="checkbox" checked={orphansOnly} onChange={(e) => setOrphansOnly(e.target.checked)} />
              Unused only
            </label>
          </div>

          <p className="db-count muted">
            {error
              ? error
              : list
                ? list.total === 0
                  ? "No terms"
                  : `Showing 1–${terms.length.toLocaleString()} of ${list.total.toLocaleString()}`
                : "Loading…"}
          </p>
          {orphansOnly && (
            <p className="ref-unused-note muted">
              “Unused” = a term in the {vocab.label.toLowerCase()} dictionary that isn’t directly
              linked to any contract. It may still appear in a contract’s narrative — open a term to
              see.
            </p>
          )}
        </div>

        <ul className="db-results">
          {terms.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                className={selected?.id === t.id ? "db-result is-active" : "db-result"}
                onClick={() => setSelected(t)}
              >
                <span className="db-result-title">{t.value}</span>
                <span className="db-result-meta muted">
                  {t.count > 0 ? `used ${t.count.toLocaleString()}×` : "unused"}
                </span>
              </button>
            </li>
          ))}
          {terms.length === 0 && !error && <li className="db-empty muted">No terms match.</li>}
          {list && terms.length > 0 && terms.length < list.total && (
            <li className="db-load-more">
              <button
                type="button"
                className="db-load-more-btn"
                onClick={() => runList(kind, search, sort, orphansOnly, terms.length)}
              >
                Load more <span className="db-load-more-count">({(list.total - terms.length).toLocaleString()} more)</span>
              </button>
            </li>
          )}
        </ul>
      </aside>

      <section className="db-detail">
        {!selected ? (
          <div className="ref-overview">
            <p className="eyebrow">Most-referenced</p>
            <h2>{vocab.label} by usage</h2>
            <p className="muted ref-note">
              How many records reference each term across the corpus. Select a term to read the
              contracts that use it.
            </p>
            {list && list.top.length > 0 ? (
              <UsageBars top={list.top} onPick={(value) => setSearch(value)} />
            ) : (
              <p className="muted">No usage to chart.</p>
            )}
          </div>
        ) : (
          <div className="ref-term">
            <p className="eyebrow">{vocab.label.replace(/s$/, "")} · verbatim term</p>
            <h2 className="ref-term-value">{selected.value}</h2>
            <p className="muted ref-term-meta">
              Used as a {vocab.noun} in {(detail?.record_total ?? selected.count).toLocaleString()} contract
              {(detail?.record_total ?? selected.count) === 1 ? "" : "s"}
            </p>
            {detail && (
              <p className="muted ref-narrative-line">
                {detail.narrative_mentions > 0 ? (
                  <>
                    The word also appears in {detail.narrative_mentions.toLocaleString()} contract narrative
                    {detail.narrative_mentions === 1 ? "" : "s"} —{" "}
                    <button
                      type="button"
                      className="link-like"
                      onClick={() => navigate(`/explore?q=${encodeURIComponent(selected.value)}`)}
                    >
                      view in corpus search →
                    </button>
                  </>
                ) : (
                  "The word does not appear in any narrative text."
                )}
              </p>
            )}

            {detail && detail.by_decade.length > 0 && (
              <div className="ref-term-block">
                <p className="ref-block-label">Attested by decade</p>
                <DecadeBars data={detail.by_decade} />
              </div>
            )}

            <div className="ref-term-block">
              <p className="ref-block-label">
                Contracts recording this {vocab.noun}{detail ? ` (${detail.record_total.toLocaleString()})` : ""}
              </p>
              {detail && detail.records.length > 0 ? (
                <ul className="ref-records">
                  {detail.records.map((r) => (
                    <li key={r.row_id}>
                      <button
                        type="button"
                        className="db-result"
                        onClick={() => navigate(`/database/contract/${r.id}`)}
                      >
                        <span className="db-result-title">{r.title}</span>
                        <span className="db-result-meta muted">{r.meta || `#${r.id}`}</span>
                      </button>
                    </li>
                  ))}
                  {detail.record_total > detail.records.length && (
                    <li className="db-empty muted">
                      Showing first {detail.records.length.toLocaleString()} of {detail.record_total.toLocaleString()}.
                    </li>
                  )}
                </ul>
              ) : (
                <p className="muted">
                  {detail
                    ? `No contract records this term as a ${vocab.noun} — it’s in the vocabulary but never structurally linked.`
                    : "Loading…"}
                </p>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
