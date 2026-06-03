import type { ReviewCase } from "../types";
import { isConfirmMultiRowBucket, isVerifyDateFolioBucket } from "../utils/reviewLinks";
import { shouldShowActComponentMap } from "../utils/actComponents";

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
  queueOpen: boolean;
  onToggleQueue: () => void;
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
  queueOpen,
  onToggleQueue,
  onPrevious,
  onNext,
  canPrevious,
  canNext,
}: Props) {
  const row = reviewCase.row;
  const verifyDateFolio = isVerifyDateFolioBucket(value(row, "recommended_review_bucket"));
  const confirmMultiRow =
    isConfirmMultiRowBucket(value(row, "recommended_review_bucket")) || shouldShowActComponentMap(reviewCase);
  const conflicts = value(row, "top_match_conflicts_plain_language");
  let question = "Is this database record supported by the Word segment?";
  if (verifyDateFolio) {
    question =
      "Link looks correct — only date or folio differs. Confirm the link, then fix the field in Corrections.";
  } else if (confirmMultiRow) {
    question = "Two or more acts in one Word entry — confirm each database row matches the bracket label.";
  }
  return (
    <header className="case-bar">
      <div className="case-bar-main">
        <button
          type="button"
          className="icon-button"
          aria-label={queueOpen ? "Hide queue" : "Show queue"}
          aria-pressed={queueOpen}
          onClick={onToggleQueue}
        >
          ☰
        </button>
        <div className="case-bar-titles">
          <h1 className="case-bar-question">{question}</h1>
          <p className="case-bar-context">
            <strong>{value(row, "register_id")}</strong>
            {verifyDateFolio && conflicts ? <span className="case-bar-conflicts"> · {conflicts}</span> : null}
            {isReviewed ? <span className="reviewed-flag"> · Reviewed</span> : null}
          </p>
        </div>
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
