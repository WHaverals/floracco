import type { ReviewCase } from "../types";
import {
  CONFIRM_COMBINED_BUCKET,
  isConfirmCombinedBucket,
  VERIFY_FIELD_BUCKET,
} from "./reviewLinks";
import { bestDbRowId, primaryDbRowIds } from "./actComponents";

// The seven action-first buckets (mirror workflows/word_pipeline.py).
// VERIFY_FIELD_BUCKET and CONFIRM_COMBINED_BUCKET are re-exported below.
export const CHOOSE_ROW_BUCKET = "Choose the right row";
export const INVESTIGATE_BUCKET = "Investigate — no clear DB match";
export const CONFIRM_LINK_BUCKET = "Confirm the link";
export const DB_NEEDS_WORD_BUCKET = "DB row needs a Word link";
export const NON_ACCOMANDITA_BUCKET = "Non-accomandita (Word-only)";

const GUESS_LABEL: Record<string, string> = {
  new_contract: "nuova",
  termination: "disdetta",
  renewal: "rinnovo",
  balance: "bilancio",
  modification: "modifica",
  assignment: "cessione",
  ratification: "ratifica",
  extension: "proroga",
  confirmation: "conferma",
  declaration: "dichiarazione",
  winding_up: "stralcio",
  capital_return: "restituzione capitali",
};

export function humanActType(labelGuess: string): string {
  return GUESS_LABEL[labelGuess] ?? labelGuess.replace(/_/g, " ");
}

/** One-line guidance per bucket. The bucket is authoritative (it already encodes
 *  combined-act / conflict / ambiguity), so this is a direct lookup, not a stack
 *  of heuristics. */
export function caseBarQuestion(reviewCase: ReviewCase): string {
  const bucket = String(reviewCase.row.recommended_review_bucket ?? "");
  switch (bucket) {
    case VERIFY_FIELD_BUCKET:
      return "The link looks right — only a field (date, folio, register, or type) differs. Confirm, then correct the field on the database record.";
    case CHOOSE_ROW_BUCKET:
      return "Several database rows could match — mark only the row(s) the Word segment supports.";
    case INVESTIGATE_BUCKET:
      return "The matcher couldn't place this — decide if it's Word-only, a parser miss, or a database row to create.";
    case NON_ACCOMANDITA_BUCKET:
      return "This is a non-accomandita act — confirm it should stay Word-only.";
    case DB_NEEDS_WORD_BUCKET:
      return "This database row has no Word link — check whether a Word entry exists for it (or it is a duplicate).";
    case CONFIRM_COMBINED_BUCKET:
      return "Two or more acts in one Word entry — confirm each database row matches the bracket label.";
    case CONFIRM_LINK_BUCKET:
      return "Spot-check this single match before it is used for field reconciliation.";
    default:
      return "Is this database record supported by the Word segment?";
  }
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
  // Uncertain or no-row-needed buckets: pre-select nothing.
  if (
    bucket === CHOOSE_ROW_BUCKET ||
    bucket === INVESTIGATE_BUCKET ||
    bucket === DB_NEEDS_WORD_BUCKET ||
    bucket === NON_ACCOMANDITA_BUCKET
  ) {
    return [];
  }
  // Combined act: pre-select every primary (one per act).
  if (bucket === CONFIRM_COMBINED_BUCKET) {
    const primaries = primaryDbRowIds(reviewCase);
    return primaries.length > 0 ? primaries : [...suggestedIds];
  }
  // Confirm-the-link / Verify-a-field: the single best row.
  if (suggestedIds.length <= 1) {
    return [...suggestedIds];
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
  if (isRoutineMultiRowDiagnostic(diagnosticDetail) && isConfirmCombinedBucket(bucket)) {
    return "Routine combined act — one Word entry maps to several database rows (expected). Confirm each row below.";
  }
  if (isRoutineMultiRowDiagnostic(diagnosticDetail)) {
    return "This Word entry has multiple proposed database links — confirm they belong together.";
  }
  return diagnosticDetail;
}

export type TypeRelationItem = {
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

/** Word-label ↔ DB-type verdicts, read from the matcher's per-link
 * `event_type_relation` (the pipeline is the single source of truth — it is
 * component-aware and encodes the FT-pending `DB_EVENT_TYPE_MAP`; the UI keeps
 * no label→type map of its own). `mismatch` warrants a warning; `interpretive`
 * (cessione coded as variation, …) only a quiet explanatory note. */
export function wordDbTypeRelations(reviewCase: ReviewCase): {
  mismatches: TypeRelationItem[];
  interpretive: TypeRelationItem[];
} {
  const wordGuess = String(reviewCase.row.entry_type_interpretation ?? "").trim().toLowerCase();
  const wordLabel = wordGuess ? humanActType(wordGuess) : "—";
  const metrics = reviewCase.link_metrics ?? {};
  const mismatches: TypeRelationItem[] = [];
  const interpretive: TypeRelationItem[] = [];
  reviewCase.suggested_db_row_ids.forEach((dbRowId, index) => {
    const relation = metrics[dbRowId]?.event_type_relation ?? null;
    if (relation !== "mismatch" && relation !== "interpretive") {
      return;
    }
    const item = {
      dbRowId,
      wordLabel,
      dbType: dbTypeForRow(dbRowId, reviewCase.db_rows[index] ?? {}),
    };
    (relation === "mismatch" ? mismatches : interpretive).push(item);
  });
  return { mismatches, interpretive };
}

export { VERIFY_FIELD_BUCKET, CONFIRM_COMBINED_BUCKET };
