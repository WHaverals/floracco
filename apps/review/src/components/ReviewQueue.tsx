import type { CasePreview, ReviewSummary } from "../types";
import { orderReviewBuckets, shortReviewBucket } from "../utils/reviewBuckets";
import { caseLabel } from "../utils/reviewLabels";

type Filters = {
  bucket: string;
  register: string;
  reviewed: string;
  search: string;
};

type Props = {
  summary: ReviewSummary | null;
  cases: CasePreview[];
  currentCase: CasePreview | null;
  currentIndex: number;
  total: number;
  filters: Filters;
  selectedReviewId: string;
  queueOpen: boolean;
  onToggleQueue: () => void;
  onFilterChange: (filters: Filters) => void;
  onSelect: (reviewId: string) => void;
  onPrevious: () => void;
  onNext: () => void;
};

export default function ReviewQueue({
  summary,
  cases,
  currentCase,
  currentIndex,
  total,
  filters,
  selectedReviewId,
  queueOpen,
  onToggleQueue,
  onFilterChange,
  onSelect,
  onPrevious,
  onNext,
}: Props) {
  const update = (key: keyof Filters, value: string) => onFilterChange({ ...filters, [key]: value });
  const reviewed = summary?.reviewed_cases ?? 0;
  const allCases = summary?.total_cases ?? 0;
  const progress = allCases ? Math.round((reviewed / allCases) * 100) : 0;

  return (
    <aside className={`queue${queueOpen ? "" : " is-collapsed"}`}>
      <div className="queue-header">
        {queueOpen ? <p className="eyebrow">Review queue</p> : null}
        <button
          type="button"
          className="queue-collapse-btn"
          aria-label={queueOpen ? "Hide review queue" : "Show review queue"}
          aria-expanded={queueOpen}
          onClick={onToggleQueue}
          title={queueOpen ? "Collapse queue" : "Expand queue"}
        >
          {queueOpen ? "‹" : "›"}
        </button>
      </div>

      {queueOpen ? (
        <div className="queue-content">
          <div className="progress-card">
            <div className="progress-label">
              <span>Overall progress</span>
              <strong>{progress}%</strong>
            </div>
            <progress max={allCases || 1} value={reviewed} />
            <span>
              {reviewed} of {allCases} reviewed
            </span>
          </div>

          <label>
            Search
            <input value={filters.search} onChange={(event) => update("search", event.target.value)} placeholder="Name, ID, folio..." />
          </label>
          <label>
            Bucket
            <select value={filters.bucket} onChange={(event) => update("bucket", event.target.value)}>
              <option>All</option>
              {orderReviewBuckets(summary?.buckets ?? []).map((bucket) => (
                <option key={bucket} value={bucket} title={bucket}>
                  {shortReviewBucket(bucket)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Register
            <select value={filters.register} onChange={(event) => update("register", event.target.value)}>
              <option>All</option>
              {summary?.registers.map((register) => (
                <option key={register}>{register}</option>
              ))}
            </select>
          </label>
          <label>
            Status
            <select value={filters.reviewed} onChange={(event) => update("reviewed", event.target.value)}>
              <option value="unreviewed">Unreviewed</option>
              <option value="reviewed">Reviewed</option>
              <option value="all">All</option>
            </select>
          </label>

          <div className="queue-navigation">
            <span>
              Case {cases.length ? currentIndex + 1 : 0} of {total} in this filtered queue
            </span>
            <div className="nav-buttons">
              <button disabled={currentIndex <= 0} onClick={onPrevious} type="button">
                Previous
              </button>
              <button disabled={!cases.length || currentIndex >= cases.length - 1} onClick={onNext} type="button">
                Next
              </button>
            </div>
          </div>

          <div className="current-case-card">
            <p className="eyebrow">Current case</p>
            {currentCase ? (
              <>
                <strong title={currentCase.recommended_review_bucket}>
                  {shortReviewBucket(currentCase.recommended_review_bucket)}
                </strong>
                <span>{caseLabel(currentCase).primary}</span>
                <span title={currentCase.source_entry_id || currentCase.suggested_db_row_ids}>
                  {caseLabel(currentCase).secondary}
                </span>
              </>
            ) : (
              <span>No case matches the current filters.</span>
            )}
          </div>

          <ul className="queue-list" aria-label="Cases in this filtered queue">
            {cases.map((item) => {
              const isActive = item.review_id === selectedReviewId;
              const label = caseLabel(item);
              return (
                <li key={item.review_id}>
                  <button
                    type="button"
                    className={`queue-list-item${isActive ? " is-active" : ""}${item.is_reviewed ? " is-reviewed" : ""}`}
                    onClick={() => onSelect(item.review_id)}
                    aria-current={isActive}
                  >
                    <span
                      className="queue-list-text"
                      title={item.source_entry_id || item.suggested_db_row_ids || item.register_id}
                    >
                      <strong>{label.primary}</strong>
                      {label.secondary ? <span>{label.secondary}</span> : null}
                      <span title={item.recommended_review_bucket}>{shortReviewBucket(item.recommended_review_bucket)}</span>
                    </span>
                    {item.is_reviewed ? (
                      <span className="queue-check" aria-label="Reviewed">
                        ✓
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </aside>
  );
}
