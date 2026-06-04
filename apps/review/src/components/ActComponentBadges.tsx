import type { ReviewCase } from "../types";
import { actBadgeTitle, shortDbRowId, shouldShowActComponentMap } from "../utils/actComponents";

export default function ActComponentBadges({
  reviewCase,
  selectedDbRows = [],
  onJumpToRecord,
}: {
  reviewCase: ReviewCase;
  selectedDbRows?: string[];
  onJumpToRecord?: (dbRowId: string) => void;
}) {
  if (!shouldShowActComponentMap(reviewCase)) {
    return null;
  }

  const components = reviewCase.act_components ?? [];

  return (
    <span className="act-badge-row" aria-label="Word act labels mapped to database rows">
      {components.map((component, index) => (
        <span className="act-badge-pair" key={`${component.label_display}-${index}`} title={actBadgeTitle(component)}>
          <span className="act-chip-sm act-chip-word">{component.label_display}</span>
          <span className="act-badge-arrow" aria-hidden="true">
            →
          </span>
          {component.suggested_db_row_id ? (
            onJumpToRecord ? (
              <button
                type="button"
                className={`act-chip-sm act-chip-db act-chip-jump${
                  selectedDbRows.includes(component.suggested_db_row_id) ? " is-supported" : ""
                }`}
                onClick={() => onJumpToRecord(component.suggested_db_row_id!)}
                title={component.suggested_db_row_id}
              >
                {shortDbRowId(component.suggested_db_row_id)}
                {selectedDbRows.includes(component.suggested_db_row_id) ? " ✓" : ""}
              </button>
            ) : (
              <span className="act-chip-sm act-chip-db">{shortDbRowId(component.suggested_db_row_id)}</span>
            )
          ) : (
            <span className="act-chip-sm act-chip-unmapped">?</span>
          )}
        </span>
      ))}
    </span>
  );
}
