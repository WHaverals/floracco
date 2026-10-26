import type { WaterfallContribution } from "../../types";

/* The evidence chart as the case page draws it: one bar per comparison from
   a centre line, pointing left for evidence against and right for evidence in
   favour. The prior is not drawn and no numbers are printed; the partnership
   role and the comparisons with nothing to compare are folded into a short
   list beneath. The case page (ModelWaterfall) and the primer both render
   this component, so the primer shows exactly what a reviewer will meet. */

export function evidenceLabel(row: WaterfallContribution): string {
  const label = row.label.toLowerCase();
  if (row.comparison === "name") {
    if (label.includes("exact match")) return "Recorded name is exact";
    if (label.includes("0.92")) return "Recorded names are very close";
    if (label.includes("0.85")) return "Recorded names are moderately close";
    return "Recorded names differ";
  }
  if (row.comparison === "lineage") {
    if (label.includes("father and grandfather agree")) return "Father and grandfather agree";
    if (label.includes("grandfather unrecorded")) return "Father agrees; one grandfather is unrecorded";
    if (label.includes("grandfather differs")) return "Father agrees; grandfathers differ";
    if (label.includes("one letter apart")) return "Father’s name differs by one letter";
    if (label.includes("sibling")) return "Same father but different given names";
    if (label.includes("father-and-son")) return "The names form a father–son pattern";
    if (label === "all other comparisons") return "Fathers’ names differ";
  }
  if (row.comparison === "contemporaneity") {
    if (label.includes("same firm")) return "Compatible dates and the same firm";
    if (label.includes("3+ shared partners")) return "Compatible dates and 3+ shared partners";
    if (label.includes("shared partner")) return "Compatible dates and a shared partner";
    if (label.includes("shared firm")) return "Compatible dates and related firm names";
    if (label.includes("no shared network")) return "Dates are compatible; no shared network recorded";
    if (label.includes("shared network")) return "Compatible dates and a shared business network";
    if (label === "all other comparisons") return "Combined career is longer than sixty years";
  }
  if (row.comparison === "husband") {
    if (label.includes("same husband")) return "The recorded husband agrees";
    if (label.includes("exact match")) return "The husbands’ family names agree";
    return "Recorded husbands differ";
  }
  return row.label;
}

// The folded list beneath the bars: the model's own level names, put into
// words where they are database shorthand.
function contextLabel(row: WaterfallContribution): string {
  const label = row.label.toLowerCase();
  if (label.includes("is null")) return "nothing to compare";
  if (label.includes("no evidence, or the pair shares an act")) {
    return "no dated contracts on one side, or both entries in the same contract";
  }
  if (row.comparison === "role") {
    const text = label === "all other comparisons" ? "one general partner, one limited partner" : row.label;
    return `${text} (shown for context; not used in review order)`;
  }
  return row.label;
}

/** The tooltip's level name: the model's own wording, with column names put into words. */
function levelText(row: WaterfallContribution): string {
  const jw = row.label.match(/^Jaro-Winkler distance of full_name_norm >= ([\d.]+)$/);
  if (jw) return `Jaro–Winkler similarity of the full names: ${jw[1]} or more`;
  if (row.label === "Exact match on full_name_norm") return "The full names are identical";
  if (row.label === "Exact match on husband_last_norm") return "The husbands’ family names are identical";
  if (row.label === "All other comparisons") return evidenceLabel(row);
  return row.label.replace(/career <= (\d+) years/, "combined career of $1 years or less");
}

export default function EvidenceBars({
  rows,
  idPrefix = "pl-evidence",
}: {
  rows: WaterfallContribution[];
  idPrefix?: string;
}) {
  const comparisons = rows.filter((row) => row.kind !== "prior");
  const ranked = comparisons.filter(
    (row) => row.comparison !== "role" && Math.abs(row.weight_bits) >= 0.05,
  );
  const contextual = comparisons.filter(
    (row) => row.comparison === "role" || Math.abs(row.weight_bits) < 0.05,
  );
  const supports = ranked.filter((row) => row.weight_bits > 0).map(evidenceLabel);
  const against = ranked.filter((row) => row.weight_bits < 0).map(evidenceLabel);
  const summary = [
    supports.length ? `Supports same person: ${supports.join("; ")}.` : "",
    against.length ? `Supports different people: ${against.join("; ")}.` : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <div className="pl-waterfall" role="img" aria-label={summary}>
        <div className="pl-water-head" aria-hidden="true">
          <span>Supports different people</span>
          <span>Supports same person</span>
        </div>
        {ranked.map((row, index) => {
          const tooltipId = `${idPrefix}-tip-${row.comparison}-${index}`;
          const width = `${Math.min(48, Math.max(2, (Math.abs(row.weight_bits) / 12) * 48))}%`;
          return (
            <div
              className="pl-water-row"
              key={`${row.comparison}-${index}`}
              tabIndex={0}
              aria-describedby={tooltipId}
            >
              <div className="pl-water-label">
                <strong>{row.comparison_label ?? row.comparison}</strong>
                <span>{evidenceLabel(row)}</span>
              </div>
              <div className="pl-water-track" aria-hidden="true">
                <span className="pl-water-zero" />
                <span
                  className={`pl-water-bar is-${row.direction}`}
                  style={
                    row.weight_bits >= 0
                      ? { left: "50%", width }
                      : { right: "50%", width }
                  }
                />
              </div>
              <span className="pl-water-info" aria-hidden="true">i</span>
              <span className="pl-water-tooltip" id={tooltipId} role="tooltip">
                <strong>{levelText(row)}</strong>
                <span>
                  {row.weight_bits > 0 ? "Supports same person" : "Supports different people"}
                  {" · "}{row.weight_bits > 0 ? "+" : ""}{row.weight_bits.toFixed(2)} model bits
                </span>
              </span>
            </div>
          );
        })}
      </div>
      <p className="pl-sr-only">{summary}</p>
      {contextual.length ? (
        <details className="pl-no-evidence">
          <summary>
            {contextual.length} contextual or unrecorded field{contextual.length === 1 ? "" : "s"}
          </summary>
          <ul>
            {contextual.map((row, index) => (
              <li key={`${row.comparison}-${index}`}>
                <strong>{row.comparison_label ?? row.comparison}:</strong> {contextLabel(row)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}
