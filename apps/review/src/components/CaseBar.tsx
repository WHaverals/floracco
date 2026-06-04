import type { ReviewCase } from "../types";
import { caseBarQuestion } from "../utils/reconcileUx";
import { shortReviewBucket } from "../utils/reviewBuckets";
import { isVerifyDateFolioBucket } from "../utils/reviewLinks";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

type Props = {
  reviewCase: ReviewCase;
  index: number;
  total: number;
  isReviewed: boolean;
  hasImage: boolean;
  showImage: boolean;
  onToggleImage: () => void;
  onPrevious: () => void;
  onNext: () => void;
  canPrevious: boolean;
  canNext: boolean;
};

export default function CaseBar({
  reviewCase,
  index,
  total,
  isReviewed,
  hasImage,
  showImage,
  onToggleImage,
  onPrevious,
  onNext,
  canPrevious,
  canNext,
}: Props) {
  const row = reviewCase.row;
  const bucket = value(row, "recommended_review_bucket");
  const verifyDateFolio = isVerifyDateFolioBucket(bucket);
  const conflicts = value(row, "top_match_conflicts_plain_language");
  const question = caseBarQuestion(reviewCase);

  return (
    <header className="case-bar">
      <div className="case-bar-titles">
        <h1 className="case-bar-question">{question}</h1>
        <p className="case-bar-context">
          <span className="case-bar-bucket" title={bucket}>
            {shortReviewBucket(bucket)}
          </span>
          <span className="case-bar-context-sep"> · </span>
          <strong>{value(row, "register_id")}</strong>
          {verifyDateFolio && conflicts ? <span className="case-bar-conflicts"> · {conflicts}</span> : null}
          {isReviewed ? <span className="reviewed-flag"> · Reviewed</span> : null}
        </p>
      </div>

      <div className="case-bar-tools">
        <button
          type="button"
          className={`pill-button${showImage ? " is-active" : ""}`}
          disabled={!hasImage}
          aria-pressed={showImage}
          onClick={onToggleImage}
          title={hasImage ? "Show/hide the manuscript image" : "No image candidate for this case"}
        >
          Manuscript
        </button>
        <div className="case-bar-nav">
          <button type="button" onClick={onPrevious} disabled={!canPrevious} aria-label="Previous case">
            ◀
          </button>
          <span className="case-counter">
            {total ? index + 1 : 0} / {total}
          </span>
          <button type="button" onClick={onNext} disabled={!canNext} aria-label="Next case">
            ▶
          </button>
        </div>
      </div>
    </header>
  );
}
