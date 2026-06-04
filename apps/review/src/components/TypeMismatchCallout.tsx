import type { ReviewCase } from "../types";
import { wordDbTypeMismatches } from "../utils/reconcileUx";

export default function TypeMismatchCallout({ reviewCase }: { reviewCase: ReviewCase }) {
  const mismatches = wordDbTypeMismatches(reviewCase);
  if (!mismatches.length) {
    return null;
  }
  return (
    <div className="type-mismatch-callout" role="status">
      <strong>Event type differs.</strong>{" "}
      {mismatches.map((item, index) => (
        <span key={item.dbRowId}>
          {index > 0 ? " · " : null}
          Word suggests <em>{item.wordLabel}</em> but {item.dbRowId} is <em>{item.dbType}</em>
        </span>
      ))}
      {" — "}
      worth checking before you confirm.
    </div>
  );
}
