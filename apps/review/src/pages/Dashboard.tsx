import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { exportUrl, loadDashboard } from "../api";
import type { Dashboard as DashboardData, ProgressCount } from "../types";

function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ProgressRow({ item }: { item: ProgressCount }) {
  const percent = pct(item.reviewed, item.total);
  return (
    <div className="dash-progress-row">
      <div className="dash-progress-label">
        <span>{item.label}</span>
        <span className="muted">
          {item.reviewed}/{item.total}
        </span>
      </div>
      <div className="dash-bar" role="img" aria-label={`${percent}% reviewed`}>
        <span className="dash-bar-fill" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    loadDashboard()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  const snapshotUrl = useMemo(() => {
    if (!data) return "";
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    return URL.createObjectURL(blob);
  }, [data]);

  useEffect(() => {
    return () => {
      if (snapshotUrl) URL.revokeObjectURL(snapshotUrl);
    };
  }, [snapshotUrl]);

  if (error) {
    return (
      <div className="dashboard">
        <div className="notice error">{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="dashboard">
        <p className="muted">Loading dashboard…</p>
      </div>
    );
  }

  const { reconcile, coverage, corrections } = data;
  const reconcilePct = pct(reconcile.reviewed_cases, reconcile.total_cases);
  const wordTotal = coverage.word_entry_total || 1;
  const candidates = corrections.candidates;

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Florentine Accomandite · review platform</p>
          <h1>Dashboard &amp; exports</h1>
          <p className="muted">
            Progress and coverage across the corpus. This view only reads the logs and pipeline outputs — it never
            changes data. Tiles link into the tool that does the work.
          </p>
        </div>
      </header>

      {/* Reconcile progress */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Reconciliation progress</h2>
          <Link className="dash-link" to="/reconcile">
            Open Reconcile →
          </Link>
        </div>
        <div className="dash-cards">
          <div className="dash-card dash-card-hero">
            <span className="dash-stat">{reconcilePct}%</span>
            <span className="muted">
              {reconcile.reviewed_cases} of {reconcile.total_cases} queue cases reviewed
            </span>
            <div className="dash-bar dash-bar-lg">
              <span className="dash-bar-fill" style={{ width: `${reconcilePct}%` }} />
            </div>
            <div className="dash-verdicts">
              <span className="dash-chip band-ok">{reconcile.decisions.confirmed} confirmed</span>
              <span className="dash-chip band-alert">{reconcile.decisions.rejected} none/rejected</span>
              <span className="dash-chip band-mid">{reconcile.decisions.not_sure} not sure</span>
            </div>
          </div>
          <div className="dash-card">
            <h3>By bucket</h3>
            <div className="dash-scroll">
              {reconcile.by_bucket.map((item) => (
                <ProgressRow key={item.label} item={item} />
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Coverage */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Coverage</h2>
          <Link className="dash-link" to="/database">
            Open Database →
          </Link>
        </div>
        <div className="dash-cards">
          <div className="dash-card">
            <h3>Word entries · {coverage.word_entry_total.toLocaleString()}</h3>
            <div className="dash-stacked" role="img" aria-label="Word entry status split">
              {Object.entries(coverage.word_status_totals).map(([key, count]) => (
                <span
                  key={key}
                  className={`dash-stacked-seg seg-${key}`}
                  style={{ width: `${pct(count, wordTotal)}%` }}
                  title={`${coverage.word_status_labels[key] ?? key}: ${count}`}
                />
              ))}
            </div>
            <ul className="dash-legend">
              {Object.entries(coverage.word_status_totals).map(([key, count]) => (
                <li key={key}>
                  <span className={`dash-dot seg-${key}`} />
                  {coverage.word_status_labels[key] ?? key}
                  <strong>{count.toLocaleString()}</strong>
                </li>
              ))}
            </ul>
          </div>
          <div className="dash-card">
            <h3>Database rows</h3>
            <p className="dash-stat-sm">{coverage.db_row_total.toLocaleString()}</p>
            <span className="muted">total rows</span>
            <p className="dash-inline">
              <Link to="/reconcile" className="dash-chip band-mid">
                {coverage.db_only_total} DB-only (no Word link)
              </Link>
            </p>
          </div>
          <div className="dash-card">
            <h3>Manuscript images</h3>
            <p className="dash-stat-sm">{pct(coverage.images.with_candidates, coverage.images.queue_rows)}%</p>
            <span className="muted">
              {coverage.images.with_candidates} of {coverage.images.queue_rows} queue rows have a candidate image
            </span>
            {coverage.images.need_review > 0 ? (
              <p className="dash-inline">
                <span className="dash-chip band-alert">{coverage.images.need_review} need image review</span>
              </p>
            ) : null}
          </div>
        </div>

        <div className="dash-card dash-table-card">
          <h3>Per register</h3>
          <div className="dash-table-scroll">
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Register</th>
                  <th>Word</th>
                  <th>High-conf</th>
                  <th>Candidate</th>
                  <th>Multi</th>
                  <th>Ambiguous</th>
                  <th>Word-only</th>
                  <th>DB rows</th>
                  <th>DB-only</th>
                </tr>
              </thead>
              <tbody>
                {coverage.registers.map((reg) => (
                  <tr key={reg.register_id}>
                    <td className="dash-reg">{reg.register_id}</td>
                    <td>{reg.word_entry_count}</td>
                    <td>{reg.matched_high_confidence}</td>
                    <td>{reg.matched_candidate}</td>
                    <td>{reg.matched_multiple}</td>
                    <td className={reg.ambiguous ? "dash-flag" : ""}>{reg.ambiguous}</td>
                    <td>{reg.word_only}</td>
                    <td>{reg.db_row_count}</td>
                    <td className={reg.db_only ? "dash-flag" : ""}>{reg.db_only}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Corrections */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Database corrections</h2>
          <Link className="dash-link" to="/corrections">
            Open Corrections →
          </Link>
        </div>
        <div className="dash-cards">
          <div className="dash-card dash-card-hero">
            <span className="dash-stat">{corrections.applied_writes}</span>
            <span className="muted">database fields applied (audited writes)</span>
            <div className="dash-verdicts">
              {Object.entries(corrections.proposals_by_status).length === 0 ? (
                <span className="muted">No correction proposals yet.</span>
              ) : (
                Object.entries(corrections.proposals_by_status).map(([status, count]) => (
                  <span key={status} className="dash-chip band-neutral">
                    {count} {status}
                  </span>
                ))
              )}
            </div>
          </div>
          <div className="dash-card">
            <h3>Candidate queue · {candidates.total.toLocaleString()}</h3>
            <div className="dash-verdicts">
              <span className="dash-chip band-mid">{candidates.open.toLocaleString()} open</span>
              <span className="dash-chip band-ok">{candidates.handled} handled</span>
              <span className="dash-chip band-neutral">{candidates.dismissed} dismissed</span>
            </div>
            <ul className="dash-legend">
              {Object.entries(candidates.by_family).map(([family, count]) => (
                <li key={family}>
                  {family === "word_db_conflict" ? "Word ↔ DB conflict" : family === "db_intrinsic" ? "DB-intrinsic" : family}
                  <strong>{count.toLocaleString()}</strong>
                </li>
              ))}
              {Object.entries(candidates.by_strength).map(([strength, count]) => (
                <li key={strength} className="muted">
                  {strength} signal
                  <strong>{count.toLocaleString()}</strong>
                </li>
              ))}
            </ul>
          </div>
          <div className="dash-card">
            <h3>Recent applied writes</h3>
            {corrections.recent_applied.length === 0 ? (
              <p className="muted">No writes have been applied to the database yet.</p>
            ) : (
              <ul className="dash-applied">
                {corrections.recent_applied.map((write, index) => (
                  <li key={`${write.db_row_id}-${write.field}-${index}`}>
                    <code>{write.db_row_id}</code> · {write.field}
                    <span className="dash-applied-diff">
                      <s>{write.pre_image || "∅"}</s> → <strong>{write.post_image || "∅"}</strong>
                    </span>
                    <span className="muted">
                      {write.by} · {formatWhen(write.at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      {/* Exports */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Exports</h2>
        </div>
        <div className="dash-exports">
          <a className="dash-export-btn" href={exportUrl("decisions")} download>
            Reconcile decisions (CSV)
          </a>
          <a className="dash-export-btn" href={exportUrl("proposals")} download>
            Correction proposals (JSONL)
          </a>
          <a className="dash-export-btn" href={exportUrl("candidates")} download>
            Correction candidates (JSONL)
          </a>
          {snapshotUrl ? (
            <a className="dash-export-btn" href={snapshotUrl} download="floracco-dashboard.json">
              Dashboard snapshot (JSON)
            </a>
          ) : null}
        </div>
      </section>
    </div>
  );
}
