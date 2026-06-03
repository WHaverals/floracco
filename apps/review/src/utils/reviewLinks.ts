export const VERIFY_DATE_FOLIO_BUCKET = "Likely match — verify date/folio";

export const CONFIRM_MULTI_ROW_BUCKET = "Confirm multi-row link";

export const DATE_FOLIO_REASON_CODES = new Set(["registration_date_differs", "folio_differs"]);

export function reconcileReviewId(sourceEntryKey: string, dbRowId: string): string {
  return `${sourceEntryKey}__${dbRowId}`;
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

export function isVerifyDateFolioBucket(bucket: string): boolean {
  return bucket === VERIFY_DATE_FOLIO_BUCKET;
}

export function isConfirmMultiRowBucket(bucket: string): boolean {
  return bucket === CONFIRM_MULTI_ROW_BUCKET;
}
