import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { contractsWord } from "./FindChips";
import { TimeChart } from "./FindViews";
import { findTime, loadFindLanding } from "../api";
import type { FindFigure, FindLanding as Landing, FindSegment, FindTimeResult } from "../types";
import { EMPTY_FIND, findSearch, fromSpec, toSpec, type FindSpec, type Kinds } from "../utils/findSpec";
import { joinSearch, periodLabel, viewOf, viewSearch, type FindView } from "../utils/findView";

/* The corpus before the first condition (review 2026-10-27, sections 7 and 12.1):
 * the corpus's answer line, the standing figures and every decade of
 * registration on the shared axis, with the undated contracts named. Each figure
 * is computed now by the server through the Find engine (find_figures), carries
 * its definition and the last edit logged to what it counts, and each of its
 * numbers opens the Find question it counts. The figures' words are the
 * server's, still before Francesca (register 12e). The landing folds away once
 * a condition is added. */

// The first of the validated series colours (FindViews.tsx), on the chart surface.
const BAR = "#2a78d6";

/** The address of a Find question with its view, as the page writes it. */
export function findAddress(spec: FindSpec, view: Partial<FindView>, kinds: Kinds): string {
  const search = joinSearch(findSearch(fromSpec(spec), kinds), viewSearch(viewOf(view)));
  return `/analysis/find${search ? `?${search}` : ""}`;
}

function Segments({ segments, kinds }: { segments: FindSegment[]; kinds: Kinds }) {
  return (
    <>
      {segments.map((segment, k) =>
        segment.spec ? (
          <Link key={k} to={findAddress(segment.spec, segment.view ?? {}, kinds)}>
            {segment.text}
          </Link>
        ) : (
          <span key={k}>{segment.text}</span>
        ),
      )}
    </>
  );
}

function words(segments: FindSegment[]): string {
  return segments.map((s) => s.text).join("");
}

function Figure({ figure, kinds }: { figure: FindFigure; kinds: Kinds }) {
  return (
    <li className="find-standing">
      <span className="find-standing-label">{figure.label}</span>
      <span className="find-standing-value">
        <Segments segments={figure.value} kinds={kinds} />
      </span>
      {figure.detail.length > 0 && (
        <span className="find-standing-detail">
          <Segments segments={figure.detail} kinds={kinds} />
        </span>
      )}
      <span className="find-standing-note">{figure.definition}</span>
      <span className="find-standing-note">{figure.as_of}</span>
    </li>
  );
}

function CorpusChart({ chart, time }: { chart: FindFigure; time: FindTimeResult }) {
  const series = time.series[0];
  if (!series) return null;
  const today = new Date();
  const source = `FlorAcco review platform · ${window.location.origin}/analysis/find · ${today.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  })}`;
  const notes = [
    ...(series.undated > 0
      ? [`${contractsWord(series.undated)} with no registration date ${series.undated === 1 ? "is" : "are"} not on the axis.`]
      : []),
    ...time.notes,
  ];
  return (
    <div className="find-view">
      <TimeChart
        title={chart.label}
        subtitle={`${words(chart.value)}; ${words(chart.detail)}. ${chart.definition}`}
        yTitle="contracts"
        periods={time.periods}
        width={time.width}
        partial={time.partial}
        series={[{ label: "contracts", color: BAR, values: series.counts, low: series.counts.map(() => false) }]}
        form="bars"
        percent={false}
        notes={notes}
        source={source}
        tip={(i) => ({
          title: periodLabel(time.periods[i], time.width, time.partial, time.periods),
          rows: [{ color: BAR, value: series.counts[i].toLocaleString(), label: "contracts" }],
        })}
        fileName="find-corpus-by-decade"
      />
      <details className="find-figures">
        <summary>The figures as a table</summary>
        <div className="an-table-wrap">
          <table className="an-table">
            <thead>
              <tr>
                <th>Decade</th>
                <th className="num">Contracts</th>
              </tr>
            </thead>
            <tbody>
              {time.periods.map((p, i) => (
                <tr key={p}>
                  <td>{periodLabel(p, time.width, time.partial, time.periods)}</td>
                  <td className="num">{series.counts[i].toLocaleString()}</td>
                </tr>
              ))}
              {series.undated > 0 && (
                <tr>
                  <td>no registration date</td>
                  <td className="num">{series.undated.toLocaleString()}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

export default function FindLanding({ kinds }: { kinds: Kinds }) {
  const [landing, setLanding] = useState<Landing | null>(null);
  const [time, setTime] = useState<FindTimeResult | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setError("");
    Promise.all([loadFindLanding(), findTime(toSpec(EMPTY_FIND), "decade", "", [])])
      .then(([figures, decades]) => {
        if (!live) return;
        setLanding(figures);
        setTime(decades);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [attempt]);

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
  if (!landing || !time) return <p className="muted">Loading…</p>;
  return (
    <section className="find-landing" aria-label="The corpus">
      <p className="find-answer">
        <strong>{landing.answer[0]}</strong> {landing.answer.slice(1).join(" ")}
      </p>
      <ul className="find-standings">
        {landing.figures.map((figure) => (
          <Figure key={figure.key} figure={figure} kinds={kinds} />
        ))}
      </ul>
      <CorpusChart chart={landing.chart} time={time} />
      <p className="find-standing-note">
        <Segments segments={landing.chart.value} kinds={kinds} />
        {" · "}
        {landing.chart.as_of}
      </p>
    </section>
  );
}
