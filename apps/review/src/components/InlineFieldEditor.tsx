import { useEffect, useRef, useState } from "react";
import { createCorrection, transitionCorrection } from "../api";
import type { DbFieldInputType } from "../types";

/* Lightweight in-place editor for one record field.
 *
 * Replaces the old "Suggest a fix" drawer on the record page: no change-type
 * dropdown (inferred: empty → fill, else correct), no source-quote ceremony
 * (the human reviewer is the interpreter; the Word summary sits right on the
 * page as evidence), just the value, an optional note, and Save. Saving runs
 * the full audited lifecycle (propose → approve → apply) in one step — the
 * "direct-with-audit" mode for the project team — so the change lands
 * immediately and shows in Change history.
 */
export default function InlineFieldEditor({
  dbRowId,
  column,
  label,
  inputType,
  options,
  currentValue,
  onSaved,
  onCancel,
}: {
  dbRowId: string;
  column: string;
  label: string;
  inputType: DbFieldInputType;
  options?: string[] | null;
  currentValue: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(currentValue);
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Clearing a non-empty field to blank is a legitimate edit — most fields are
  // empty for many contracts (a stray "NULL" should become a true blank).
  const isClear = value.trim() === "" && currentValue.trim() !== "";

  const save = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Initials needed.");
      return;
    }
    if (value.trim() === currentValue.trim()) {
      setError("Value is unchanged.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setSaving(true);
    try {
      const created = await createCorrection({
        reviewer: reviewer.trim(),
        db_row_id: dbRowId,
        field: column,
        change_type: isClear ? "clear" : currentValue.trim() ? "correct" : "fill_missing",
        proposed_value: value.trim(),
        rationale: note.trim() || (isClear ? "Cleared on the record page." : "Edited directly on the record page."),
        origin: "manual",
        source_entry_id: "",
        source_entry_key: "",
        source_quote: "",
        source_register_id: "",
        source_folio: "",
        link_review_id: "",
      });
      const id = created.proposal.proposal_id;
      await transitionCorrection(id, "approve", { reviewer: reviewer.trim() });
      await transitionCorrection(id, "apply", { reviewer: reviewer.trim() });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const isTextarea = inputType === "textarea";

  return (
    <div className={`inline-editor${isTextarea ? " is-textarea" : ""}`}>
      {inputType === "bool" ? (
        // Stored as 0/1; the corpus has no NULL booleans, so Yes/No is exact.
        <select
          ref={inputRef as React.RefObject<HTMLSelectElement>}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        >
          <option value="1">Yes</option>
          <option value="0">No</option>
        </select>
      ) : inputType === "enum" ? (
        <select
          ref={inputRef as React.RefObject<HTMLSelectElement>}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        >
          <option value="">— choose —</option>
          {(options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : isTextarea ? (
        <textarea
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          rows={Math.min(18, Math.max(6, value.split("\n").length + 2))}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      ) : (
        <input
          ref={inputRef as React.RefObject<HTMLInputElement>}
          type={inputType === "date" ? "date" : inputType === "number" ? "number" : "text"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={inputType === "date" ? "YYYY-MM-DD" : ""}
        />
      )}
      <div className="inline-editor-row">
        <input
          className="inline-editor-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why? (optional)"
          aria-label={`Note for the ${label} change`}
        />
        <input
          className="inline-editor-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
          aria-label="Your initials"
        />
        <button type="button" className="pill-button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button type="button" className="pill-button is-active" onClick={save} disabled={saving}>
          {saving ? "Saving…" : isClear ? "Clear field" : "Save"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <p className="inline-editor-foot muted">
        Applies immediately, fully audited — see Change history.
      </p>
    </div>
  );
}
