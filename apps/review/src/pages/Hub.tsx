import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { searchGlobal } from "../api";
import type { SearchResponse, SearchResult } from "../types";

/* The home page is search-first: the platform's daily life is "find this
 * contract / firm / person", so the box comes before the tool cards. The query
 * lives in the URL (?q=) so searches are shareable and the nav box on other
 * pages can route here.
 */

const TOOLS = [
  {
    to: "/database",
    title: "Browse & edit the database",
    blurb: "Records with their narratives, Word summaries, manuscript pages — and the add/fix tools.",
  },
  {
    to: "/reconcile",
    title: "Attach Word summaries",
    blurb: "Work through the remaining unconfirmed Word↔database links.",
  },
  {
    to: "/corrections",
    title: "Flagged for correction",
    blurb: "Prefiltered queue of fields that may need fixing.",
  },
  {
    to: "/dashboard",
    title: "Progress & exports",
    blurb: "Overview of review work and download logs.",
  },
];

const KIND_ROUTE: Record<SearchResult["kind"], string> = {
  contract: "contract",
  sub_contract: "sub_contract",
  person: "person",
};

/** Render a snippet with «matched terms» as <mark>. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/[«»]/);
  return (
    <span className="search-snippet">
      {parts.map((part, index) => (index % 2 === 1 ? <mark key={index}>{part}</mark> : part))}
    </span>
  );
}

export default function Hub() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [expanded, setExpanded] = useState<string>("");
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounce = useRef<number | undefined>(undefined);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (query.trim().length < 2) {
      setResponse(null);
      setExpanded("");
      return;
    }
    setSearching(true);
    debounce.current = window.setTimeout(() => {
      searchGlobal(query.trim(), expanded)
        .then(setResponse)
        .catch(() => setResponse(null))
        .finally(() => setSearching(false));
    }, 250);
    return () => window.clearTimeout(debounce.current);
  }, [query, expanded]);

  const hasQuery = query.trim().length >= 2;
  const nonEmptyGroups = useMemo(
    () => (response?.groups ?? []).filter((g) => g.total > 0),
    [response],
  );

  return (
    <div className="hub">
      <header className="hub-header">
        <p className="eyebrow">Florentine Accomandite · review platform</p>
        <h1>Search the corpus</h1>
        <div className="hub-search">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => {
              setExpanded("");
              setSearchParams(event.target.value ? { q: event.target.value } : {}, { replace: true });
            }}
            placeholder="firm, person, place, activity, narrative text, or an act number…"
            aria-label="Search the corpus"
          />
        </div>
        <p className="muted hub-search-hint">
          Words combine with AND · "quotes" for exact phrases · diacritics optional (niccolo finds
          Niccolò) · searches exact spellings — try historical variants yourself.
        </p>
      </header>

      {hasQuery && (
        <div className="search-results" aria-busy={searching}>
          {response && response.id_jumps.length > 0 && (
            <div className="search-jumps">
              {response.id_jumps.map((jump) => (
                <button
                  type="button"
                  key={`${jump.kind}-${jump.ref}`}
                  className="search-jump"
                  onClick={() => navigate(`/database/${KIND_ROUTE[jump.kind]}/${jump.ref}`)}
                >
                  → {jump.kind === "sub_contract" ? "Act" : jump.kind === "person" ? "Person" : "Contract"}{" "}
                  <strong>{jump.ref}</strong> · {jump.title}
                  {jump.meta ? <span className="muted"> · {jump.meta}</span> : null}
                </button>
              ))}
            </div>
          )}

          {response && response.total === 0 && (
            <div className="search-empty">
              <p>No record matches all of these terms.</p>
              {response.term_counts && (
                <p className="muted">
                  Separately:{" "}
                  {response.term_counts.map((t, i) => (
                    <span key={t.term}>
                      {i > 0 ? " · " : ""}
                      <strong>{t.term}</strong>: {t.count.toLocaleString()}
                    </span>
                  ))}{" "}
                  · together: 0 — these never co-occur in one record; try fewer terms.
                </p>
              )}
            </div>
          )}

          {nonEmptyGroups.map((group) => (
            <section key={group.kind} className="search-group">
              <h2>
                {group.label} <span className="muted">({group.total.toLocaleString()})</span>
              </h2>
              <ul>
                {group.results.map((result) => (
                  <li key={`${result.kind}-${result.ref}`}>
                    <button
                      type="button"
                      className="search-result"
                      onClick={() => navigate(`/database/${KIND_ROUTE[result.kind]}/${result.ref}`)}
                    >
                      <span className="search-result-title">
                        {result.title || `${group.label.slice(0, -1)} ${result.ref}`}
                      </span>
                      {result.meta && <span className="search-result-meta muted">{result.meta}</span>}
                      <Snippet text={result.snippet} />
                    </button>
                  </li>
                ))}
              </ul>
              {group.total > group.results.length && (
                <button type="button" className="link-like" onClick={() => setExpanded(group.kind)}>
                  Show all {group.total.toLocaleString()} {group.label.toLowerCase()}
                </button>
              )}
            </section>
          ))}
        </div>
      )}

      {!hasQuery && (
        <div className="hub-grid">
          {TOOLS.map((tool) => (
            <Link className="hub-card" key={tool.to} to={tool.to}>
              <div className="hub-card-top">
                <h2>{tool.title}</h2>
              </div>
              <p>{tool.blurb}</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
