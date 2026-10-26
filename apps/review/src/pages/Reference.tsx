import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  createReferenceLink,
  loadReference,
  loadReferenceDuplicates,
  loadReferenceRecords,
  revokeReferenceLink,
} from "../api";
import { useLatest } from "../utils/latest";
import PlaceMap from "../components/PlaceMap";
import CopyLinkButton from "../components/CopyLinkButton";
import type {
  ReferenceDuplicatesResponse,
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

/* Every number counts contracts (WH, 2026-10-26). `hint` says what puts a
 * contract under a term: a place or a title can be recorded several times on
 * one contract (once per partner), and the contract still counts once. */
const VOCABS: {
  kind: ReferenceKind;
  label: string;
  singular: string;
  noun: string;
  aNoun: string;
  hint: string;
  quick: string[];
}[] = [
  {
    kind: "place",
    label: "Places",
    singular: "Place",
    noun: "place",
    aNoun: "a place",
    hint: "A contract counts once, whether the place is its place of activity or a partner’s residence or place of origin.",
    quick: ["popolo", "fiere", "parti di"],
  },
  {
    kind: "title",
    label: "Titles",
    singular: "Title",
    noun: "title",
    aNoun: "a title",
    hint: "A contract counts once, whether the title is a partner’s own or one recorded for a partner’s husband, father or mother, or grandfather.",
    quick: ["senatore", "cavaliere", "quondam", "signora"],
  },
  {
    kind: "currency",
    label: "Currencies",
    singular: "Currency",
    noun: "currency",
    aNoun: "a currency",
    hint: "Each contract counts once, under the currency it is recorded in.",
    quick: ["scudi", "ducati", "fiorini", "lire", "pezze"],
  },
  {
    kind: "activity",
    label: "Economic activities",
    singular: "Economic activity",
    noun: "economic activity",
    aNoun: "an economic activity",
    hint: "Each contract counts once, under the economic activity recorded for it.",
    quick: ["seta", "lana", "cambi", "panni", "spezieria"],
  },
];

const fmtContracts = (n: number) => `${n.toLocaleString()} contract${n === 1 ? "" : "s"}`;

/** A term's number in the rail: the contracts that record it. */
function usageLabel(n: number): string {
  return n === 0 ? "unused" : fmtContracts(n);
}

/* The kinds of use behind a place's or a title's number, in the order the
 * record page lists those fields; each part counts contracts too. */
const PART_PHRASE: [string, (n: number) => string][] = [
  ["activity", (n) => `on ${n.toLocaleString()} as the place of activity`],
  ["residence", (n) => `on ${n.toLocaleString()} as a partner’s residence`],
  ["origin", (n) => `on ${n.toLocaleString()} as a partner’s place of origin`],
  ["title", (n) => `on ${n.toLocaleString()} as a partner’s own title`],
  ["title_husband", (n) => `on ${n.toLocaleString()} as a husband’s title`],
  ["title_father_mother", (n) => `on ${n.toLocaleString()} as a father’s or mother’s title`],
  ["title_grandfather", (n) => `on ${n.toLocaleString()} as a grandfather’s title`],
];

const joinAnd = (xs: string[]) =>
  xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")}${xs.length > 2 ? "," : ""} and ${xs[xs.length - 1]}`;

/** One sentence that puts the rail's number (contracts) and its parts together. */
function usageSentence(kind: ReferenceKind, d: ReferenceRecordsResponse): string {
  if (d.usage_total === 0) return "Not recorded on any contract.";
  if (kind === "currency") return `The currency of ${fmtContracts(d.record_total)}.`;
  if (kind === "activity") return `The economic activity of ${fmtContracts(d.record_total)}.`;
  const shown = PART_PHRASE.filter(([k]) => (d.usage_parts[k] ?? 0) > 0);
  const parts = shown.map(([k, say]) => say(d.usage_parts[k]));
  const overlap = shown.reduce((sum, [k]) => sum + d.usage_parts[k], 0) > d.record_total;
  return (
    `Recorded on ${fmtContracts(d.record_total)}: ${joinAnd(parts)}.` +
    (overlap ? " A contract that records it in more than one way counts once." : "")
  );
}

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
            <text key={y} x={x} y={H - 3} fontSize="10" fill="#6f6052" textAnchor="middle" fontFamily="Georgia, serif">
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

/** Review-duplicates worklist for one vocabulary. The matcher proposes; the human
 *  decides. Linking is additive + reversible and never alters the verbatim term. */
function DuplicatesView({ kind, label, noun }: { kind: ReferenceKind; label: string; noun: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<ReferenceDuplicatesResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [canonical, setCanonical] = useState<Record<string, number>>({});
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [showCautions, setShowCautions] = useState(false);

  const reload = useCallback(() => {
    loadReferenceDuplicates(kind)
      .then((res) => {
        setData(res);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, [kind]);

  useEffect(() => {
    setData(null);
    setCanonical({});
    setError(""); // an error from the previous vocabulary does not belong here
    reload();
  }, [reload]);

  const act = async (fn: () => Promise<unknown>) => {
    if (!reviewer.trim()) {
      setError("Enter your initials before linking.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await fn();
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const linkFamily = (family: ReferenceTerm[], canonId: number, rel: "same_as" | "distinct") =>
    act(async () => {
      // same_as is transitive, so the canonical star suffices; "distinct" is
      // NOT — the backend requires EVERY pair decided, so a 3+ family linked
      // only canonical↔member resurfaced forever (finding D5). Write all pairs.
      const pairs: Array<[number, number]> =
        rel === "distinct"
          ? family.flatMap((a, i) => family.slice(i + 1).map((b): [number, number] => [a.id, b.id]))
          : family.filter((t) => t.id !== canonId).map((t): [number, number] => [t.id, canonId]);
      for (const [fromId, toId] of pairs) {
        try {
          await createReferenceLink(kind, {
            reviewer: reviewer.trim(),
            rel,
            from_id: fromId,
            to_id: toId,
          });
        } catch (e) {
          // a pair already decided (e.g. completing a partly-linked family) — skip
          // it. Match the 409 status first; the message text is only a fallback.
          const status = (e as Error & { status?: number }).status;
          if (status !== 409 && !String((e as Error).message).includes("already linked")) throw e;
        }
      }
    });

  const renderFamily = (fam: import("../types").ReferenceDuplicateFamily) => {
    const canonId = canonical[fam.signature] ?? fam.terms[0].id;
    return (
      <li key={fam.signature} className="ref-fam">
        {fam.source === "llm" && (
          <p className="ref-fam-llm">
            <span className="ref-fam-llm-tag">suggested by the computer</span>
            {typeof fam.confidence === "number" && (
              <span className="ref-fam-conf">{Math.round(fam.confidence * 100)}% sure</span>
            )}
            {fam.rationale}
          </p>
        )}
        <div className="ref-fam-terms">
          {fam.terms.map((t) => (
            <button
              key={t.id}
              type="button"
              className={t.id === canonId ? "ref-fam-term is-canon" : "ref-fam-term"}
              title={
                t.id === canonId
                  ? "The spelling the others will point to"
                  : "Click to make this the kept spelling"
              }
              onClick={() => setCanonical((c) => ({ ...c, [fam.signature]: t.id }))}
            >
              <span className="ref-fam-value">{t.value}</span>
              <span className="ref-fam-count muted">
                {usageLabel(t.count)}
              </span>
              {t.id === canonId && <span className="ref-fam-badge">kept spelling</span>}
            </button>
          ))}
        </div>
        <div className="ref-fam-actions">
          <button
            type="button"
            className="pill-button is-primary"
            disabled={busy || !reviewer.trim()}
            title={reviewer.trim() ? undefined : "Type your initials above first"}
            onClick={() => linkFamily(fam.terms, canonId, "same_as")}
          >
            Link as same
          </button>
          <button
            type="button"
            className="pill-button"
            disabled={busy || !reviewer.trim()}
            onClick={() => linkFamily(fam.terms, canonId, "distinct")}
            title={
              reviewer.trim()
                ? "Record that these are NOT the same — stops resurfacing them"
                : "Type your initials above first"
            }
          >
            Not the same
          </button>
        </div>
      </li>
    );
  };

  if (data && !data.available) {
    return (
      <div className="ref-overview">
        <p className="eyebrow">Review duplicates</p>
        <h2>{label}</h2>
        <p className="muted ref-note">
          The duplicate matcher for {label.toLowerCase()} isn’t built yet. Currency is the first one —
          its grammar (coin + rate + place) is mechanical, so matching is safe. Places (with a
          gazetteer) and the others come next.
        </p>
      </div>
    );
  }

  return (
    <div className="ref-dupes">
      <p className="eyebrow">Review duplicates</p>
      <h2>{label} — possibly the same</h2>
      <p className="muted ref-note">
        {kind === "place"
          ? "Exact matches plus reviewed machine suggestions for historic/variant spellings — never a different place or administrative scope (città ≠ contado)."
          : `The matcher folds only spelling (apostrophes, di/a/e, word order) — never ${
              kind === "currency"
                ? "a different number, coin, or place"
                : kind === "title"
                  ? "a different rank, gender, or status"
                  : "a different word, trade, or commodity"
            }.`}{" "}
        Each pair below is a <strong>suggestion</strong>: linking records a reviewed{" "}
        <em>“same as”</em> beside both terms — it never edits or deletes either, and it’s reversible.
      </p>

      <label className="ref-reviewer">
        <span className="db-sort-label">Your initials, needed before linking</span>
        <input
          type="text"
          placeholder="initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        />
      </label>
      {error && <p className="error-text">{error}</p>}

      {!data ? (
        error ? (
          <p>
            <button type="button" className="link-like" onClick={reload}>
              Try again
            </button>
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )
      ) : (
        <>
          {data.note && <p className="muted ref-note ref-dupe-note">{data.note}</p>}
          <p className="ref-block-label">
            Likely the same{data.families.length ? ` (${data.families.length})` : ""}
          </p>
          {data.families.length === 0 ? (
            <p className="muted ref-note">
              {data.note
                ? "No exact matches. Suggestions from the computer have not been prepared for this list yet."
                : "No unresolved candidates."}
            </p>
          ) : (
            <ul className="ref-fam-list">{data.families.map(renderFamily)}</ul>
          )}

          {data.uncertain && data.uncertain.length > 0 && (
            <>
              <p className="ref-block-label">Needs your eye ({data.uncertain.length})</p>
              <p className="muted ref-note ref-uncertain-note">
                Suggestions the computer is less sure about — look before linking.
              </p>
              <ul className="ref-fam-list">{data.uncertain.map(renderFamily)}</ul>
            </>
          )}

          {data.links.length > 0 && (
            <>
              <p className="ref-block-label">Already linked ({data.links.length})</p>
              <ul className="ref-link-list">
                {data.links.map((ln) => (
                  <li key={ln.link_id} className="ref-link">
                    <span className="ref-link-text">
                      {ln.rel === "distinct" ? (
                        <>
                          <span className="ref-fam-value">{ln.from_value}</span>
                          <span className="ref-link-rel">≠</span>
                          <span className="ref-fam-value">{ln.to_value}</span>
                        </>
                      ) : (
                        <>
                          <span className="ref-fam-value">{ln.from_value}</span>
                          <span className="ref-link-rel">→</span>
                          <span className="ref-fam-value">{ln.to_value}</span>
                        </>
                      )}
                      <span className="muted ref-link-by">{ln.created_by}</span>
                    </span>
                    <button
                      type="button"
                      className="link-like"
                      disabled={busy}
                      onClick={() => act(() => revokeReferenceLink(ln.link_id, { reviewer: reviewer.trim() }))}
                    >
                      Undo
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          {data.cautions.length > 0 && (
            <div className="ref-cautions">
              <button type="button" className="link-like" onClick={() => setShowCautions((s) => !s)}>
                {showCautions ? "Hide" : "Show"} look-alikes the matcher kept separate ({data.cautions.length})
              </button>
              {showCautions && (
                <>
                  <p className="muted ref-note">
                    These look similar but differ by a <strong>number</strong> (a different exchange rate) — a
                    real distinction. The matcher deliberately does <em>not</em> propose merging them.
                  </p>
                  <ul className="ref-caution-list">
                    {data.cautions.map((c, i) => (
                      <li key={i} className="ref-caution">
                        {c.terms.map((t, j) => (
                          <span key={t.id}>
                            <button
                              type="button"
                              className="link-like"
                              onClick={() => navigate(`/explore?q=${encodeURIComponent(t.value)}`)}
                            >
                              {t.value}
                            </button>
                            {j < c.terms.length - 1 && <span className="ref-link-rel"> vs </span>}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </>
      )}
      <p className="muted ref-note ref-noun-foot">Curating the {noun} vocabulary.</p>
    </div>
  );
}

const KINDS: ReferenceKind[] = ["place", "title", "currency", "activity"];

/* What the page shows lives in the address, so a term can be linked to and Back
 * returns to it (UI review 2026-10-14, #44): /reference/<vocabulary>/<term id>,
 * plus ?q= (search), ?sort=name, ?unused=1 and ?view=duplicates|map, each left out
 * at its default. Opening a term, a vocabulary or a view adds a history step;
 * typing, sort and "Unused only" replace the current one. */
function referencePath(
  kind: ReferenceKind,
  termId: number | null,
  opts: { q?: string; sort?: string; unused?: boolean; view?: "duplicates" | "map" } = {},
): string {
  const query = new URLSearchParams();
  if (opts.q) query.set("q", opts.q);
  if (opts.sort === "name") query.set("sort", "name");
  if (opts.unused) query.set("unused", "1");
  if (opts.view) query.set("view", opts.view);
  const tail = query.toString();
  return `/reference/${kind}${termId != null ? `/${termId}` : ""}${tail ? `?${tail}` : ""}`;
}

export default function Reference() {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams<{ kind?: string; termId?: string }>();
  const [searchParams] = useSearchParams();
  const kindParam = params.kind;
  const unknownKind = kindParam !== undefined && !KINDS.includes(kindParam as ReferenceKind);
  const kind: ReferenceKind = unknownKind || kindParam === undefined ? "place" : (kindParam as ReferenceKind);
  const termParam = unknownKind ? null : (params.termId ?? null);
  const termId = termParam !== null && /^\d+$/.test(termParam) ? Number(termParam) : null;
  const view = searchParams.get("view");
  const mode: "browse" | "dupes" | "map" =
    view === "duplicates" ? "dupes" : view === "map" && kind === "place" ? "map" : "browse";
  const sort = searchParams.get("sort") === "name" ? "name" : "usage";
  const orphansOnly = searchParams.get("unused") === "1";
  const urlQ = searchParams.get("q") ?? "";
  // The search box keeps its own value (no cursor jumps) and is copied to ?q=.
  const [search, setSearch] = useState(urlQ);
  const writtenQ = useRef(urlQ);
  useEffect(() => {
    if (urlQ !== writtenQ.current) {
      writtenQ.current = urlQ; // Back/Forward or a link changed it
      setSearch(urlQ);
    }
  }, [urlQ]);
  const [rawList, setList] = useState<ReferenceListResponse | null>(null);
  const [rawTerms, setTerms] = useState<ReferenceTerm[]>([]);
  // The rail shows only the open vocabulary's list: after Back across vocabularies
  // the previous one's list never stands in for it while the new one loads.
  const list = rawList?.kind === kind ? rawList : null;
  const terms = list ? rawTerms : [];
  // The page's current place, for the search timer and the Browse tab.
  const live = useRef({ mode: "browse" as "browse" | "dupes" | "map", termId: null as number | null });
  const lastBrowse = useRef<{ kind: ReferenceKind; path: string } | null>(null);
  const [detail, setDetail] = useState<ReferenceRecordsResponse | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [detailFailed, setDetailFailed] = useState(false);
  const [detailRetry, setDetailRetry] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  const beginList = useLatest();
  const beginDetail = useLatest();
  const [error, setError] = useState("");
  const debounce = useRef<number | undefined>(undefined);

  const vocab = useMemo(() => VOCABS.find((v) => v.kind === kind)!, [kind]);

  const runList = useCallback(
    (k: ReferenceKind, q: string, s: string, orphans: boolean, offset: number) => {
      const fresh = beginList();
      loadReference(k, { q, sort: s, orphansOnly: orphans, offset })
        .then((res) => {
          if (!fresh()) return; // a stale places page must not append under Titles
          setList(res);
          setTerms((prev) => (offset > 0 ? [...prev, ...res.terms] : res.terms));
          setError("");
        })
        .catch((err: Error) => fresh() && setError(err.message));
    },
    [beginList],
  );

  useEffect(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => {
      runList(kind, search, sort, orphansOnly, 0);
      if (search !== writtenQ.current) {
        // Where the page is now (a click may have moved it since the typing began).
        const { mode: nowMode, termId: nowTerm } = live.current;
        writtenQ.current = search;
        navigate(
          referencePath(kind, nowMode === "browse" ? nowTerm : null, {
            q: search, sort, unused: orphansOnly, view: nowMode === "dupes" ? "duplicates" : nowMode === "map" ? "map" : undefined,
          }),
          { replace: true },
        );
      }
    }, 200);
    return () => window.clearTimeout(debounce.current);
    // the address is written from live.current; mode and termId must not re-run the list
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, search, sort, orphansOnly, runList]);

  useEffect(() => {
    setLoadingMore(false); // a term switch abandons any in-flight "Load more"
    setMoreFailed(false);
    setDetailFailed(false);
    setNotFound(false);
    if (termId === null) {
      beginDetail();
      setDetail(null);
      if (termParam !== null) setNotFound(true); // "/reference/place/abc"
      return;
    }
    setDetail(null); // never render term A's numbers under term B's heading
    const fresh = beginDetail();
    loadReferenceRecords(kind, termId)
      .then((d) => fresh() && setDetail(d))
      .catch((err: Error & { status?: number }) => {
        if (!fresh()) return;
        setDetail(null);
        if (err.status === 404) setNotFound(true);
        else setDetailFailed(true);
      });
  }, [kind, termId, termParam, beginDetail, detailRetry]);

  const loadMoreRecords = () => {
    if (termId === null || !detail) return;
    const fresh = beginDetail(); // a term switch (the effect above) invalidates this append
    setLoadingMore(true);
    setMoreFailed(false);
    loadReferenceRecords(kind, termId, detail.records.length)
      .then((more) => {
        if (!fresh()) return;
        setDetail((prev) => {
          if (!prev) return prev;
          const seen = new Set(prev.records.map((r) => r.row_id));
          return {
            ...prev,
            record_total: more.record_total,
            records: [...prev.records, ...more.records.filter((r) => !seen.has(r.row_id))],
          };
        });
      })
      .catch(() => fresh() && setMoreFailed(true))
      .finally(() => fresh() && setLoadingMore(false));
  };

  // The open term's text: the server's (a link has no rail row), the rail's meanwhile.
  const termValue = detail?.value ?? terms.find((t) => t.id === termId)?.value ?? "";
  const filters = { q: search, sort, unused: orphansOnly };
  live.current = { mode, termId };
  if (mode === "browse" && !unknownKind) lastBrowse.current = { kind, path: location.pathname + location.search };
  const openTerm = (id: number) => {
    if (id !== termId) navigate(referencePath(kind, id, filters));
  };
  const switchKind = (k: ReferenceKind) => {
    if (k === kind && !unknownKind) return;
    // a new vocabulary starts with an empty search; the sort is kept; map is place-only
    window.clearTimeout(debounce.current);
    writtenQ.current = "";
    setSearch("");
    navigate(
      referencePath(k, null, {
        sort, view: mode === "dupes" ? "duplicates" : mode === "map" && k === "place" ? "map" : undefined,
      }),
    );
  };
  const setMode = (m: "browse" | "dupes" | "map") => {
    if (m === mode) return;
    if (m === "browse") {
      // back to the list as it was: its search, sort, filter and open term
      const back = lastBrowse.current;
      navigate(back && back.kind === kind ? back.path : referencePath(kind, null, filters));
      return;
    }
    navigate(referencePath(m === "map" ? "place" : kind, null, { view: m === "map" ? "map" : "duplicates" }));
  };
  const setSort = (value: string) =>
    navigate(referencePath(kind, termId, { ...filters, sort: value }), { replace: true });
  const setOrphansOnly = (value: boolean) =>
    navigate(referencePath(kind, termId, { ...filters, unused: value }), { replace: true });

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

          <div className="db-tabs ref-mode">
            <button
              type="button"
              className={mode === "browse" ? "db-tab is-active" : "db-tab"}
              onClick={() => setMode("browse")}
            >
              Browse
            </button>
            <button
              type="button"
              className={mode === "dupes" ? "db-tab is-active" : "db-tab"}
              onClick={() => setMode("dupes")}
            >
              Review duplicates
            </button>
            {kind === "place" && (
              <button
                type="button"
                className={mode === "map" ? "db-tab is-active" : "db-tab"}
                onClick={() => setMode("map")}
              >
                Map
              </button>
            )}
          </div>

          {mode === "browse" && (
          <input
            className="db-search"
            type="search"
            placeholder={`Search ${vocab.label.toLowerCase()}…`}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          )}

          {mode === "browse" && (
          <>
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

          <p className={error ? "db-count error-text" : "db-count muted"}>
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
          </>
          )}
        </div>

        {mode === "browse" && (
        <ul className="db-results">
          {terms.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                className={termId === t.id ? "db-result is-active" : "db-result"}
                onClick={() => openTerm(t.id)}
              >
                <span className="db-result-title">{t.value}</span>
                <span className="db-result-meta muted" title={vocab.hint}>
                  {usageLabel(t.count)}
                </span>
              </button>
            </li>
          ))}
          {list && terms.length === 0 && !error && <li className="db-empty muted">No terms match.</li>}
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
        )}
      </aside>

      <section className="db-detail">
        {mode === "map" ? (
          <PlaceMap onPick={(p) => navigate(referencePath("place", p.id))} />
        ) : mode === "dupes" ? (
          <DuplicatesView kind={kind} label={vocab.label} noun={vocab.noun} />
        ) : unknownKind ? (
          <div className="ref-term">
            <p className="eyebrow">Reference</p>
            <h2>Not a list on this site</h2>
            <p className="muted ref-note">
              This link names a list that does not exist (“{kindParam}”). The lists are Places, Titles, Currencies and
              Economic activities.
            </p>
          </div>
        ) : notFound ? (
          <div className="ref-term">
            <p className="eyebrow">{vocab.singular}</p>
            <h2>Not in the list</h2>
            <p className="muted ref-note">
              This link points to {vocab.aNoun} that is not in the list of {vocab.label.toLowerCase()}. The list may
              have changed since the link was made.
            </p>
            <button type="button" className="link-like" onClick={() => navigate(referencePath(kind, null))}>
              Show all {vocab.label.toLowerCase()}
            </button>
          </div>
        ) : termId === null ? (
          <div className="ref-overview">
            <p className="eyebrow">Most-referenced</p>
            <h2>{vocab.label} by usage</h2>
            <p className="muted ref-note">
              How many contracts record each term. {vocab.hint} Select a term to read its contracts.
            </p>
            {list && list.top.length > 0 ? (
              <UsageBars top={list.top} onPick={(value) => setSearch(value)} />
            ) : list ? (
              <p className="muted">No usage to chart.</p>
            ) : null}
          </div>
        ) : (
          <div className="ref-term">
            <div className="page-head">
              <div>
                <p className="eyebrow">{vocab.singular} · verbatim term</p>
                <h2 className="ref-term-value">{termValue || "\u00a0"}</h2>
              </div>
              <CopyLinkButton path={referencePath(kind, termId)} title={`Copy a link to this ${vocab.noun}, to send or to reopen later`} />
            </div>
            {detail && <p className="muted ref-term-meta">{usageSentence(kind, detail)}</p>}
            {detail && (
              <p className="muted ref-narrative-line">
                {detail.narrative_mentions > 0 ? (
                  <>
                    The text “{termValue}” occurs in the narrative text of{" "}
                    {[
                      detail.narrative_contracts > 0 ? fmtContracts(detail.narrative_contracts) : "",
                      detail.narrative_sub_contracts > 0
                        ? `${detail.narrative_sub_contracts.toLocaleString()} sub-contract${
                            detail.narrative_sub_contracts === 1 ? "" : "s"
                          }`
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" and ")}
                    . The count is a plain text match and can include longer words that contain it.{" "}
                    <button
                      type="button"
                      className="link-like"
                      onClick={() => navigate(`/explore?q=${encodeURIComponent(termValue)}`)}
                    >
                      view in corpus search →
                    </button>
                  </>
                ) : (
                  `The text “${termValue}” does not occur in any narrative.`
                )}
              </p>
            )}

            {detail?.resolution && (
              <div className="ref-resolution">
                <p className="ref-block-label">
                  Resolved <span className="ref-fam-llm-tag">by the computer</span>
                </p>
                <p className="ref-resolution-main">
                  {detail.resolution.modern_name}
                  <span className="ref-resolution-type">
                    {detail.resolution.feature_type}
                    {detail.resolution.country ? ` · ${detail.resolution.country}` : ""}
                  </span>
                  <span className="ref-fam-conf">
                    {Math.round(detail.resolution.confidence * 100)}% sure
                  </span>
                </p>
                {detail.resolution.note && (
                  <p className="muted ref-resolution-note">{detail.resolution.note}</p>
                )}
                {typeof detail.resolution.lat === "number" && typeof detail.resolution.lon === "number" && (
                  <p className="muted ref-resolution-coords">
                    Coordinates {detail.resolution.lat.toFixed(3)}, {detail.resolution.lon.toFixed(3)}
                    {detail.resolution.geo_source === "country-centroid" ? " (approx.)" : ""}{" "}
                    <a
                      className="link-like"
                      href={`https://www.openstreetmap.org/?mlat=${detail.resolution.lat}&mlon=${detail.resolution.lon}#map=8/${detail.resolution.lat}/${detail.resolution.lon}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      map →
                    </a>
                  </p>
                )}
                <p className="muted ref-resolution-foot">
                  Suggested by the computer from the spelling as written — not authoritative; the original term stands.
                </p>
              </div>
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
                    <li className="db-load-more">
                      <p className="muted ref-records-count">
                        Showing {detail.records.length.toLocaleString()} of {detail.record_total.toLocaleString()}.
                        {moreFailed ? " The next contracts did not load; please try again." : ""}
                      </p>
                      <button
                        type="button"
                        className="db-load-more-btn"
                        disabled={loadingMore}
                        onClick={loadMoreRecords}
                      >
                        Load more{" "}
                        <span className="db-load-more-count">
                          ({(detail.record_total - detail.records.length).toLocaleString()} more)
                        </span>
                      </button>
                    </li>
                  )}
                </ul>
              ) : (
                <p className="muted">
                  {detail ? (
                    `No contract records this term as ${vocab.aNoun}. It is in the list of terms but not linked to any record.`
                  ) : detailFailed ? (
                    <>
                      The contracts for this term did not load.{" "}
                      <button type="button" className="link-like" onClick={() => setDetailRetry((n) => n + 1)}>
                        Try again
                      </button>
                    </>
                  ) : (
                    "Loading…"
                  )}
                </p>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
