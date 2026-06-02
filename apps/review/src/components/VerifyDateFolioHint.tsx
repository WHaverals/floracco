import type { ReviewCase } from "../types";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "").trim();
}

export default function VerifyDateFolioHint({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  const conflicts = value(row, "top_match_conflicts_plain_language");
  const wordDate = value(row, "word_registration_date");
  const wordFolio = value(row, "word_folio_range");
  const dbRow = reviewCase.db_rows[0];
  const dbDate = dbRow?.registration_date ? String(dbRow.registration_date) : "";
  const dbFolio = dbRow?.folio ? String(dbRow.folio) : "";

  return (
    <div className="verify-date-folio-hint">
      <p>
        The matcher believes this is the right row — only metadata differs. After you confirm the link, fix the
        field in <strong>Corrections</strong>.
      </p>
      {conflicts ? <p className="verify-date-folio-conflicts">{conflicts}</p> : null}
      <dl className="verify-date-folio-compare">
        <div>
          <dt>Word date</dt>
          <dd>{wordDate || "—"}</dd>
        </div>
        <div>
          <dt>DB date</dt>
          <dd>{dbDate || "—"}</dd>
        </div>
        <div>
          <dt>Word folio</dt>
          <dd>{wordFolio || "—"}</dd>
        </div>
        <div>
          <dt>DB folio</dt>
          <dd>{dbFolio || "—"}</dd>
        </div>
      </dl>
    </div>
  );
}
