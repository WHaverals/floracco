import type { ReviewCase } from "../types";
import { shortDbRowId } from "../utils/actComponents";
import { wordDbTypeMismatches } from "../utils/reconcileUx";

export default function TypeMismatchCallout({ reviewCase }: { reviewCase: ReviewCase }) {
  const mismatches = wordDbTypeMismatches(reviewCase);
  if (!mismatches.length) {
    return null;
  }

  // Collapse the per-row repetition ("Word suggests X but A is Y · … B is Y · …")
  // into one phrase per (word label → db type): "Word label X, but rows A, B are Y".
  const groups = new Map<string, { wordLabel: string; dbType: string; ids: string[] }>();
  for (const item of mismatches) {
    const key = `${item.wordLabel}|${item.dbType}`;
    const group = groups.get(key) ?? { wordLabel: item.wordLabel, dbType: item.dbType, ids: [] };
    const id = shortDbRowId(item.dbRowId);
    if (!group.ids.includes(id)) {
      group.ids.push(id);
    }
    groups.set(key, group);
  }

  return (
    <div className="type-mismatch-callout" role="status">
      <strong>Event type differs.</strong>{" "}
      {[...groups.values()].map((group, index) => {
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
  );
}
