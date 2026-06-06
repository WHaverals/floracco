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

/** Buckets in review-workflow order (what to work first), not alphabetical:
 *  decide → verify a field → quick confirm → DB-side. The filter dropdown reads
 *  far better grouped this way than by the server's alphabetical sort.
 */
const BUCKET_ORDER = [
  // Needs a decision
  "Match has conflicts to resolve",
  "Ambiguous match to choose",
  "Word entry with weak or rejected DB candidates",
  "Word entry with no DB match",
  // Verify a field
  "Likely match — verify date/folio",
  "Candidate match to confirm",
  // Quick confirm
  "Confirm multi-row link",
  "Expected Word-only (non-accomandita)",
  "High-confidence match",
  // DB-side (does a Word entry exist?)
  "DB row may have Word evidence",
  "DB row not in Word — review",
  "Expected DB-only (outside Word corpus)",
];

/** Sort bucket strings into workflow order; unknown buckets keep their original
 *  (alphabetical) order at the end so a new bucket never silently disappears. */
export function orderReviewBuckets(buckets: string[]): string[] {
  const rank = (bucket: string) => {
    const index = BUCKET_ORDER.indexOf(bucket);
    return index === -1 ? BUCKET_ORDER.length : index;
  };
  return [...buckets].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}
