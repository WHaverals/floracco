import type { ReviewCase } from "../types";
import {
  CONFIRM_MULTI_ROW_BUCKET,
  isConfirmMultiRowBucket,
  isVerifyDateFolioBucket,
  VERIFY_DATE_FOLIO_BUCKET,
} from "./reviewLinks";
import { bestDbRowId, isGenuineMultiAct, primaryDbRowIds, shouldShowActComponentMap } from "./actComponents";

export const AMBIGUOUS_BUCKET = "Ambiguous match to choose";
export const CONFLICTS_BUCKET = "Match has conflicts to resolve";
export const WEAK_MATCH_BUCKET = "Word entry with weak or rejected DB candidates";
export const NO_DB_MATCH_BUCKET = "Word entry with no DB match";
export const WORD_ONLY_BUCKET = "Expected Word-only (non-accomandita)";

const GUESS_LABEL: Record<string, string> = {
  new_contract: "nuova",
  termination: "disdetta",
  renewal: "rinnovo",
  balance: "bilancio",
  modification: "modifica",
  assignment: "cessione",
  ratification: "ratifica",
};

/** DB sub_type / table values compatible with a Word event_label_guess. */
const GUESS_DB_TYPES: Record<string, Set<string>> = {
  new_contract: new Set(["contract"]),
  termination: new Set(["termination"]),
  renewal: new Set(["renewal"]),
  balance: new Set(["balance"]),
  modification: new Set(["variation", "modification"]),
  assignment: new Set(["assignment", "cession"]),
  ratification: new Set(["ratification"]),
};

export function humanActType(labelGuess: string): string {
  return GUESS_LABEL[labelGuess] ?? labelGuess.replace(/_/g, " ");
}

export function caseBarQuestion(reviewCase: ReviewCase): string {
  const bucket = String(reviewCase.row.recommended_review_bucket ?? "");
  // Field-verification tier: the link is trusted; only metadata differs. The
  // compare hint below names the specific field, so keep this short.
  if (isVerifyDateFolioBucket(bucket)) {
    return "Confirm the link — only the date or folio differs. Fix the field afterwards in Corrections.";
  }
  // Decision tiers come BEFORE the multi-act framing. A conflicting or ambiguous
  // case must lead with that — "confirm each row" reads as "just approve them",
  // which is exactly wrong when the rows conflict or are uncertain. (A multi-act
  // entry can still be ambiguous or conflicting; the bucket is authoritative.)
  if (bucket === CONFLICTS_BUCKET) {
    return "This link has a recorded conflict — read both sides before confirming.";
  }
  if (bucket === AMBIGUOUS_BUCKET) {
    return "Several database rows could match — mark only the rows the Word segment supports.";
  }
  if (bucket === WEAK_MATCH_BUCKET || bucket === NO_DB_MATCH_BUCKET) {
    return "The matcher is unsure — decide whether any database row fits this Word segment.";
  }
  if (bucket === WORD_ONLY_BUCKET) {
    return "This Word entry may have no accomandita database row — confirm it should stay Word-only.";
  }
  // Confirm tiers: a clean combined act, or a single act with sibling candidates.
  if (isGenuineMultiAct(reviewCase)) {
    return "Two or more acts in one Word entry — confirm each database row matches the bracket label.";
  }
  if (reviewCase.suggested_db_row_ids.length > 1) {
    return "Several sibling rows could match this one act — pick the row this entry describes (usually one).";
  }
  if (isConfirmMultiRowBucket(bucket)) {
    return "Confirm whether this Word entry supports the suggested database row.";
  }
  if (bucket === "Candidate match to confirm") {
    return "Spot-check this candidate link before using it for field reconciliation.";
  }
  if (bucket.startsWith("Expected DB-only") || bucket === "DB row may have Word evidence") {
    return "This database row sits outside the normal Word link — check whether a Word entry exists.";
  }
  return "Is this database record supported by the Word segment?";
}

/** Which DB rows start toggled "supported" when a case opens.
 *
 * The guiding rule is that over-selecting is dangerous (one click confirms a
 * wrong link) while under-selecting is cheap (the reviewer ticks another box).
 * So we only pre-select the matcher's `primary` links: for a genuine multi-act
 * entry that is every primary (one per act); for a single act with several
 * sibling candidates the matcher leaves one primary (the chosen twin) and demotes
 * the rest to `alternative`, which stay unselected; and for low-confidence buckets
 * we pre-select nothing.
 */
export function defaultSelectedDbRowIds(reviewCase: ReviewCase): string[] {
  const bucket = String(reviewCase.row.recommended_review_bucket ?? "");
  const suggestedIds = reviewCase.suggested_db_row_ids;
  if (
    bucket === AMBIGUOUS_BUCKET ||
    bucket === CONFLICTS_BUCKET ||
    bucket === WEAK_MATCH_BUCKET ||
    bucket === NO_DB_MATCH_BUCKET
  ) {
    return [];
  }
  if (suggestedIds.length <= 1) {
    return [...suggestedIds];
  }
  if (isGenuineMultiAct(reviewCase)) {
    const primaries = primaryDbRowIds(reviewCase);
    return primaries.length > 0 ? primaries : [...suggestedIds];
  }
  const best = bestDbRowId(reviewCase);
  return best ? [best] : [];
}

export function isRoutineMultiRowDiagnostic(detail: string): boolean {
  return detail.includes("word_entry_combines_multiple_db_rows");
}

export function formatFlaggedReason(bucket: string, diagnosticDetail: string): string | null {
  if (!diagnosticDetail.trim()) {
    return null;
  }
  if (isRoutineMultiRowDiagnostic(diagnosticDetail) && isConfirmMultiRowBucket(bucket)) {
    return "Routine combined act — one Word entry maps to several database rows (expected). Confirm each row below.";
  }
  if (isRoutineMultiRowDiagnostic(diagnosticDetail)) {
    return "This Word entry has multiple proposed database links — confirm they belong together.";
  }
  return diagnosticDetail;
}

export type TypeMismatch = {
  dbRowId: string;
  wordLabel: string;
  dbType: string;
};

function dbTypeForRow(dbRowId: string, dbRow: Record<string, string | number | null>): string {
  if (dbRowId.startsWith("contract:")) {
    return "contract";
  }
  const subType = String(dbRow.sub_type ?? "").trim().toLowerCase();
  return subType || "sub-contract";
}

export function wordDbTypeMismatches(reviewCase: ReviewCase): TypeMismatch[] {
  const wordGuess = String(reviewCase.row.entry_type_interpretation ?? "").trim().toLowerCase();
  if (!wordGuess) {
    return [];
  }
  const allowed = GUESS_DB_TYPES[wordGuess];
  if (!allowed) {
    return [];
  }
  const mismatches: TypeMismatch[] = [];
  reviewCase.suggested_db_row_ids.forEach((dbRowId, index) => {
    const dbRow = reviewCase.db_rows[index] ?? {};
    const dbType = dbTypeForRow(dbRowId, dbRow);
    if (!allowed.has(dbType)) {
      mismatches.push({
        dbRowId,
        wordLabel: humanActType(wordGuess),
        dbType,
      });
    }
  });
  return mismatches;
}

export { VERIFY_DATE_FOLIO_BUCKET, CONFIRM_MULTI_ROW_BUCKET };
