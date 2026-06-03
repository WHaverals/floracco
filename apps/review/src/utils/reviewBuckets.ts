/** Short labels for review buckets — filter values stay the full API string. */

const REVIEW_BUCKET_SHORT: Record<string, string> = {
  "Confirm multi-row link": "Multi-row",
  "Likely match — verify date/folio": "Verify date/folio",
  "Expected Word-only (non-accomandita)": "Word-only (ok)",
  "Match has conflicts to resolve": "Conflicts",
  "Expected DB-only (outside Word corpus)": "DB-only (scope)",
  "Word entry with weak or rejected DB candidates": "Weak match",
  "DB row may have Word evidence": "DB → Word?",
  "High-confidence match": "High confidence",
  "Ambiguous match to choose": "Ambiguous",
  "DB row not in Word — review": "DB not in Word",
  "Word entry with no DB match": "No DB match",
  "Candidate match to confirm": "Candidate",
};

export function shortReviewBucket(bucket: string): string {
  return REVIEW_BUCKET_SHORT[bucket] ?? bucket;
}
