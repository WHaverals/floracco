import type { ReviewCase } from "../types";
import { shortDbRowId } from "../utils/actComponents";
import { wordDbTypeRelations, type TypeRelationItem } from "../utils/reconcileUx";

// Collapse the per-row repetition into one phrase per (word label → db type):
// "Word label X, but rows A, B are Y".
function groupItems(items: TypeRelationItem[]) {
  const groups = new Map<string, { wordLabel: string; dbType: string; ids: string[] }>();
  for (const item of items) {
    const key = `${item.wordLabel}|${item.dbType}`;
    const group = groups.get(key) ?? { wordLabel: item.wordLabel, dbType: item.dbType, ids: [] };
    const id = shortDbRowId(item.dbRowId);
    if (!group.ids.includes(id)) {
      group.ids.push(id);
    }
    groups.set(key, group);
  }
  return [...groups.values()];
}

export default function TypeMismatchCallout({ reviewCase }: { reviewCase: ReviewCase }) {
  const { mismatches, interpretive } = wordDbTypeRelations(reviewCase);
  if (!mismatches.length && !interpretive.length) {
    return null;
  }

  return (
    <>
      {mismatches.length ? (
        <div className="type-mismatch-callout" role="status">
          <strong>Event type differs.</strong>{" "}
          {groupItems(mismatches).map((group, index) => {
            const many = group.ids.length > 1;
            return (
              <span key={`${group.wordLabel}|${group.dbType}`}>
                {index > 0 ? "; " : null}
                Word label <em>{group.wordLabel}</em>, but {many ? "rows" : "row"} {group.ids.join(", ")}{" "}
                {many ? "are" : "is"} <em>{group.dbType}</em>
              </span>
            );
          })}
          {" — check before confirming."}
        </div>
      ) : null}
      {interpretive.length ? (
        // An expected coding, not a conflict: the DB only stores four sub-types,
        // so richer Italian labels are folded into them (mapping pending FT review).
        <p className="type-interpretive-note">
          {groupItems(interpretive).map((group, index) => {
            const many = group.ids.length > 1;
            return (
              <span key={`${group.wordLabel}|${group.dbType}`}>
                {index > 0 ? "; " : null}
                The DB codes this <em>{group.wordLabel}</em> as <em>{group.dbType}</em>
                {many ? ` (rows ${group.ids.join(", ")})` : ""}
              </span>
            );
          })}
          {" — an expected coding (label mapping pending FT review)."}
        </p>
      ) : null}
    </>
  );
}
