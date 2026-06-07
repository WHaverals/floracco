import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { loadCase, loadCases, loadSummary, saveDecision } from "../api";
import CaseBar from "../components/CaseBar";
import DatabasePanel from "../components/DatabasePanel";
import DbSideHint from "../components/DbSideHint";
import DecisionBar from "../components/DecisionBar";
import MatchSummary from "../components/MatchSummary";
import ReconcileHandoffStrip from "../components/ReconcileHandoffStrip";
import ReviewQueue from "../components/ReviewQueue";
import TypeMismatchCallout from "../components/TypeMismatchCallout";
import VerifyFieldHint from "../components/VerifyFieldHint";
import WordPanel from "../components/WordPanel";
import type { CasePreview, DecisionPayload, ReviewCase, ReviewSummary } from "../types";
import { defaultSelectedDbRowIds } from "../utils/reconcileUx";
import { isVerifyFieldBucket } from "../utils/reviewLinks";

const DEFAULT_FILTERS = {
  bucket: "All",
  register: "All",
  reviewed: "unreviewed",
  search: "",
};

type HandoffTarget = {
  sourceEntryId: string;
  dbRowId: string;
};

export default function Reconcile() {
  const { reviewId: routeReviewId } = useParams<{ reviewId?: string }>();
  const [summary, setSummary] = useState<ReviewSummary | null>(null);
  const [cases, setCases] = useState<CasePreview[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedReviewId, setSelectedReviewId] = useState("");
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [selectedDbRows, setSelectedDbRows] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [queueOpen, setQueueOpen] = useState(true);
  const [handoff, setHandoff] = useState<HandoffTarget | null>(null);

  const selectedIndex = cases.findIndex((item) => item.review_id === selectedReviewId);
  const currentIndex = selectedIndex >= 0 ? selectedIndex : 0;
  const currentCase = cases[currentIndex] ?? null;
  const canPrevious = currentIndex > 0;
  const canNext = cases.length > 0 && currentIndex < cases.length - 1;

  const params = useMemo(() => {
    const values = new URLSearchParams();
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
        const preferred =
          routeReviewId && response.cases.some((item) => item.review_id === routeReviewId)
            ? routeReviewId
            : response.cases[0]?.review_id ?? "";
        setSelectedReviewId(preferred);
      })
      .catch((err: Error) => setError(err.message));
  }, [params, routeReviewId]);

  useEffect(() => {
    if (!selectedReviewId) {
      setReviewCase(null);
      return;
    }
    setHandoff(null);
    loadCase(selectedReviewId)
      .then((response) => {
        setReviewCase(response);
        setSelectedDbRows(defaultSelectedDbRowIds(response));
        setMessage("");
      })
      .catch((err: Error) => setError(err.message));
  }, [selectedReviewId]);

  const goPrevious = useCallback(() => {
    setHandoff(null);
    setSelectedReviewId((current) => {
      const index = cases.findIndex((item) => item.review_id === current);
      const previous = cases[(index >= 0 ? index : 0) - 1];
      return previous ? previous.review_id : current;
    });
  }, [cases]);

  const goNext = useCallback(() => {
    setHandoff(null);
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
      const latestSummary = await loadSummary();
      setSummary(latestSummary);
      setCases((current) =>
        current.map((item) => (item.review_id === selectedReviewId ? { ...item, is_reviewed: true } : item)),
      );

      const bucket = String(reviewCase?.row.recommended_review_bucket ?? "");
      const confirmed =
        decision.next_action === "approve_link" && (decision.selected_db_row_ids?.length ?? 0) > 0;
      if (isVerifyFieldBucket(bucket) && confirmed && reviewCase) {
        setHandoff({
          sourceEntryId: String(reviewCase.row.source_entry_id ?? decision.source_entry_id),
          dbRowId: decision.selected_db_row_ids![0],
        });
        setMessage("");
      } else if (decision.next_action === "reject_link") {
        setMessage("Link rejected — saved. Rejected links show de-emphasised in Database.");
        goNext();
      } else {
        setMessage("Decision saved.");
        goNext();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (handoff) {
        return;
      }
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
  }, [goNext, goPrevious, handoff]);

  const isReviewed = currentCase?.is_reviewed ?? false;
  const showVerifyHint =
    reviewCase && isVerifyFieldBucket(String(reviewCase.row.recommended_review_bucket ?? ""));

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
        queueOpen={queueOpen}
        onToggleQueue={() => setQueueOpen((value) => !value)}
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
              onPrevious={goPrevious}
              onNext={goNext}
              canPrevious={canPrevious}
              canNext={canNext}
            />
            <div className="reconcile-body">
              {showVerifyHint ? <VerifyFieldHint reviewCase={reviewCase} /> : null}
              {handoff ? (
                <ReconcileHandoffStrip
                  dbRowId={handoff.dbRowId}
                  sourceEntryId={handoff.sourceEntryId}
                  onNext={goNext}
                  onDismiss={() => setHandoff(null)}
                />
              ) : null}
              <DbSideHint reviewCase={reviewCase} />
              <TypeMismatchCallout reviewCase={reviewCase} />
              <div className="reconcile-compare">
                <WordPanel reviewCase={reviewCase} />
                <DatabasePanel reviewCase={reviewCase} selectedDbRows={selectedDbRows} onToggle={toggleDbRow} />
              </div>
              <MatchSummary reviewCase={reviewCase} />
              <DecisionBar key={selectedReviewId} reviewCase={reviewCase} selectedDbRows={selectedDbRows} onSave={save} />
            </div>
          </>
        ) : (
          <div className="empty-state large">
            {routeReviewId ? "Case not in the current filter — reset filters or search." : "No cases match the current filters."}
          </div>
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
