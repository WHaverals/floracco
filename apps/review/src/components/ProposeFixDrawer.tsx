import { useEffect, useMemo, useState } from "react";
import { createCorrection } from "../api";
import type { CorrectionChangeType, DbFieldInputType, DbWordSource } from "../types";

const CHANGE_TYPES: { id: CorrectionChangeType; label: string; hint: string }[] = [
  { id: "correct", label: "Correct", hint: "Replace the current value with a corrected one." },
  { id: "fill_missing", label: "Fill missing", hint: "Supply a value where the field is empty." },
  { id: "flag_uncertain", label: "Flag uncertain", hint: "Record doubt — never writes to the database." },
];

export type ProposeSeed = {
  dbRowId: string;
  recordTitle: string;
  fieldLabel: string;
  column: string;
  inputType: DbFieldInputType;
  options?: string[] | null;
  currentValue: string;
  wordSources: DbWordSource[];
  /** /database pre-fills the proposed value with the current value; candidates do not. */
  prefillProposed?: boolean;
  /** An adjudicated reading to pre-fill (tracked-change dates). Wins over prefillProposed. */
  initialProposedValue?: string;
  initialSourceEntryId?: string;
  initialSourceQuote?: string;
  /** The Word evidence value — shown beside the input, never written into it. */
  wordValueHint?: string | null;
};

export default function ProposeFixDrawer({
  seed,
  onClose,
  onSubmitted,
}: {
  seed: ProposeSeed;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [changeType, setChangeType] = useState<CorrectionChangeType>(
    seed.currentValue ? "correct" : "fill_missing",
  );
  const [proposedValue, setProposedValue] = useState(
    seed.initialProposedValue ?? (seed.prefillProposed ? seed.currentValue : ""),
  );
  const [rationale, setRationale] = useState("");
  const [sourceEntryId, setSourceEntryId] = useState(seed.initialSourceEntryId ?? "");
  const [sourceQuote, setSourceQuote] = useState(seed.initialSourceQuote ?? "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const selectedSource = useMemo(
    () => seed.wordSources.find((s) => s.source_entry_id === sourceEntryId) ?? null,
    [seed.wordSources, sourceEntryId],
  );

  const submit = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Please enter your initials.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setSaving(true);
    try {
      await createCorrection({
        reviewer: reviewer.trim(),
        db_row_id: seed.dbRowId,
        field: seed.column,
        change_type: changeType,
        proposed_value: proposedValue.trim(),
        rationale: rationale.trim(),
        origin: "manual",
        source_entry_id: sourceEntryId,
        source_entry_key: selectedSource?.source_entry_key ?? "",
        source_quote: sourceQuote.trim(),
        source_register_id: selectedSource?.register_id ?? "",
        source_folio: selectedSource?.folio ?? "",
        link_review_id: "",
      });
      onSubmitted();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside className="propose-drawer" onClick={(event) => event.stopPropagation()}>
        <header className="word-drawer-head">
          <div>
            <p className="eyebrow">Suggest a fix</p>
            <h3>{seed.fieldLabel}</h3>
            <p className="muted word-drawer-meta">
              {seed.recordTitle} · <code>{seed.dbRowId}</code>
            </p>
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="propose-body">
          <div className="propose-evidence">
            <div className="propose-current">
              <span className="propose-label">In the database (the value being corrected)</span>
              <span className="propose-current-value">
                {seed.currentValue ? seed.currentValue : "— (empty)"}
              </span>
            </div>
            {seed.wordValueHint && (
              <div className="propose-current propose-word">
                <span className="propose-label">Word source reads (evidence — not pre-filled)</span>
                <span className="propose-current-value">{seed.wordValueHint}</span>
              </div>
            )}
          </div>

          <label className="propose-field">
            <span className="propose-label">Change type</span>
            <select
              value={changeType}
              onChange={(event) => setChangeType(event.target.value as CorrectionChangeType)}
            >
              {CHANGE_TYPES.map((ct) => (
                <option key={ct.id} value={ct.id}>
                  {ct.label}
                </option>
              ))}
            </select>
            <span className="propose-hint">{CHANGE_TYPES.find((c) => c.id === changeType)?.hint}</span>
          </label>

          {changeType !== "flag_uncertain" && (
            <label className="propose-field">
              <span className="propose-label">Proposed value</span>
              {seed.inputType === "enum" ? (
                <select value={proposedValue} onChange={(e) => setProposedValue(e.target.value)}>
                  <option value="">— choose —</option>
                  {(seed.options ?? []).map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={seed.inputType === "date" ? "date" : seed.inputType === "number" ? "number" : "text"}
                  value={proposedValue}
                  onChange={(e) => setProposedValue(e.target.value)}
                  placeholder={seed.inputType === "date" ? "YYYY-MM-DD" : ""}
                />
              )}
            </label>
          )}

          <label className="propose-field">
            <span className="propose-label">Source (optional)</span>
            <select value={sourceEntryId} onChange={(e) => setSourceEntryId(e.target.value)}>
              <option value="">— no Word source (editorial) —</option>
              {seed.wordSources.map((s) => (
                <option key={s.source_entry_id} value={s.source_entry_id}>
                  {s.source_entry_id}
                  {s.via ? ` (via ${s.via})` : ""}
                </option>
              ))}
            </select>
          </label>

          {sourceEntryId && (
            <label className="propose-field">
              <span className="propose-label">Source quote</span>
              <textarea
                rows={2}
                value={sourceQuote}
                onChange={(e) => setSourceQuote(e.target.value)}
                placeholder="Paste the exact Word text that justifies this change."
              />
            </label>
          )}

          <label className="propose-field">
            <span className="propose-label">
              Rationale {sourceEntryId ? "(optional)" : "(required if no source)"}
            </span>
            <textarea
              rows={2}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Why is this the correct value?"
            />
          </label>

          <label className="propose-field propose-reviewer">
            <span className="propose-label">Your initials</span>
            <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="e.g. FT" />
          </label>

          {error && <p className="error-text">{error}</p>}

          <div className="propose-actions">
            <button type="button" className="pill-button" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="pill-button is-active" onClick={submit} disabled={saving}>
              {saving ? "Saving…" : "Submit proposal"}
            </button>
          </div>
          <p className="muted propose-foot">
            This records a proposal only. Nothing is written to the database until it is approved and
            applied in the Corrections tool.
          </p>
        </div>
      </aside>
    </div>
  );
}
