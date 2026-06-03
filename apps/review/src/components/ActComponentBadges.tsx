import type { ReviewCase } from "../types";
import { actBadgeTitle, shortDbRowId, shouldShowActComponentMap, unmappedDbRowIds } from "../utils/actComponents";

export default function ActComponentBadges({ reviewCase }: { reviewCase: ReviewCase }) {
  if (!shouldShowActComponentMap(reviewCase)) {
    return null;
  }

  const components = reviewCase.act_components ?? [];
  const extraDbRowIds = unmappedDbRowIds(reviewCase);

  return (
    <span className="act-badge-row" aria-label="Word act labels mapped to database rows">
      {components.map((component, index) => (
        <span className="act-badge-pair" key={`${component.label_display}-${index}`} title={actBadgeTitle(component)}>
          <span className="act-chip-sm act-chip-word">{component.label_display}</span>
          <span className="act-badge-arrow" aria-hidden="true">
            →
          </span>
          {component.suggested_db_row_id ? (
            <span className="act-chip-sm act-chip-db">{shortDbRowId(component.suggested_db_row_id)}</span>
          ) : (
            <span className="act-chip-sm act-chip-unmapped">?</span>
          )}
        </span>
      ))}
      {extraDbRowIds.map((dbRowId) => (
        <span className="act-badge-pair" key={`extra-${dbRowId}`} title={`Suggested link ${dbRowId}`}>
          <span className="act-chip-sm act-chip-db">{shortDbRowId(dbRowId)}</span>
        </span>
      ))}
    </span>
  );
}
