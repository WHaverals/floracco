import type { ReviewCase } from "../types";
import { isGenuineMultiAct } from "../utils/actComponents";
import { formatFlaggedReason } from "../utils/reconcileUx";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

function num(row: ReviewCase["row"], key: string): number | null {
  const raw = row[key];
  if (raw === null || raw === undefined || raw === "") {
    return null;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function pct(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value * 100)}%`;
}

function band(value: number | null): { label: string; tone: "ok" | "mid" | "alert" } {
  if (value === null) return { label: "Unknown", tone: "mid" };
  if (value >= 0.75) return { label: "Strong", tone: "ok" };
  if (value >= 0.5) return { label: "Partial", tone: "mid" };
  return { label: "Weak", tone: "alert" };
}

const STATUS_ICONS: Record<string, string> = {
  match: "✓",
  strong: "✓",
  partial: "~",
  weak: "!",
  neutral: "○",
  conflict: "!",
  review: "?",
};

export default function MatchSummary({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  const bucket = value(row, "recommended_review_bucket");
  const conflicts = reviewCase.evidence_items.filter(
    (item) => item.kind === "conflict" && item.status === "conflict",
  );
  const diagnostics = reviewCase.evidence_items.filter((item) => item.kind === "diagnostic");
  const evidence = reviewCase.evidence_items.filter((item) => item.kind !== "diagnostic");

  const similarity = num(row, "narrative_similarity_ratio");
  const containment = num(row, "text_containment_ratio");
  const strength =
    similarity === null && containment === null ? null : Math.max(similarity ?? 0, containment ?? 0);
  const strengthBand = band(strength);
  const phrase = value(row, "longest_shared_phrase_words") || "0";

  const flaggedReason = diagnostics
    .map((item) => formatFlaggedReason(bucket, String(item.detail ?? "")))
    .filter((text): text is string => Boolean(text))
    .join(" ");

  const multiRow = reviewCase.suggested_db_row_ids.length > 1;
  const genuineMultiAct = isGenuineMultiAct(reviewCase);

  let verdict: { tone: "ok" | "mid" | "alert"; text: string };
  if (conflicts.length > 0) {
    const extra = conflicts.length > 1 ? ` (and ${conflicts.length - 1} more)` : "";
    verdict = { tone: "alert", text: `Worth a closer look — ${conflicts[0].label.toLowerCase()}${extra}.` };
  } else if (multiRow && genuineMultiAct) {
    verdict = {
      tone: "mid",
      text: "Combined act — confirm each database row matches its bracket label.",
    };
  } else if (multiRow) {
    verdict = {
      tone: "mid",
      text: "Several sibling rows share this text — pick the one this entry describes (usually one), not all.",
    };
  } else if (strength !== null && strength >= 0.75) {
    verdict = { tone: "ok", text: "These look like a clear match — the text strongly overlaps and nothing conflicts." };
  } else if (strength !== null && strength >= 0.5) {
    verdict = { tone: "mid", text: "Likely a match — skim the two texts to confirm." };
  } else {
    verdict = { tone: "mid", text: "Weak text overlap — read the segment carefully before deciding." };
  }

  return (
    <section className="match-summary">
      <div className={`verdict verdict-${verdict.tone}`}>
        <span className="verdict-dot" aria-hidden="true" />
        <span className="verdict-text">{verdict.text}</span>
        <span className={`match-band band-${strengthBand.tone}`}>
          Text {strengthBand.label}
          {strength === null ? "" : ` · ${pct(strength)}`}
        </span>
        <span className="match-band band-neutral">Longest phrase · {phrase} words</span>
        <span className={`match-band ${conflicts.length ? "band-alert" : "band-ok"}`}>
          {conflicts.length} conflict{conflicts.length === 1 ? "" : "s"}
        </span>
      </div>

      <details className="match-details">
        <summary>Show signals &amp; why it was flagged</summary>
        <div className="match-details-body">
          {flaggedReason ? (
            <p className="muted why-flagged">
              <strong>Why flagged: </strong>
              {flaggedReason}
            </p>
          ) : null}
          <div className="evidence-table">
            {evidence.map((item, index) => (
              <div className="evidence-row" key={`${item.kind}-${index}`}>
                <span className={`status-chip status-${item.status}`}>{STATUS_ICONS[item.status] ?? "•"}</span>
                <strong>{item.label}</strong>
                <span>{item.detail}</span>
              </div>
            ))}
          </div>
        </div>
      </details>
    </section>
  );
}
