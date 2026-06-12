import { useEffect, useState } from "react";
import { loadWordEntry } from "../api";
import type { DbLinkStatus, DbWordSource, WordEntryDetail } from "../types";
import TrackedText from "./TrackedText";

const STATUS_LABEL: Record<DbLinkStatus, string> = {
  confirmed: "Attached",
  proposed: "Suggested",
  rejected: "Rejected",
};

/* One frozen Word summary, collapsed to a single line under the DB narrative.
 *
 * The summary is context, not a co-equal column: collapsed it costs one row;
 * expanded it renders the full frozen text right beneath the narrative for
 * comparison, with the editorial record (tracked changes, comments) visible
 * via the Clean | Tracked toggle. Lazy-loaded on first expand.
 */
export default function WordSummaryInline({ source }: { source: DbWordSource }) {
  const [open, setOpen] = useState(false);
  const [entry, setEntry] = useState<WordEntryDetail | null>(null);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"clean" | "tracked">("clean");

  useEffect(() => {
    if (!open || entry || error) return;
    loadWordEntry(source.source_entry_id)
      .then(setEntry)
      .catch((err: Error) => setError(err.message));
  }, [open, entry, error, source.source_entry_id]);

  const rich = entry?.rich ?? null;
  const hasEditorialRecord = Boolean(
    rich && (rich.has_revisions || rich.comments.length > 0 || rich.notes.length > 0),
  );
  const headline = [source.label, source.date].filter(Boolean).join(" · ") || source.source_entry_id;

  return (
    <div className={`ws-inline is-${source.status}${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="ws-inline-bar"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={source.source_entry_id}
      >
        <span className={`db-source-badge is-${source.status}`}>{STATUS_LABEL[source.status]}</span>
        <span className="ws-inline-headline">{headline}</span>
        {source.folio && <span className="ws-inline-folio muted">cc. {source.folio}</span>}
        {(source.comment_count ?? 0) > 0 && (
          <span
            className="ws-inline-comments"
            title="This summary carries editorial comments (often paleographic doubts) — expand to read them"
          >
            💬 {source.comment_count}
          </span>
        )}
        {source.strength != null && source.status !== "confirmed" && (
          <span className="db-source-strength">text {Math.round(source.strength * 100)}%</span>
        )}
        <span className="ws-inline-chevron" aria-hidden="true">
          {open ? "▴ Hide" : "▾ Show"}
        </span>
      </button>

      {open && (
        <div className="ws-inline-body">
          {error && <p className="error-text">{error}</p>}
          {!entry && !error && <p className="muted">Loading…</p>}
          {entry && (
            <>
              {hasEditorialRecord && rich && (
                <div className="ws-inline-tools">
                  <span className="muted ws-inline-note">
                    Editorial record:{" "}
                    {[
                      rich.summary.insertions && `${rich.summary.insertions} ins.`,
                      rich.summary.deletions && `${rich.summary.deletions} del.`,
                      rich.summary.moves && `${rich.summary.moves} moves`,
                      rich.comments.length && `${rich.comments.length} comment${rich.comments.length === 1 ? "" : "s"}`,
                      rich.notes.length && `${rich.notes.length} note${rich.notes.length === 1 ? "" : "s"}`,
                    ]
                      .filter(Boolean)
                      .join(", ")}
                  </span>
                  <div className="word-mode-toggle" role="group" aria-label="Reading mode">
                    <button
                      type="button"
                      className={mode === "clean" ? "mode-chip is-active" : "mode-chip"}
                      onClick={() => setMode("clean")}
                    >
                      Clean
                    </button>
                    <button
                      type="button"
                      className={mode === "tracked" ? "mode-chip is-active" : "mode-chip"}
                      onClick={() => setMode("tracked")}
                    >
                      Tracked changes
                    </button>
                  </div>
                </div>
              )}
              {hasEditorialRecord && rich ? (
                <TrackedText rich={rich} highlights={[]} mode={mode} />
              ) : (
                <p className="reading-text narrative db-narrative">{entry.text}</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
