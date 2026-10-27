import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { searchGlobal } from "../api";
import { useLatest } from "../utils/latest";
import { isToolHidden } from "../features";
import type { SearchResponse, SearchResult } from "../types";

/* The corpus search, on its own page: search-first, results at the top. Reached
 * from the home page's "Explore the contracts" link, the global nav search, and
 * the Reference tab's "view in corpus search". The query lives in ?q= so results
 * stay shareable and deep-linkable.
 */

const TOOLS = [
  {
    to: "/database",
    key: "database",
    title: "Browse & edit the database",
    blurb: "Records beside their Word summaries and manuscript pages.",
  },
  {
    to: "/reconcile",
    key: "reconcile",
    title: "Attach Word summaries",
    blurb: "Fix unconfirmed Word ↔ database links.",
  },
];

/** How many more hits each "Show more" asks the server for. */
const SEARCH_PAGE = 50;

const KIND_ROUTE: Record<SearchResult["kind"], string> = {
  contract: "contract",
  sub_contract: "sub_contract",
  person: "person",
};

const KIND_NOUN: Record<SearchResult["kind"], string> = {
  contract: "Contract",
  sub_contract: "Sub-contract",
  person: "Person entry",
};

function Snippet({ text }: { text: string }) {
  const parts = text.split(/[«»]/);
  return (
    <span className="search-snippet">
      {parts.map((part, index) => (index % 2 === 1 ? <mark key={index}>{part}</mark> : part))}
    </span>
  );
}

export default function Explore() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const [response, setResponse] = useState<SearchResponse | null>(null);
  // The group shown beyond its first five, and how many of its hits to show.
  const [expanded, setExpanded] = useState<{ kind: string; limit: number } | null>(null);
  const [searching, setSearching] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounce = useRef<number | undefined>(undefined);
  const beginSearch = useLatest();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (query.trim().length < 2) {
      beginSearch(); // kill any in-flight response for the cleared query
      setResponse(null);
      setExpanded(null);
      setSearching(false);
      setFailed(false);
      return;
    }
    setSearching(true);
    debounce.current = window.setTimeout(() => {
      const fresh = beginSearch();
      searchGlobal(query.trim(), expanded?.kind ?? "", expanded?.limit ?? 0)
        .then((r) => {
          if (!fresh()) return;
          setResponse(r);
          setFailed(false);
        })
        .catch(() => {
          if (!fresh()) return;
          setResponse(null);
          setFailed(true);
        })
        .finally(() => fresh() && setSearching(false));
    }, 250);
    return () => window.clearTimeout(debounce.current);
  }, [query, expanded, attempt, beginSearch]);

  const hasQuery = query.trim().length >= 2;
  const nonEmptyGroups = useMemo(() => (response?.groups ?? []).filter((g) => g.total > 0), [response]);

  return (
    <div className="hub">
      <header className="explore-header">
        <p className="eyebrow">Florentine Accomandite</p>
        <h1>Explore the contracts</h1>
        <div className="hub-search">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => {
              setExpanded(null);
              setSearchParams(event.target.value ? { q: event.target.value } : {}, { replace: true });
            }}
            placeholder="firm, person, place, activity, narrative text, or a number…"
            aria-label="Search the corpus"
          />
        </div>
        <p className="muted hub-search-hint">
          Words combine with AND · "quotes" for exact phrases · diacritics optional (niccolo finds
          Niccolò) · a number also finds the contract, sub-contract and person entry that carry it.
        </p>
      </header>

      {hasQuery ? (
        <div className="search-results" aria-busy={searching}>
          {failed && !searching && (
            <p className="notice error">
              The search could not be completed.{" "}
              <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </p>
          )}
          {searching && !response && <p className="muted">Searching…</p>}
          {response && response.id_jumps.length > 0 && (
            <div className="search-jumps">
              {response.id_jumps.map((jump) => (
                <button
                  type="button"
                  key={`${jump.kind}-${jump.ref}`}
                  className="search-jump"
                  onClick={() => navigate(`/database/${KIND_ROUTE[jump.kind]}/${jump.ref}`)}
                >
                  → {KIND_NOUN[jump.kind]} <strong>{jump.ref}</strong>
                  {jump.title !== `${KIND_NOUN[jump.kind]} ${jump.ref}` ? ` · ${jump.title}` : null}
                  {jump.meta ? <span className="muted"> · {jump.meta}</span> : null}
                </button>
              ))}
            </div>
          )}

          {response && response.total === 0 && (
            <div className="search-empty">
              <p>
                {response.id_jumps.length > 0
                  ? `The records above carry the number ${query.trim()}; no record mentions it in its text.`
                  : query.trim().split(/\s+/).length > 1
                    ? "No record matches all of these terms."
                    : "No record matches this search."}
              </p>
              {response.term_counts && (
                <p className="muted">
                  Separately:{" "}
                  {response.term_counts.map((t, i) => (
                    <span key={t.term}>
                      {i > 0 ? " · " : ""}
                      <strong>{t.term}</strong>: {t.count.toLocaleString()}
                    </span>
                  ))}{" "}
                  · together: 0. Try fewer terms.
                </p>
              )}
            </div>
          )}

          {nonEmptyGroups.map((group) => (
            <section key={group.kind} className="search-group">
              <h3>
                {group.label} <span className="muted">({group.total.toLocaleString()})</span>
              </h3>
              <ul>
                {group.results.map((result) => (
                  <li key={`${result.kind}-${result.ref}`}>
                    <button
                      type="button"
                      className="search-result"
                      onClick={() => navigate(`/database/${KIND_ROUTE[result.kind]}/${result.ref}`)}
                    >
                      <span className="search-result-title">
                        {result.title || `${KIND_NOUN[result.kind]} ${result.ref}`}
                      </span>
                      {result.meta && <span className="search-result-meta muted">{result.meta}</span>}
                      <Snippet text={result.snippet} />
                    </button>
                  </li>
                ))}
              </ul>
              {group.total > group.results.length && (
                <p className="muted search-more">
                  Showing {group.results.length.toLocaleString()} of {group.total.toLocaleString()}.{" "}
                  <button
                    type="button"
                    className="link-like"
                    disabled={searching}
                    onClick={() => setExpanded({ kind: group.kind, limit: group.results.length + SEARCH_PAGE })}
                  >
                    Show {Math.min(SEARCH_PAGE, group.total - group.results.length).toLocaleString()} more
                  </button>
                </p>
              )}
            </section>
          ))}
        </div>
      ) : (
        <div className="hub-grid">
          {TOOLS.filter((tool) => !isToolHidden(tool.key)).map((tool) => (
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
