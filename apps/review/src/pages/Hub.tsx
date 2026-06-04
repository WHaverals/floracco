import { Link } from "react-router-dom";

type Tool = {
  to: string;
  title: string;
  blurb: string;
  status: "ready" | "planned";
};

const TOOLS: Tool[] = [
  {
    to: "/reconcile",
    title: "Match Word to database",
    blurb: "Does this summary belong to this database record?",
    status: "ready",
  },
  {
    to: "/changes",
    title: "Word edits",
    blurb: "Accept or reject tracked changes in the summaries.",
    status: "planned",
  },
  {
    to: "/database",
    title: "Browse database",
    blurb: "Look up contracts and people. Read-only.",
    status: "ready",
  },
  {
    to: "/corrections",
    title: "Fix database fields",
    blurb: "Correct dates, folios, and other details using the Word text as evidence.",
    status: "ready",
  },
  {
    to: "/dashboard",
    title: "Progress & exports",
    blurb: "Overview of review work and download logs.",
    status: "ready",
  },
];

export default function Hub() {
  return (
    <div className="hub">
      <header className="hub-header">
        <p className="eyebrow">Florentine Accomandite · review platform</p>
        <h1>What would you like to work on?</h1>
        <p className="muted">Pick a task below — you can switch anytime from the top bar.</p>
      </header>

      <div className="hub-grid">
        {TOOLS.map((tool) => {
          const isReady = tool.status === "ready";
          const card = (
            <>
              <div className="hub-card-top">
                <h2>{tool.title}</h2>
                {isReady ? null : <span className="hub-tag">Planned</span>}
              </div>
              <p>{tool.blurb}</p>
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
    </div>
  );
}
