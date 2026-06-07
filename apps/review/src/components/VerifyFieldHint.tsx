import type { ReviewCase } from "../types";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "").trim();
}

/** Compact "which field differs" hint for the verify-a-field tier.
 *
 * The case-bar title already says "only a field differs — fix in Corrections";
 * this shows *which*. For date/folio it renders a Word→DB value compare; for the
 * conflicts that have no clean two-value compare (register, event type, malformed
 * metadata, low text similarity) it names the conflict so the reviewer still
 * knows what to check. (Before this, the hint only handled date/folio and showed
 * nothing useful for ~12% of verify cases.)
 */
export default function VerifyFieldHint({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  const conflicts = value(row, "top_match_conflicts_plain_language");
  const lc = conflicts.toLowerCase();
  const dbRow = reviewCase.db_rows[0];

  const fields: { label: string; word: string; db: string }[] = [];
  if (lc.includes("date")) {
    fields.push({
      label: "Date",
      word: value(row, "word_registration_date"),
      db: dbRow?.registration_date ? String(dbRow.registration_date) : "",
    });
  }
  if (lc.includes("folio")) {
    fields.push({
      label: "Folio",
      word: value(row, "word_folio_range"),
      db: dbRow?.folio ? String(dbRow.folio) : "",
    });
  }

  // Conflicts with no clean two-value compare — name them instead.
  const mentionsOther = /\bregister\b|\btype\b|metadat|similar|narrative/i.test(conflicts);
  const showNamed = conflicts !== "" && (mentionsOther || fields.length === 0);

  if (fields.length === 0 && !conflicts) {
    return null;
  }

  return (
    <div className="verify-field-hint">
      {showNamed ? <p className="verify-field-conflict">{conflicts}</p> : null}
      {fields.length ? (
        <dl className="verify-field-compare">
          {fields.map(({ label, word, db }) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>
                <span className="vdf-word">{word || "—"}</span>
                <span className="vdf-arrow" aria-hidden="true">→</span>
                <span className="vdf-db">{db || "—"}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}
