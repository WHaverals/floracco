import type { ReviewCase } from "../types";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "").trim();
}

/** Compact Word→DB compare for the verify-date/folio tier.
 *
 * The case-bar title already says "only the date or folio differs — fix in
 * Corrections", so this no longer repeats that prose. It shows only the field(s)
 * the matcher actually flagged (a date-only conflict hides the folio row and vice
 * versa), Word value → DB value, so the reviewer sees exactly what to check.
 */
export default function VerifyDateFolioHint({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  const conflicts = value(row, "top_match_conflicts_plain_language").toLowerCase();
  const dbRow = reviewCase.db_rows[0];

  const wantsDate = conflicts.includes("date");
  const wantsFolio = conflicts.includes("folio");
  // Defensive: if the conflict text names neither, show both rather than nothing.
  const showDate = wantsDate || (!wantsDate && !wantsFolio);
  const showFolio = wantsFolio || (!wantsDate && !wantsFolio);

  const fields: { label: string; word: string; db: string }[] = [];
  if (showDate) {
    fields.push({
      label: "Date",
      word: value(row, "word_registration_date"),
      db: dbRow?.registration_date ? String(dbRow.registration_date) : "",
    });
  }
  if (showFolio) {
    fields.push({
      label: "Folio",
      word: value(row, "word_folio_range"),
      db: dbRow?.folio ? String(dbRow.folio) : "",
    });
  }

  return (
    <dl className="verify-date-folio-compare">
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
  );
}
