import type { ReviewCase } from "../types";

/** Banner for DB-side cases ("DB row needs a Word link").
 *
 * These reverse the usual flow: the row is a DB record with no confirmed Word
 * link, and the Word panel shows a *candidate* entry the matcher found by text
 * (or nothing). The decision frame ("Mark supported" / "None match") otherwise
 * reads as if the Word entry were authoritative, so this states the direction.
 */
export default function DbSideHint({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  if (String(row.packet_section ?? "") !== "DB-only review") {
    return null;
  }
  const hasCandidate = String(row.word_entry_text ?? "").trim() !== "";
  const dbRow = String(row.top_db_row_id ?? "");

  return (
    <div className="db-side-hint" role="status">
      <strong>Unlinked database row.</strong>{" "}
      {hasCandidate ? (
        <>
          {dbRow ? <code>{dbRow}</code> : "This row"} has no confirmed Word link. The Word panel shows a{" "}
          <em>candidate the matcher found by text</em> — if it is the same act, tick the row to link it; if not,
          choose <em>None match</em>.
        </>
      ) : (
        <>
          No candidate Word entry was found for {dbRow ? <code>{dbRow}</code> : "this row"} — likely a missing Word
          entry, a segmentation gap, or out of scope. Choose <em>None match</em> to flag it for investigation.
        </>
      )}
    </div>
  );
}
