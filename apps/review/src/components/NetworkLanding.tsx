import { useEffect, useState, type MouseEvent, type ReactElement } from "react";
import { Link, useNavigate } from "react-router-dom";
import { TimeChart } from "./FindViews";
import { KIND_WORDS } from "./NetworkFigure";
import { loadNetworkLanding } from "../api";
import type {
  NetworkLanding as Landing,
  NetworkLandingEntry,
  NetworkLandingFigure,
  NetworkLandingList,
  NetworkLandingPairRow,
  NetworkLandingWidestRow,
} from "../types";
import { periodLabel } from "../utils/findView";

/* The corpus before an entry is chosen (docs/network/next_2026-11-01.md §4.3;
 * the build contract in docs/network/step2b_contract.md §4), in the form of
 * Find's landing: the answer line, the five standing figures with their
 * definition and the last edit logged to what they count, the entries active
 * by decade on the house axis, and two lists a row of which opens a circle.
 * Every word and number is the server's (GET /api/network/landing); the page
 * composes only the row cells and the hover rows. In development, `?fixture=1`
 * reads the hand-written fixture instead of asking the server. */

// The first of the validated series colours (FindViews.tsx), on the chart surface.
const BAR = "#2a78d6";
// The hover rows that are not drawn carry no key.
const NO_KEY = "transparent";
const WAYS_IN = "Ways in";
const ALL_ROWS = "All 30";
const TABLE_FOLD = "The figures as a table";
const UNDATED = "no registration date";
/** The rows shown before the fold. */
const SHOWN = 10;

/** "1670–1720", "1669" for one year, "no registration date" when none. */
function yearsCell(first: number | null, last: number | null): string {
  if (first === null || last === null) return UNDATED;
  return first === last ? String(first) : `${first}–${last}`;
}

/** The stored names carried by two or more distinct entries in one list (the
 * rule of duplicateNames in utils/networkSpec.ts; an entry in two pair rows
 * is one entry). */
function namesakes(entries: NetworkLandingEntry[]): Set<string> {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const seen = new Set<string>();
  const twice = new Set<string>();
  for (const entry of byId.values()) (seen.has(entry.name) ? twice : seen).add(entry.name);
  return twice;
}

/** The name as stored, and "di {father_mother}" only when another row in the
 * list carries the same stored name (the rule of nodeLabel). */
function label(entry: NetworkLandingEntry, duplicates: Set<string>): string {
  const father = entry.father_mother?.trim();
  return duplicates.has(entry.name) && father ? `${entry.name} di ${father}` : entry.name;
}

function Figure({ figure }: { figure: NetworkLandingFigure }) {
  return (
    <li className="find-standing">
      <span className="find-standing-label">{figure.label}</span>
      <span className="find-standing-value">{figure.value}</span>
      {figure.detail && <span className="find-standing-detail">{figure.detail}</span>}
      <span className="find-standing-note">{figure.definition}</span>
      <span className="find-standing-note">{figure.as_of}</span>
    </li>
  );
}

function Name({ entry, duplicates }: { entry: NetworkLandingEntry; duplicates: Set<string> }) {
  return (
    <>
      {label(entry, duplicates)} <span className="nw-side-id">entry {entry.id}</span>
      {entry.kind !== "person" && <span className="nw-landing-kind"> · {KIND_WORDS[entry.kind]}</span>}
    </>
  );
}

/** The whole row opens the circle; a click on the name's own link is left to it. */
function useRowLink() {
  const navigate = useNavigate();
  return (to: string) => ({
    className: "nw-landing-row",
    onClick: (event: MouseEvent<HTMLTableRowElement>) => {
      if ((event.target as HTMLElement).closest("a")) return;
      navigate(to);
    },
  });
}

function widestAddress(row: NetworkLandingWidestRow): string {
  return `/network/person/${row.id}?steps=1`;
}

function pairAddress(row: NetworkLandingPairRow): string {
  return `/network/person/${row.a.id}?steps=1&select=pair:${row.a.id}.${row.b.id}`;
}

