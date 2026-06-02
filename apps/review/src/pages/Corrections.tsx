import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  dismissCandidate,
  loadCandidates,
  loadCorrection,
  loadCorrections,
  transitionCorrection,
} from "../api";
import ProposeFixDrawer, { type ProposeSeed } from "../components/ProposeFixDrawer";
import WordSourceDrawer from "../components/WordSourceDrawer";
import type {
  CandidateRevisionEvidence,
  CorrectionCandidate,
  CorrectionCandidateStrength,
  CorrectionProposal,
  CorrectionStatus,
} from "../types";

const STATUS_LABEL: Record<CorrectionStatus, string> = {
  draft: "Draft",
  proposed: "Proposed",
  approved: "Approved",
  rejected: "Rejected",
  applied: "Applied",
  reverted: "Reverted",
};

const REASON_LABEL: Record<string, string> = {
  registration_date_differs: "Date ≠ Word",
  folio_differs: "Folio ≠ Word",
  event_type_table_differs: "Type ≠ Word",
  db_register_differs: "Register ≠ Word",
  db_register_missing: "Register missing",
  db_date_missing: "Date missing",
  orphan_main_contract: "Orphan main",
  missing_sub_type: "Type blank",
  person_no_name: "No name",
  numerical_discrepancy: "Numbers flag",
  missing_firm_name: "Firm missing",
};

// The tracked change that touches this field: deleted spans → inserted spans.
// This is *why* a date/folio conflict is worth a close look; it never decides anything.
function RevisionLens({ ev }: { ev: CandidateRevisionEvidence }) {
  const attribution = [ev.author, ev.date ? ev.date.slice(0, 10) : null].filter(Boolean).join(" · ");
  return (
    <section className="db-block">
      <h3>What the tracked change did</h3>
      <div className="rev-lens">
        {ev.deletions.length > 0 && (
          <div className="rev-lane">
            <span className="rev-lane-label">Removed</span>
            <span className="rev-spans">
              {ev.deletions.map((t, i) => (
                <span key={`d${i}`} className="rev-del">
                  {t}
                </span>
              ))}
            </span>
          </div>
        )}
        {ev.insertions.length > 0 && (
          <div className="rev-lane">
            <span className="rev-lane-label">Added</span>
            <span className="rev-spans">
              {ev.insertions.map((t, i) => (
                <span key={`i${i}`} className="rev-ins">
                  {t}
                </span>
              ))}
            </span>
          </div>
        )}
      </div>
      {attribution && <p className="muted cand-source-meta">Revised by {attribution}</p>}
    </section>
  );
}

