import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { loadDbFacets, searchDb, searchGlobal } from "../api";
import DistributionRibbon from "../components/DistributionRibbon";
import { isToolHidden } from "../features";
import type { SearchResponse, SearchResult } from "../types";

type Bin = { decade: number; count: number };

/* The home page is the project's front door: a scholarly introduction leads,
 * and the functionality (search + the tool cards) sits in an "Explore" band
 * below the essay. Quick lookups still live in the global nav search; the query
 * here lives in the URL (?q=) so searches stay shareable.
 */

const TOOLS = [
  {
    to: "/database",
    key: "database",
    title: "Browse & edit the database",
    blurb: "Browse and edit database records alongside their Word summaries, and manuscript pages.",
  },
  {
    to: "/reconcile",
    key: "reconcile",
    title: "Attach Word summaries",
    blurb: "Fix unconfirmed Word ↔ database links.",
  },
];

const TIMELINE = [
  { year: "1408", note: "Recognized in Florentine law" },
  { year: "1445", note: "Earliest surviving register" },
  { year: "1808", note: "Registration abolished" },
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
  const [stats, setStats] = useState<{ contracts: number; acts: number; people: number } | null>(null);
  const [hist, setHist] = useState<{ contract: Bin[]; sub: Bin[] } | null>(null);
  const debounce = useRef<number | undefined>(undefined);

  // Live corpus counts for the figure line (cheap COUNT(*) per table).
  useEffect(() => {
    Promise.all([searchDb("contract", ""), searchDb("sub_contract", ""), searchDb("person", "")])
      .then(([c, s, p]) => setStats({ contracts: c.total, acts: s.total, people: p.total }))
      .catch(() => setStats(null));
  }, []);

  // Per-decade histograms for the distribution ribbon.
  useEffect(() => {
    Promise.all([loadDbFacets("contract"), loadDbFacets("sub_contract")])
      .then(([c, s]) => setHist({ contract: c.year_histogram, sub: s.year_histogram }))
      .catch(() => setHist(null));
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
      <div className="home">
        <header className="home-masthead">
          <h1 className="home-title">
            <span className="home-acro">Flor</span>ence <span className="home-acro">Acco</span>mandite
          </h1>
        </header>

        <p className="home-lede">
          This website hosts a relational database that stores what is likely the longest and most
          homogenous archival series of business contracts from pre-industrial Europe. In 1408, the
          city-state of Florence recognized the legal validity of a contract called{" "}
          <span className="term">accomandita</span> (pl. accomandite), known in English as a{" "}
          <span className="term">limited partnership</span>. Soon after, Florence mandated the central
          registration of all such contracts signed in the territories subjected to its jurisdiction,
          regardless of the location where the firm was to operate. Those central registers are now
          preserved starting from 1445 (the first register is lost) and until the abolition of that
          system of registration in 1808.
        </p>

        <ol className="home-timeline">
          {TIMELINE.map((point) => (
            <li key={point.year}>
              <span className="home-tl-year">{point.year}</span>
              <span className="home-tl-note">{point.note}</span>
            </li>
          ))}
        </ol>

        {stats && (
          <p className="home-figures">
            <span className="home-fig-num">{stats.contracts.toLocaleString()}</span> accomandite
            <span className="home-fig-dot">·</span>
            <span className="home-fig-num">{stats.acts.toLocaleString()}</span> later acts
            <span className="home-fig-dot">·</span>
            <span className="home-fig-num">{stats.people.toLocaleString()}</span> people
          </p>
        )}

        {hist && (hist.contract.length > 0 || hist.sub.length > 0) && (
          <section className="home-ribbon">
            <div className="home-ribbon-head">
              <h2>Registrations by decade</h2>
              <div className="home-ribbon-legend">
                <span className="legend-item">
                  <span className="legend-swatch is-acc" /> accomandite
                </span>
                <span className="legend-item">
                  <span className="legend-swatch is-acts" /> later acts
                </span>
              </div>
            </div>
            <DistributionRibbon contract={hist.contract} sub={hist.sub} />
          </section>
        )}

        <article className="home-essay">
          <p>A limited partnership involves two sets of partners:</p>

          <div className="home-callout">
            <div className="home-callout-item">
              <h3>General partner(s)</h3>
              <p>
                manage the firm, invest their labor and/or capital, and are liable for all its debt,
                including with their family assets.
              </p>
            </div>
            <div className="home-callout-item">
              <h3>External investor(s)</h3>
              <p>
                invest a specific sum for a set period of time (usually 3 to 5 years), can only lose as
                much as they invest, and are not allowed to interfere with the firm's management.
              </p>
            </div>
          </div>

          <p>
            Then as today, limited partnerships operate under a collective firm's name and establish a
            separate fund for their operations with clearly defined rules for the sharing of profits and
            losses among investors.
          </p>

          <p>
            At least since the publication in 1889 of <span className="term">Max Weber</span>'s
            dissertation, <cite>The History of Commercial Partnerships in the Middle Ages</cite> (English
            translation and critical edition by Lutz Kaelber [Rowman and Littlefield, 2003]), social and
            economic theorists have attributed great importance to limited partnerships in severing the
            link between family and business, which characterizes "traditional societies," and fostering
            the development of impersonal credit markets. The degree of impersonality of credit markets is
            a hallmark of "modernity" and <span className="term">limited liability</span> the legal clause
            that allows for it to develop. Thanks to that legal clause, investors can risk placing their
            savings in the hands of individuals whom they do not know in person, as long as they possess
            adequate information about those individuals and there exist tribunals that enforce property
            rights equitably. Limited liability is thus central to most accounts of the rise of capitalism,
            so much so that the modern corporation (of which the first examples emerged in the Netherlands
            and England at the beginning of the seventeenth century) is often portrayed as a development of
            earlier limited partnerships such as those that existed in Tuscany.
          </p>

          <blockquote className="home-pullquote">
            In spite of the significance that is attributed to limited partnerships in the historical arc
            of European capitalism, we know very little about who used them, when, and for what purposes.
          </blockquote>

          <p>
            This online platform is designed to offer new answers to these fundamental questions.{" "}
            <a href="#explore">Explore the contracts.</a>
          </p>
        </article>

        <section id="explore" className="home-explore">
          <h2>Explore the contracts</h2>
          <div className="hub-search">
            <input
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
            Niccolò).
          </p>

          {hasQuery ? (
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
          ) : (
            <div className="hub-grid">
              {TOOLS.map((tool) =>
                isToolHidden(tool.key) ? (
                  <div className="hub-card is-disabled" key={tool.to} aria-disabled="true">
                    <div className="hub-card-top">
                      <h2>{tool.title}</h2>
                      <span className="hub-soon-tag">Coming soon</span>
                    </div>
                    <p>{tool.blurb}</p>
                  </div>
                ) : (
                  <Link className="hub-card" key={tool.to} to={tool.to}>
                    <div className="hub-card-top">
                      <h2>{tool.title}</h2>
                    </div>
                    <p>{tool.blurb}</p>
                  </Link>
                ),
              )}
            </div>
          )}
        </section>

        <footer className="home-colophon">
          <p className="home-colophon-name">Francesca Trivellato</p>
          <p className="home-colophon-title">
            Andrew W. Mellon Professor · School of Historical Studies · Early Modern Europe
          </p>
          <p className="home-colophon-links">
            <a href="mailto:ft@ias.edu">ft@ias.edu</a>
            <span className="home-colophon-sep"> · </span>
            <a href="https://www.ias.edu/scholars/trivellato" target="_blank" rel="noreferrer">
              Institute for Advanced Study ↗
            </a>
          </p>
          <p className="home-colophon-cite">
            <span className="home-colophon-cite-label">How to cite</span>
            Francesca Trivellato,{" "}
            <cite>Florence Accomandite: a database of Tuscan limited partnerships, 1445–1808</cite>.
            Institute for Advanced Study, [year]. [stable URL], accessed [date].
          </p>
        </footer>
      </div>
    </div>
  );
}
