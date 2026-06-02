import type { ReviewCase } from "../types";

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
          <h1 className="case-bar-question">Is this database record supported by the Word segment?</h1>
          <p className="case-bar-context">
            <strong>{value(row, "register_id")}</strong>
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