export default function Corrections() {
  const [view, setView] = useState<"candidates" | "proposals">("candidates");
  return (
    <div className="corrections">
      <div className="corrections-modes">
        <button
          type="button"
          className={view === "candidates" ? "mode-tab is-active" : "mode-tab"}
          onClick={() => setView("candidates")}
        >
          Possibly needs correction
        </button>
        <button
          type="button"
          className={view === "proposals" ? "mode-tab is-active" : "mode-tab"}
          onClick={() => setView("proposals")}
        >
          Proposals · writes to the database
        </button>
      </div>
      {view === "candidates" ? <CandidateQueue /> : <ProposalsBoard />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Candidate queue — "which DB rows need a reviewer's eyes?" (read-only hypotheses)
// ---------------------------------------------------------------------------

function CandidateQueue() {
  const [strength, setStrength] = useState<"All" | CorrectionCandidateStrength>("All");
  const [reason, setReason] = useState("All");
  const [table, setTable] = useState("All");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [hideHandled, setHideHandled] = useState(false);

  const [candidates, setCandidates] = useState<CorrectionCandidate[]>([]);
  const [reasons, setReasons] = useState<string[]>([]);
  const [counts, setCounts] = useState({ total: 0, dismissed: 0, handled: 0 });
  const [selectedKey, setSelectedKey] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [dismissReason, setDismissReason] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);
  const [proposeSeed, setProposeSeed] = useState<ProposeSeed | null>(null);

  const params = useMemo(() => {
    const values = new URLSearchParams();
    if (strength !== "All") values.set("strength", strength);
    if (reason !== "All") values.set("reason", reason);
    if (table !== "All") values.set("table", table);
    values.set("include_dismissed", String(includeDismissed));
    values.set("include_handled", String(!hideHandled));
    return values;
  }, [strength, reason, table, includeDismissed, hideHandled]);

  const refresh = useCallback(() => {
    loadCandidates(params)
      .then((res) => {
        setCandidates(res.candidates);
        setReasons(res.reasons);
        setCounts({ total: res.total_all, dismissed: res.dismissed_count, handled: res.handled_count });
        setSelectedKey((prev) =>
          res.candidates.some((c) => c.candidate_key === prev) ? prev : res.candidates[0]?.candidate_key ?? "",
        );
        setError("");
      })
      .catch((err: Error) => setError(err.message));
  }, [params]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const selected = useMemo(
    () => candidates.find((c) => c.candidate_key === selectedKey) ?? null,
    [candidates, selectedKey],
  );

  const draft = (cand: CorrectionCandidate) => {
    if (!cand.field || !cand.editable) return;
    setProposeSeed({
      dbRowId: cand.db_row_id,
      recordTitle: cand.title,
      fieldLabel: cand.field_label ?? cand.field,
      column: cand.field,
      inputType: cand.input_type ?? "text",
      options: cand.options,
      currentValue: cand.db_value,
      wordValueHint: cand.word_value,
      prefillProposed: false,
      // Only tracked-change dates carry an adjudicated reading safe to pre-fill.
      initialProposedValue: cand.suggested_value ?? undefined,
      initialSourceEntryId: cand.source_entry_id ?? "",
      initialSourceQuote: cand.evidence_snippet || "",
      wordSources: cand.source_entry_id
        ? [
            {
              source_entry_id: cand.source_entry_id,
              source_entry_key: cand.source_entry_key,
              register_id: cand.register_id,
              label: null,
              date: null,
              folio: cand.source_folio,
              relationship: null,
              strength: null,
              status: cand.link_confirmed ? "confirmed" : "proposed",
            },
          ]
        : [],
    });
  };

  const dismiss = async () => {
    if (!selected) return;
    setError("");
    if (!reviewer.trim()) {
      setError("Enter your initials first.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    try {
      await dismissCandidate(selected.candidate_key, {
        reviewer: reviewer.trim(),
        reason: dismissReason.trim(),
      });
      setMessage("Dismissed — hidden from the queue.");
      setDismissReason("");
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Possibly needs correction · hypotheses, not changes</p>
          <div className="db-tabs">
            {(["All", "high", "medium", "low"] as const).map((s) => (
              <button
                key={s}
                type="button"
                className={s === strength ? "db-tab is-active" : "db-tab"}
                onClick={() => setStrength(s)}
              >
                {s === "All" ? "All" : s[0].toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
          <div className="cand-filters">
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="All">All reasons</option>
              {reasons.map((r) => (
                <option key={r} value={r}>
                  {REASON_LABEL[r] ?? r}
                </option>
              ))}
            </select>
            <select value={table} onChange={(e) => setTable(e.target.value)}>
              <option value="All">All tables</option>
              <option value="contract">Contracts</option>
              <option value="sub_contract">Sub-contracts</option>
              <option value="person">People</option>
            </select>
          </div>
          <div className="cand-toggles">
            <label>
              <input type="checkbox" checked={hideHandled} onChange={(e) => setHideHandled(e.target.checked)} />
              Hide handled
            </label>
            <label>
              <input
                type="checkbox"
                checked={includeDismissed}
                onChange={(e) => setIncludeDismissed(e.target.checked)}
              />
              Show dismissed
            </label>
          </div>
          <p className="db-count muted">
            {candidates.length} shown · {counts.total} total · {counts.handled} handled · {counts.dismissed} dismissed
          </p>
        </div>
        <ul className="db-results">
          {candidates.map((c) => (
            <li key={c.candidate_key}>
              <button
                type="button"
                className={c.candidate_key === selectedKey ? "db-result is-active" : "db-result"}
                onClick={() => setSelectedKey(c.candidate_key)}
              >
                <span className="db-result-title">
                  <span className={`cand-strength is-${c.strength}`} title={c.strength} />
                  {c.title}
                </span>
                <span className="db-result-meta">
                  <span className="cand-chip">{REASON_LABEL[c.reason_code] ?? c.reason_code}</span>
                  {c.db_row_id}
                  {c.revision_evidence && <span className="cand-tag is-revision">tracked change</span>}
                  {c.link_confirmed && <span className="cand-tag is-confirmed">confirmed link</span>}
                  {c.existing_proposal && <span className="cand-tag is-handled">handled</span>}
                  {c.dismissed && <span className="cand-tag is-dismissed">dismissed</span>}
                </span>
              </button>
            </li>
          ))}
          {candidates.length === 0 && !error && <li className="db-empty muted">Nothing in this view.</li>}
        </ul>
      </aside>

      <section className="db-detail">
        {!selected && <p className="muted">{error || "Select a candidate."}</p>}
        {selected && (
          <article className="db-record correction-detail">
            <header className="db-record-head">
              <p className="eyebrow">
                {selected.family === "word_db_conflict" ? "Word ↔ database conflict" : "Database health"} ·{" "}
                <span className={`cand-strength-label is-${selected.strength}`}>{selected.strength} signal</span>
              </p>
              <h2>{selected.title}</h2>
              <Link
                className="db-row-id"
                to={`/database/${selected.db_table}/${selected.db_row_id.split(":")[1]}`}
              >
                {selected.db_row_id} ↗
              </Link>
            </header>

            {message && <div className="notice success">{message}</div>}
            {error && <p className="error-text">{error}</p>}

            {selected.link_confirmed && (
              <div className="notice info">
                This DB row is on a <strong>reviewer-confirmed link</strong> — identity is settled, so the field
                really should agree with the Word source.
              </div>
            )}
            {selected.existing_proposal && (
              <div className="notice info">
                A correction is already <strong>{selected.existing_proposal.status}</strong> for this field
                {selected.existing_proposal.proposed_value
                  ? ` (→ ${selected.existing_proposal.proposed_value})`
                  : ""}
                .
              </div>
            )}

            <p className="reading-text cand-explanation">{selected.explanation}</p>

            <div className="correction-diff cand-compare">
              <div className="diff-side diff-before">
                <span className="propose-label">In the database (the truth to perfect)</span>
                <span>{selected.db_value || "— (empty)"}</span>
              </div>
              <span className="diff-arrow">vs</span>
              <div className="diff-side diff-after">
                <span className="propose-label">Word source (evidence — not applied)</span>
                <span>{selected.word_value || "— open the narrative to read"}</span>
              </div>
            </div>

            {selected.suggested_value && (
              <p className="muted cand-suggested">
                Adjudicated reading <code>{selected.suggested_value}</code> will pre-fill the draft — confirm it
                against the manuscript before proposing.
              </p>
            )}

            {selected.revision_evidence && <RevisionLens ev={selected.revision_evidence} />}

            {selected.evidence_snippet && (
              <section className="db-block">
                <h3>Linked narrative</h3>
                <blockquote className="source-quote">{selected.evidence_snippet}…</blockquote>
                <p className="muted cand-source-meta">
                  {[selected.register_id, selected.source_folio].filter(Boolean).join(" · ")}
                </p>
                {selected.source_entry_id && (
                  <button
                    type="button"
                    className="pill-button"
                    onClick={() => setOpenSourceId(selected.source_entry_id)}
                  >
                    Open Word entry & image
                  </button>
                )}
              </section>
            )}

            <div className="correction-actionbar cand-actionbar">
              <input
                className="actionbar-reviewer"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                placeholder="initials"
              />
              <input
                className="actionbar-note"
                value={dismissReason}
                onChange={(e) => setDismissReason(e.target.value)}
                placeholder="dismiss reason (e.g. expected foliation)"
              />
              <button type="button" className="pill-button" onClick={dismiss}>
                Dismiss
              </button>
              {selected.editable ? (
                <button type="button" className="pill-button is-active" onClick={() => draft(selected)}>
                  Draft correction
                </button>
              ) : (
                <span className="muted cand-flagonly">Flag only — not a directly editable field</span>
              )}
            </div>
          </article>
        )}
      </section>

      {openSourceId && (
        <WordSourceDrawer sourceEntryId={openSourceId} onClose={() => setOpenSourceId(null)} />
      )}
      {proposeSeed && (
        <ProposeFixDrawer
          seed={proposeSeed}
          onClose={() => setProposeSeed(null)}
          onSubmitted={() => {
            setProposeSeed(null);
            setMessage("Correction drafted — find it under Proposals.");
            refresh();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Proposals board — the human-authored review/apply path (unchanged behaviour)
// ---------------------------------------------------------------------------

function ProposalsBoard() {
  const [statusFilter, setStatusFilter] = useState("proposed");
  const [proposals, setProposals] = useState<CorrectionProposal[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [detail, setDetail] = useState<CorrectionProposal | null>(null);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);

  const params = useMemo(() => {
    const values = new URLSearchParams();
    values.set("status", statusFilter);
    return values;
  }, [statusFilter]);

  const refresh = useCallback(() => {
    loadCorrections(params)
      .then((response) => {
        setProposals(response.proposals);
        setSelectedId((prev) =>
          response.proposals.some((p) => p.proposal_id === prev) ? prev : response.proposals[0]?.proposal_id ?? "",
        );
      })
      .catch((err: Error) => setError(err.message));
  }, [params]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    loadCorrection(selectedId)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [selectedId]);

  const act = async (action: "approve" | "reject" | "apply" | "revert") => {
    if (!detail) return;
    setError("");
    setMessage("");
    if (!reviewer.trim()) {
      setError("Enter your initials first.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    try {
      const res = await transitionCorrection(detail.proposal_id, action, {
        reviewer: reviewer.trim(),
        note: note.trim(),
      });
      setMessage(`Proposal ${action}d.`);
      setNote("");
      setDetail(res.proposal);
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const showStale = detail?.is_stale && (detail.status === "proposed" || detail.status === "approved");

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Proposals · writes to the database</p>
          <div className="db-tabs">
            {["proposed", "approved", "applied", "All"].map((s) => (
              <button
                key={s}
                type="button"
                className={s === statusFilter ? "db-tab is-active" : "db-tab"}
                onClick={() => setStatusFilter(s)}
              >
                {s === "All" ? "All" : STATUS_LABEL[s as CorrectionStatus]}
              </button>
            ))}
          </div>
          <p className="db-count muted">{proposals.length} proposal(s)</p>
        </div>
        <ul className="db-results">
          {proposals.map((p) => (
            <li key={p.proposal_id}>
              <button
                type="button"
                className={p.proposal_id === selectedId ? "db-result is-active" : "db-result"}
                onClick={() => setSelectedId(p.proposal_id)}
              >
                <span className="db-result-title">
                  {p.db_table}.{p.field}
                </span>
                <span className="db-result-meta">
                  {p.current_value || "∅"} → {p.proposed_value || "(flag)"}
                </span>
              </button>
            </li>
          ))}
          {proposals.length === 0 && !error && <li className="db-empty muted">No proposals.</li>}
        </ul>
      </aside>

      <section className="db-detail">
        {!detail && <p className="muted">{error || "Select a proposal."}</p>}
        {detail && (
          <article className="db-record correction-detail">
            <header className="db-record-head">
              <p className="eyebrow">
                {detail.origin === "agent_suggested" ? "Agent-suggested" : "Manual"} ·{" "}
                {detail.change_type.replace("_", " ")}
              </p>
              <h2>
                {detail.field_label}{" "}
                <span className={`db-source-badge is-${detail.status}`}>{STATUS_LABEL[detail.status]}</span>
              </h2>
              <Link className="db-row-id" to={`/database/${detail.db_table}/${detail.db_row_id.split(":")[1]}`}>
                {detail.db_row_id} ↗
              </Link>
            </header>

            {showStale && (
              <div className="notice error">
                The database value changed since this proposal (now{" "}
                <strong>{detail.db_value_now || "∅"}</strong>). Re-confirm before applying.
              </div>
            )}
            {message && <div className="notice success">{message}</div>}
            {error && <p className="error-text">{error}</p>}

            <div className="correction-diff">
              <div className="diff-side diff-before">
                <span className="propose-label">Current</span>
                <span>{detail.current_value || "— (empty)"}</span>
              </div>
              <span className="diff-arrow">→</span>
              <div className="diff-side diff-after">
                <span className="propose-label">Proposed</span>
                <span>{detail.proposed_value || "(annotation only)"}</span>
              </div>
            </div>

            {detail.rationale && (
              <section className="db-block">
                <h3>Rationale</h3>
                <p className="reading-text">{detail.rationale}</p>
              </section>
            )}

            {detail.source.source_quote && (
              <section className="db-block">
                <h3>Source</h3>
                <blockquote className="source-quote">{detail.source.source_quote}</blockquote>
                {detail.source.source_entry_id && (
                  <button
                    type="button"
                    className="pill-button"
                    onClick={() => setOpenSourceId(detail.source.source_entry_id)}
                  >
                    Open Word entry
                  </button>
                )}
              </section>
            )}

            <dl className="db-fields correction-meta">
              <div className="db-field">
                <dt>Proposed by</dt>
                <dd>
                  {detail.created_by} · {detail.created_at.slice(0, 10)}
                </dd>
              </div>
              {detail.reviewed_by && (
                <div className="db-field">
                  <dt>Reviewed by</dt>
                  <dd>{detail.reviewed_by}</dd>
                </div>
              )}
              {detail.applied_by && (
                <div className="db-field">
                  <dt>Applied by</dt>
                  <dd>
                    {detail.applied_by} · {(detail.applied_at ?? "").slice(0, 10)}
                  </dd>
                </div>
              )}
            </dl>

            <div className="correction-actionbar">
              <input
                className="actionbar-reviewer"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                placeholder="initials"
              />
              <input
                className="actionbar-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="note (optional)"
              />
              {detail.status === "proposed" && (
                <>
                  <button type="button" className="pill-button" onClick={() => act("reject")}>
                    Reject
                  </button>
                  <button type="button" className="pill-button is-active" onClick={() => act("approve")}>
                    Approve
                  </button>
                </>
              )}
              {detail.status === "approved" && (
                <button
                  type="button"
                  className="pill-button is-apply"
                  onClick={() => act("apply")}
                  disabled={Boolean(showStale)}
                >
                  Apply to database
                </button>
              )}
              {detail.status === "applied" && (
                <button type="button" className="pill-button is-revert" onClick={() => act("revert")}>
                  Revert
                </button>
              )}
            </div>
          </article>
        )}
      </section>

      {openSourceId && <WordSourceDrawer sourceEntryId={openSourceId} onClose={() => setOpenSourceId(null)} />}
    </div>
  );
}
