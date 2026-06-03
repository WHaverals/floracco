import type { ReviewCase } from "../types";
import ActComponentBadges from "./ActComponentBadges";
import HighlightedText from "./HighlightedText";

const FACT_FIELDS: [string, string][] = [
  ["registration_date", "Date"],
  ["folio", "Folio"],
  ["sub_type", "Type"],
  ["firm_name", "Firm"],
  ["sub_firm_name", "Sub-firm"],
  ["total", "Amount"],
];

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

export default function DatabasePanel({
  reviewCase,
  selectedDbRows,
  onToggle,
}: {
  reviewCase: ReviewCase;
  selectedDbRows: string[];
  onToggle: (dbRowId: string) => void;
}) {
  const seen = new Set<string>();
  const records = reviewCase.suggested_db_row_ids
    .map((id, index) => ({ id, dbRow: reviewCase.db_rows[index] ?? {} }))
    .filter(({ id }) => (seen.has(id) ? false : (seen.add(id), true)));
  const count = records.length;
  return (
    <section className="panel segment-panel db-segment">
      <div className="segment-head">
        <p className="eyebrow db-segment-eyebrow">
          <span>
            Database record{count === 1 ? "" : "s"} · {count || "none"} to check
          </span>
          <ActComponentBadges reviewCase={reviewCase} />
        </p>
      </div>
      <div className="db-record-list">
        {records.length === 0 ? (
          <p className="muted">No database record is suggested for this Word segment.</p>
        ) : null}
        {records.map(({ id: dbRowId, dbRow }, index) => {
          const narrative =
            String(dbRow.document ?? "") ||
            (index === 0
              ? value(reviewCase.row, "top_db_document_text") || value(reviewCase.row, "suggested_db_documents_text")
              : "");
          const isSupported = selectedDbRows.includes(dbRowId);
          return (
            <article className={`db-record${isSupported ? " is-supported" : ""}`} key={`${dbRowId}-${index}`}>
              <div className="db-record-head">
                <strong className="db-record-id">{dbRowId}</strong>
                <button
                  type="button"
                  className={`support-toggle${isSupported ? " is-on" : ""}`}
                  aria-pressed={isSupported}
                  onClick={() => onToggle(dbRowId)}
                >
                  {isSupported ? "✓ Supported" : "Mark supported"}
                </button>
              </div>
              <dl className="fact-row db-facts">
                {FACT_FIELDS.map(([field, label]) =>
                  dbRow[field] !== undefined && dbRow[field] !== null && dbRow[field] !== "" ? (
                    <div key={field}>
                      <dt>{label}</dt>
                      <dd>{String(dbRow[field])}</dd>
                    </div>
                  ) : null,
                )}
              </dl>
              {narrative ? (
                <div className="db-record-text">
                  <HighlightedText highlights={reviewCase.highlight_values} text={narrative} />
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
