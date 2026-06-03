import type { ReviewCase } from "../types";
import HighlightedText from "./HighlightedText";

function value(row: ReviewCase["row"], key: string): string {
  return String(row[key] ?? "");
}

export default function WordPanel({ reviewCase }: { reviewCase: ReviewCase }) {
  const row = reviewCase.row;
  const text = value(row, "word_entry_text") || "No Word text is attached to this row.";
  return (
    <section className="panel segment-panel">
      <div className="segment-head">
        <p className="eyebrow">Word segment</p>
        <dl className="fact-row">
          <div>
            <dt>Date</dt>
            <dd>{value(row, "word_registration_date") || "—"}</dd>
          </div>
          <div>
            <dt>Folio</dt>
            <dd>{value(row, "word_folio_range") || "—"}</dd>
          </div>
          <div>
            <dt>Label</dt>
            <dd>{value(row, "entry_label") || "—"}</dd>
          </div>
        </dl>
      </div>
      <article className="reading-text narrative">
        <HighlightedText highlights={reviewCase.highlight_values} text={text} />
      </article>
    </section>
  );
}
