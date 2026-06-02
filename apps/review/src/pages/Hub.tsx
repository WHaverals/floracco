import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadSummary } from "../api";
import type { ReviewSummary } from "../types";

type Tool = {
  to: string;
  title: string;
  blurb: string;
  status: "ready" | "planned";
};

const TOOLS: Tool[] = [
  {
    to: "/reconcile",
    title: "Word–Database reconciliation",
    blurb: "Decide which database record(s), if any, each Word entry supports. Side-by-side reading, evidence, and image.",
    status: "ready",
  },
  {
    to: "/changes",
    title: "Tracked-changes review",
    blurb: "Read each act with its editorial history and accept or reject insertions, deletions, and comments.",
    status: "planned",
  },
  {
    to: "/database",
    title: "Database browser",
    blurb: "Explore contracts, sub-contracts, and people. Read-only, entity-centric, with links back to the sources.",
    status: "ready",
  },
  {
    to: "/corrections",
    title: "Database corrections",
    blurb: "Propose, review, and apply field-level corrections with a source quote and full audit trail.",
    status: "ready",
  },
  {
    to: "/dashboard",
    title: "Dashboard & exports",
    blurb: "Track progress and coverage across registers and export review decisions.",
    status: "planned",
  },
];

export default function Hub() {
  const [summary, setSummary] = useState<ReviewSummary | null>(null);

  useEffect(() => {
    loadSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  const reviewed = summary?.reviewed_cases ?? 0;
  const totalCases = summary?.total_cases ?? 0;
  const progress = totalCases ? Math.round((reviewed / totalCases) * 100) : 0;

  return (
    <div className="hub">
      <header className="hub-header">
        <p className="eyebrow">Florentine Accomandite · review platform</p>
        <h1>What would you like to work on?</h1>
        <p className="muted">
          Each tool does one job. Pick a task below — you can move between them at any time from the top bar.
        </p>
      </header>

      <div className="hub-grid">
        {TOOLS.map((tool) => {
          const isReady = tool.status === "ready";
          const meta =
            tool.to === "/reconcile" && totalCases ? `${totalCases - reviewed} of ${totalCases} open` : null;
          const card = (
            <>
              <div className="hub-card-top">
                <h2>{tool.title}</h2>
                {isReady ? null : <span className="hub-tag">Planned</span>}
              </div>
              <p>{tool.blurb}</p>
              {meta ? <span className="hub-card-meta">{meta}</span> : null}
            </>
          );
          return isReady ? (
            <Link className="hub-card" key={tool.to} to={tool.to}>
              {card}
            </Link>
          ) : (
            <div className="hub-card is-disabled" key={tool.to} aria-disabled="true">
              {card}
            </div>
          );
        })}
      </div>

      {totalCases ? (
        <footer className="hub-progress">
          <div className="progress-label">
            <span>Reconciliation progress</span>
            <strong>{progress}%</strong>
          </div>
          <progress max={totalCases || 1} value={reviewed} />
          <span className="muted">
            {reviewed} of {totalCases} cases reviewed
          </span>
        </footer>
      ) : null}
    </div>
  );
}
