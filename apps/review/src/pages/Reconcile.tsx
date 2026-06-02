import { useCallback, useEffect, useMemo, useState } from "react";
import { loadCase, loadCases, loadSummary, saveDecision } from "../api";
import CaseBar from "../components/CaseBar";
import DatabasePanel from "../components/DatabasePanel";
import DecisionBar from "../components/DecisionBar";
import ImagePanel from "../components/ImagePanel";
import MatchSummary from "../components/MatchSummary";
import ReviewQueue from "../components/ReviewQueue";
import WordPanel from "../components/WordPanel";
import type { CasePreview, DecisionPayload, ReviewCase, ReviewSummary } from "../types";

const DEFAULT_FILTERS = {
  priority: "All",
  bucket: "All",
  register: "All",
  reviewed: "unreviewed",
  search: "",
};

export default function Reconcile() {
  const [summary, setSummary] = useState<ReviewSummary | null>(null);
  const [cases, setCases] = useState<CasePreview[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedReviewId, setSelectedReviewId] = useState("");
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [selectedDbRows, setSelectedDbRows] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [showImage, setShowImage] = useState(false);
  const [queueOpen, setQueueOpen] = useState(true);

  const selectedIndex = cases.findIndex((item) => item.review_id === selectedReviewId);
  const currentIndex = selectedIndex >= 0 ? selectedIndex : 0;
  const currentCase = cases[currentIndex] ?? null;
  const canPrevious = currentIndex > 0;
  const canNext = cases.length > 0 && currentIndex < cases.length - 1;

  const params = useMemo(() => {
    const values = new URLSearchParams();
    values.set("priority", filters.priority);
    values.set("bucket", filters.bucket);
    values.set("register", filters.register);
    values.set("reviewed", filters.reviewed);
    values.set("search", filters.search);
    return values;
  }, [filters]);

  useEffect(() => {
    loadSummary().then(setSummary).catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    loadCases(params)
      .then((response) => {
        setCases(response.cases);
        setTotal(response.total);
        setSelectedReviewId(response.cases[0]?.review_id ?? "");
      })
      .catch((err: Error) => setError(err.message));
  }, [params]);

  useEffect(() => {
    if (!selectedReviewId) {
      setReviewCase(null);
      return;
    }
    setShowImage(false);
    loadCase(selectedReviewId)
      .then((response) => {
        setReviewCase(response);
        setSelectedDbRows(response.suggested_db_row_ids);
        setMessage("");
      })
      .catch((err: Error) => setError(err.message));
  }, [selectedReviewId]);

  const goPrevious = useCallback(() => {
    setSelectedReviewId((current) => {
      const index = cases.findIndex((item) => item.review_id === current);
      const previous = cases[(index >= 0 ? index : 0) - 1];
      return previous ? previous.review_id : current;
    });
  }, [cases]);

  const goNext = useCallback(() => {
    setSelectedReviewId((current) => {
      const index = cases.findIndex((item) => item.review_id === current);
      const next = cases[(index >= 0 ? index : 0) + 1];
      return next ? next.review_id : current;
    });
  }, [cases]);

  const toggleDbRow = useCallback((dbRowId: string) => {
    setSelectedDbRows((current) =>
      current.includes(dbRowId) ? current.filter((value) => value !== dbRowId) : [...current, dbRowId],
    );
  }, []);

  const save = async (decision: DecisionPayload) => {
    try {
      await saveDecision(decision);
      setMessage("Decision saved.");
      const latestSummary = await loadSummary();
      setSummary(latestSummary);
      setCases((current) =>
        current.map((item) => (item.review_id === selectedReviewId ? { ...item, is_reviewed: true } : item)),
      );
      goNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        return;
      }
      if (event.key === "ArrowRight" || event.key === "j") {
        goNext();
      } else if (event.key === "ArrowLeft" || event.key === "k") {
        goPrevious();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goNext, goPrevious]);

  const hasImage = (reviewCase?.image_paths.length ?? 0) > 0;
  const isReviewed = currentCase?.is_reviewed ?? false;

  return (
    <div className={`app-shell${queueOpen ? "" : " queue-collapsed"}`}>
      <ReviewQueue
        cases={cases}
        currentCase={currentCase}
        currentIndex={currentIndex}
        filters={filters}
        onFilterChange={setFilters}
        onSelect={setSelectedReviewId}
        selectedReviewId={selectedReviewId}
        onNext={goNext}
        onPrevious={goPrevious}
        summary={summary}
        total={total}
      />
      <section className="workspace">
        {reviewCase ? (
          <>
            <CaseBar
              reviewCase={reviewCase}
              index={currentIndex}
              total={total}
              isReviewed={isReviewed}
              hasImage={hasImage}
              showImage={showImage}
              onToggleImage={() => setShowImage((value) => !value)}
              queueOpen={queueOpen}
              onToggleQueue={() => setQueueOpen((value) => !value)}
              onPrevious={goPrevious}
              onNext={goNext}
              canPrevious={canPrevious}
              canNext={canNext}
            />
            <div className="reconcile-body">
              <div className={`reconcile-compare${showImage && hasImage ? " with-image" : ""}`}>
                <WordPanel reviewCase={reviewCase} />
                <DatabasePanel reviewCase={reviewCase} selectedDbRows={selectedDbRows} onToggle={toggleDbRow} />
                {showImage && hasImage ? <ImagePanel reviewCase={reviewCase} /> : null}
              </div>
              <MatchSummary reviewCase={reviewCase} />
              <DecisionBar key={selectedReviewId} reviewCase={reviewCase} selectedDbRows={selectedDbRows} onSave={save} />
            </div>
          </>
        ) : (
          <div className="empty-state large">No cases match the current filters.</div>
        )}
      </section>

      <div className="toast-region" aria-live="polite">
        {error ? (
          <div className="notice error" role="alert">
            {error}
            <button type="button" className="toast-close" onClick={() => setError("")} aria-label="Dismiss">
              ✕
            </button>
          </div>
        ) : null}
        {message ? (
          <div className="notice success">
            {message}
            <button type="button" className="toast-close" onClick={() => setMessage("")} aria-label="Dismiss">
              ✕
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
