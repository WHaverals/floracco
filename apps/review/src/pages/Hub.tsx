import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { loadDbFacets, searchDb } from "../api";
import DistributionRibbon from "../components/DistributionRibbon";
import { isToolHidden } from "../features";

type Bin = { decade: number; count: number };

/* The home page is the project's front door: a scholarly introduction leads,
 * then an "Explore the contracts" band whose search routes to the dedicated
 * /explore page (results shown there, not buried under the essay).
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

export default function Hub() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<{ contracts: number; acts: number; people: number } | null>(null);
  const [hist, setHist] = useState<{ contract: Bin[]; sub: Bin[] } | null>(null);
  const [query, setQuery] = useState("");

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

  const goExplore = () => navigate(`/explore${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ""}`);

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
          </p>
        </article>

        <section className="home-explore">
          <h2>Explore the contracts</h2>
          <form
            className="hub-search"
            onSubmit={(event) => {
              event.preventDefault();
              goExplore();
            }}
          >
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="firm, person, place, activity, narrative text, or an act number…"
              aria-label="Search the corpus"
            />
          </form>
          <p className="muted hub-search-hint">
            Press Enter to search the full corpus · words combine with AND · "quotes" for exact
            phrases.
          </p>
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
