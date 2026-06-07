export const VERIFY_FIELD_BUCKET = "Verify a field";

export const CONFIRM_COMBINED_BUCKET = "Confirm combined act";

export const DATE_FOLIO_REASON_CODES = new Set(["registration_date_differs", "folio_differs"]);

/** Deep-link id for a Word-entry reconcile case.
 *
 * A Word-entry case's `review_id` is its content-stable `source_entry_key` ALONE
 * (the server stopped appending the volatile suggested-link string — see
 * `review_server.review_id_for`). The `dbRowId` is kept in the signature for call
 * sites that still pass it, but it is not part of the id. */
export function reconcileReviewId(sourceEntryKey: string, _dbRowId?: string): string {
  return sourceEntryKey;
}

export function correctionsHandoffUrl(sourceEntryId: string, dbRowId: string, field?: string): string {
  const params = new URLSearchParams({
    source_entry_id: sourceEntryId,
    db_row_id: dbRowId,
  });
  if (field) {
    params.set("field", field);
  }
  return `/corrections?${params.toString()}`;
}

export function isVerifyFieldBucket(bucket: string): boolean {
  return bucket === VERIFY_FIELD_BUCKET;
}

export function isConfirmCombinedBucket(bucket: string): boolean {
  return bucket === CONFIRM_COMBINED_BUCKET;
}
