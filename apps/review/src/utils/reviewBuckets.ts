/** Short labels for review buckets — filter values stay the full API string. */

const REVIEW_BUCKET_SHORT: Record<string, string> = {
  "Choose the right row": "Choose row",
  "Verify a field": "Verify field",
  "Investigate — no clear DB match": "Investigate",
  "Confirm combined act": "Combined act",
  "Confirm the link": "Confirm link",
  "DB row needs a Word link": "DB → Word link",
  "Non-accomandita (Word-only)": "Non-accomandita",
};

export function shortReviewBucket(bucket: string): string {
  return REVIEW_BUCKET_SHORT[bucket] ?? bucket;
}

/** Buckets in review-workflow order (what to work first), not alphabetical:
 *  decide → verify a field → quick confirm → DB-side. The filter dropdown reads
 *  far better grouped this way than by the server's alphabetical sort.
 */
const BUCKET_ORDER = [
  // Decision-heavy work first, routine confirms and DB-side last.
  // Mirrors REVIEW_BUCKET_ORDER in workflows/word_pipeline.py.
  "Choose the right row",
  "Verify a field",
  "Investigate — no clear DB match",
  "Confirm combined act",
  "Confirm the link",
  "DB row needs a Word link",
  "Non-accomandita (Word-only)",
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
