import { useState } from "react";
import type { DecisionPayload, ReviewCase } from "../types";

// Shared across the whole app (Database, Reference, edit components). Migrate the
// legacy "floracco.reviewer" (dot) once so initials set here in Reconcile carry over.
const REVIEWER_KEY = "floracco_reviewer";
const LEGACY_REVIEWER_KEY = "floracco.reviewer";

function readReviewer(): string {
  if (typeof localStorage === "undefined") return "";
  return localStorage.getItem(REVIEWER_KEY) ?? localStorage.getItem(LEGACY_REVIEWER_KEY) ?? "";
}

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

type Verdict = "confirm" | "none" | "unsure";

export default function DecisionBar({
  reviewCase,
  selectedDbRows,
  onSave,
}: {
  reviewCase: ReviewCase;
  selectedDbRows: string[];
  onSave: (decision: DecisionPayload) => Promise<void>;
}) {
  const row = reviewCase.row;
  const [reviewer, setReviewer] = useState(readReviewer);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const allIds = reviewCase.suggested_db_row_ids;

  const submit = async (verdict: Verdict) => {
    if (!reviewer.trim()) return;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(REVIEWER_KEY, reviewer.trim());
      localStorage.removeItem(LEGACY_REVIEWER_KEY);   // finish the migration
    }
    const selected = verdict === "confirm" ? selectedDbRows : [];
    // Three decision states, keyed on the matcher's own link_role: unticked
    // `primary` rows were the question being asked → rejected; unticked
    // `alternative` rows (demoted siblings kept as evidence) were never the
    // question → recorded as unassessed, with no decision status, so they do
    // not surface as "rejected" on the DB record. "None match" is an explicit
    // verdict over everything shown, alternatives included; "Not sure" rejects
    // nothing — the reviewer explicitly declined to decide.
    const metrics = reviewCase.link_metrics ?? {};
    const isAlternative = (id: string) => metrics[id]?.link_role === "alternative";
    const unticked = allIds.filter((id) => !selected.includes(id));
    const rejected =
      verdict === "confirm" ? unticked.filter((id) => !isAlternative(id)) : verdict === "none" ? unticked : [];
    const unassessed =
      verdict === "confirm" ? unticked.filter(isAlternative) : verdict === "unsure" ? unticked : [];
    let mainJudgment = "cannot_decide";
    let nextAction = "ask_project_lead";
    if (verdict === "confirm") {
      mainJudgment = selected.length > 1 ? "multiple_db_records" : selected.length === 1 ? "same_act" : "not_same_act";
      nextAction = selected.length ? "approve_link" : "reject_link";
    } else if (verdict === "none") {
      mainJudgment = "not_same_act";
      nextAction = "reject_link";
    }

    setIsSaving(true);
    try {
      await onSave({
        reviewer: reviewer.trim(),
        source_entry_key: value(row, "source_entry_key"),
        source_entry_id: value(row, "source_entry_id"),
        suggested_db_row_id: allIds.join("; ") || value(row, "top_db_row_id"),
        packet_section: value(row, "packet_section"),
        register_id: value(row, "register_id"),
        recommended_review_bucket: value(row, "recommended_review_bucket"),
        main_judgment: mainJudgment,
        image_judgment: "not_needed",
        field_correction_needed: "none_obvious",
        next_action: nextAction,
        review_note: note,
        image_candidate_paths: value(row, "image_candidate_paths"),
        selected_db_row_ids: selected,
        rejected_db_row_ids: rejected,
        unassessed_db_row_ids: unassessed,
        suggested_relationship_type: value(row, "suggested_relationship_type"),
      });
      setNote("");
      setShowNote(false);
    } finally {
      setIsSaving(false);
    }
  };

  const supportedCount = selectedDbRows.length;
  const confirmLabel =
    supportedCount === 1 ? "Confirm match · next" : `Confirm ${supportedCount} records · next`;

  return (
    <section className="decision-bar">
      <input
        className="reviewer-input"
        value={reviewer}
        onChange={(event) => setReviewer(event.target.value)}
        placeholder="Your initials"
        aria-label="Reviewer initials"
      />
      <button
        type="button"
        className="ghost-button"
        onClick={() => setShowNote((open) => !open)}
        aria-pressed={showNote}
      >
        {note.trim() ? "Note ✓" : "+ Note"}
      </button>
      {showNote ? (
        <input
          className="note-input"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Optional note for this case"
          aria-label="Reviewer note"
        />
      ) : null}
      <div className="decision-actions">
        <button type="button" className="link-button" disabled={!reviewer.trim() || isSaving} onClick={() => submit("unsure")}>
          Not sure
        </button>
        {supportedCount > 0 ? (
          <>
            <button
              type="button"
              className="link-button"
              disabled={!reviewer.trim() || isSaving}
              onClick={() => submit("none")}
            >
              None match
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={!reviewer.trim() || isSaving}
              onClick={() => submit("confirm")}
            >
              {isSaving ? "Saving…" : confirmLabel}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="primary-button"
            disabled={!reviewer.trim() || isSaving}
            onClick={() => submit("none")}
          >
            {isSaving ? "Saving…" : "None match · next"}
          </button>
        )}
      </div>
    </section>
  );
}