function WidestTable({ rows, duplicates }: { rows: NetworkLandingWidestRow[]; duplicates: Set<string> }) {
  const rowLink = useRowLink();
  return (
    <div className="an-table-wrap">
      <table className="an-table nw-landing-table">
        <thead>
          <tr>
            <th>Entry</th>
            <th className="num">Co-signers</th>
            <th className="num">Acts</th>
            <th>Years</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} {...rowLink(widestAddress(row))}>
              <td>
                <Link to={widestAddress(row)} className="nw-landing-link">
                  <Name entry={row} duplicates={duplicates} />
                </Link>
              </td>
              <td className="num">
                {row.cosigners.toLocaleString("en-US")}
                {row.largest_act && (
                  <span className="nw-landing-largest">
                    , {row.largest_act.brings.toLocaleString("en-US")} of them on act {row.largest_act.id}, {row.largest_act.year}
                  </span>
                )}
              </td>
              <td className="num">{row.hub ? `on ${row.acts.toLocaleString("en-US")} acts` : row.acts.toLocaleString("en-US")}</td>
              <td className={row.first_year === null ? "muted" : undefined}>{yearsCell(row.first_year, row.last_year)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PairsTable({ rows, duplicates }: { rows: NetworkLandingPairRow[]; duplicates: Set<string> }) {
  const rowLink = useRowLink();
  return (
    <div className="an-table-wrap">
      <table className="an-table nw-landing-table">
        <thead>
          <tr>
            <th>Entries</th>
            <th className="num">Acts together</th>
            <th>Years</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.a.id}.${row.b.id}`} {...rowLink(pairAddress(row))}>
              <td>
                <Link to={pairAddress(row)} className="nw-landing-link">
                  <Name entry={row.a} duplicates={duplicates} />
                  <br />
                  <Name entry={row.b} duplicates={duplicates} />
                </Link>
              </td>
              <td className="num">{row.acts.toLocaleString("en-US")}</td>
              <td className={row.first_year === null ? "muted" : undefined}>{yearsCell(row.first_year, row.last_year)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CorpusChart({ chart }: { chart: Landing["chart"] }) {
  const width = 10;
  const partial = chart.partial ?? {};
  const today = new Date();
  const source = `FlorAcco review platform · ${window.location.origin}/network · ${today.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  })}`;
  const decade = (i: number) => periodLabel(chart.periods[i], width, partial, chart.periods);
  const n = (value: number) => value.toLocaleString("en-US");
  return (
    <div className="find-view nw-landing-chart">
      <TimeChart
        title={chart.title}
        subtitle={chart.subtitle}
        yTitle="entries"
        periods={chart.periods}
        width={width}
        partial={partial}
        series={[{ label: "entries active", color: BAR, values: chart.active, low: chart.active.map(() => false) }]}
        form="bars"
        percent={false}
        notes={chart.note ? [chart.note] : []}
        source={source}
        tip={(i) => ({
          title: decade(i),
          rows: [
            { color: NO_KEY, value: n(chart.acts[i]), label: "acts" },
            { color: BAR, value: n(chart.active[i]), label: "entries active" },
            { color: NO_KEY, value: n(chart.new_entries[i]), label: "new to the registers" },
            { color: NO_KEY, value: chart.cosigners_each[i].toFixed(1), label: "co-signers each, on these acts" },
          ],
        })}
        fileName="network-corpus-by-decade"
      />
      <details className="find-figures">
        <summary>{TABLE_FOLD}</summary>
        <div className="an-table-wrap">
          <table className="an-table nw-landing-table">
            <thead>
              <tr>
                <th>Decade</th>
                <th className="num">Entries active</th>
                <th className="num">Acts</th>
                <th className="num">New entries</th>
                <th className="num">Co-signers each</th>
              </tr>
            </thead>
            <tbody>
              {chart.periods.map((p, i) => (
                <tr key={p}>
                  <td>{decade(i)}</td>
                  <td className="num">{n(chart.active[i])}</td>
                  <td className="num">{n(chart.acts[i])}</td>
                  <td className="num">{n(chart.new_entries[i])}</td>
                  <td className="num">{chart.cosigners_each[i].toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {chart.note && <p className="find-standing-note">{chart.note}</p>}
        <p className="find-standing-note">{chart.as_of}</p>
      </details>
      <p className="nw-landing-caption">{chart.caption.join(" ")}</p>
    </div>
  );
}

function List<Row>({
  list,
  table,
}: {
  list: NetworkLandingList<Row>;
  table: (rows: Row[]) => ReactElement;
}) {
  const rest = list.rows.slice(SHOWN);
  return (
    <section className="nw-landing-list">
      <h3>{list.heading}</h3>
      <p className="find-standing-note">{list.definition}</p>
      {table(list.rows.slice(0, SHOWN))}
      {rest.length > 0 && (
        <details className="find-figures nw-landing-more">
          <summary>{ALL_ROWS}</summary>
          {table(rest)}
        </details>
      )}
      {list.note && <p className="find-standing-note">{list.note}</p>}
    </section>
  );
}

export default function NetworkLanding({ fixture }: { fixture: boolean }) {
  const [landing, setLanding] = useState<Landing | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setError("");
    const request = fixture
      ? import("../dev/network-landing-fixture.json").then((module) => module.default as unknown as Landing)
      : loadNetworkLanding();
    request
      .then((data) => {
        if (live) setLanding(data);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [fixture, attempt]);

  if (error) {
    return (
      <p className="error-text">
        {error}{" "}
        <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </button>
      </p>
    );
  }
  if (!landing) return <p className="muted">Loading…</p>;
  const widestNames = namesakes(landing.widest.rows);
  const pairNames = namesakes(landing.pairs.rows.flatMap((row) => [row.a, row.b]));
  return (
    <section className="find-landing nw-landing-corpus" aria-label="The corpus">
      <p className="find-answer">
        <strong>{landing.answer[0]}</strong> {landing.answer.slice(1).join(" ")}
      </p>
      <ul className="find-standings">
        {landing.figures.map((figure) => (
          <Figure key={figure.key} figure={figure} />
        ))}
      </ul>
      <CorpusChart chart={landing.chart} />
      <p className="eyebrow nw-landing-ways">{WAYS_IN}</p>
      <div className="nw-landing-lists">
        <List list={landing.widest} table={(rows) => <WidestTable rows={rows} duplicates={widestNames} />} />
        <List list={landing.pairs} table={(rows) => <PairsTable rows={rows} duplicates={pairNames} />} />
      </div>
      <p className="find-standing-note">{landing.as_of}</p>
    </section>
  );
}
