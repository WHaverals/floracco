import { useRef } from "react";
import type { ReviewCase } from "../types";
import { bestDbRowId, jumpIndexDbRowIds, matchStrengthForRow, shortDbRowId } from "../utils/actComponents";
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
  const recordRefs = useRef<Record<string, HTMLElement | null>>({});
  const seen = new Set<string>();
  const records = reviewCase.suggested_db_row_ids
    .map((id, index) => ({ id, dbRow: reviewCase.db_rows[index] ?? {} }))
    .filter(({ id }) => (seen.has(id) ? false : (seen.add(id), true)));
  const count = records.length;
  const supportedCount = records.filter(({ id }) => selectedDbRows.includes(id)).length;
  const jumpIds = jumpIndexDbRowIds(reviewCase);
  const bestId = count > 1 ? bestDbRowId(reviewCase) : null;

  const strengthBand = (strength: number | null): "ok" | "mid" | "alert" => {
    if (strength === null) return "mid";
    if (strength >= 0.75) return "ok";
    if (strength >= 0.5) return "mid";
    return "alert";
  };

  const scrollToRecord = (dbRowId: string) => {
    recordRefs.current[dbRowId]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  // Clicking the card toggles support — a larger, easier target than the button
  // alone — but never when the user is selecting narrative text or clicking the
  // explicit button/link (those handle themselves), so reading stays uninterrupted.
  const handleCardClick = (dbRowId: string, event: React.MouseEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest("button, a")) {
      return;
    }
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) {
      return;
    }
    onToggle(dbRowId);
  };

  return (
    <section className="panel segment-panel db-segment">
      <div className="segment-head db-segment-head">
        <p className="eyebrow">
          Database record{count === 1 ? "" : "s"} · {count || "none"} to check
          {count > 1 ? (
            <span className="db-supported-count">
              {" "}
              · {supportedCount} of {count} supported
            </span>
          ) : null}
        </p>
        <ActComponentBadges
          reviewCase={reviewCase}
          selectedDbRows={selectedDbRows}
          onJumpToRecord={scrollToRecord}
        />
        {jumpIds.length > 1 ? (
          <div className="db-record-index" role="navigation" aria-label="Jump to other suggested database records">
            {jumpIds.map((id) => {
              const isSupported = selectedDbRows.includes(id);
              return (
                <button
                  type="button"
                  key={id}
                  className={`db-record-index-chip${isSupported ? " is-supported" : ""}`}
                  onClick={() => scrollToRecord(id)}
                  title={id}
                >
                  {shortDbRowId(id)}
                  {isSupported ? " ✓" : ""}
                </button>
              );
            })}
          </div>
        ) : null}
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
          const strength = matchStrengthForRow(reviewCase, dbRowId);
          const isBest = count > 1 && dbRowId === bestId;
          return (
            <article
              className={`db-record is-clickable${isSupported ? " is-supported" : ""}`}
              id={`db-record-${dbRowId.replace(":", "-")}`}
              key={`${dbRowId}-${index}`}
              onClick={(event) => handleCardClick(dbRowId, event)}
              ref={(node) => {
                recordRefs.current[dbRowId] = node;
              }}
            >
              <div className="db-record-head">
                <strong className="db-record-id">{dbRowId}</strong>
                {strength !== null ? (
                  <span className={`db-record-strength band-${strengthBand(strength)}`}>
                    Text {Math.round(strength * 100)}%
                  </span>
                ) : null}
                {isBest ? <span className="db-record-best">Closest text</span> : null}
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
