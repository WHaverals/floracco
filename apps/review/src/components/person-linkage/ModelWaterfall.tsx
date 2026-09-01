import type { WaterfallContribution } from "../../types";

function evidenceLabel(row: WaterfallContribution): string {
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
  }
  if (row.comparison === "contemporaneity") {
    if (label.includes("same firm")) return "Compatible dates and the same firm";
    if (label.includes("3+ shared partners")) return "Compatible dates and 3+ shared partners";
    if (label.includes("shared partner")) return "Compatible dates and a shared partner";
    if (label.includes("shared firm")) return "Compatible dates and related firm names";
    if (label.includes("no shared network")) return "Dates are compatible; no shared network recorded";
    if (label.includes("longer")) return "Combined career is unusually long";
  }
  if (row.comparison === "husband") {
    if (label.includes("same husband")) return "The recorded husband agrees";
    return "Recorded husbands differ";
  }
  return row.label;
}

// One decimal, capped below certainty: the model never proves identity, so a
// rounded "100%" would overstate what the estimate can claim.
function formatProbability(probability: number): string {
  if (probability >= 0.9995) return ">99.9%";
  return `${(probability * 100).toFixed(1)}%`;
}

export default function ModelWaterfall({
  probability,
  rows,
  recall,
  modelHash,
  trainedAt,
  runAt,
  reviewRank,
  reviewPercentile,
  priorityBand,
  networkDiagnostics,
  firmTokenDiagnostics,
}: {
  probability: number | null;
  rows: WaterfallContribution[];
  recall: number | null;
  modelHash: string;
  trainedAt: string;
  runAt: string;
  reviewRank: number | null;
  reviewPercentile: number | null;
  priorityBand: string | null;
  networkDiagnostics: Record<string, unknown>;
  firmTokenDiagnostics: Record<string, unknown>;
}) {
  if (!rows.length) return null;
  const prior = rows.find((row) => row.kind === "prior");
  const comparisons = rows.filter((row) => row.kind !== "prior");
  const finalWeight = rows[rows.length - 1]?.cumulative_weight_bits ?? null;
  // Round the prior and final first and derive the evidence term from the two
  // rounded values: rounding all three independently can make the displayed
  // "prior + evidence = final" miss by 0.1, which reads as a data error.
  const priorBits = prior === undefined ? null : prior.weight_bits.toFixed(1);
  const finalBits = finalWeight === null ? null : finalWeight.toFixed(1);
  const evidenceBits = priorBits !== null && finalBits !== null
    ? (Number(finalBits) - Number(priorBits)).toFixed(1)
    : null;
  const band = priorityBand
    ? `Priority ${priorityBand.split("_")[1]}`
    : "Rule-routed";
  const rankedComparisons = comparisons.filter(
    (row) => row.comparison !== "role" && Math.abs(row.weight_bits) >= 0.05,
  );
  const contextualRows = comparisons.filter(
    (row) => row.comparison === "role" || Math.abs(row.weight_bits) < 0.05,
  );
  const supports = rankedComparisons.filter((row) => row.weight_bits > 0).map(evidenceLabel);
  const against = rankedComparisons.filter((row) => row.weight_bits < 0).map(evidenceLabel);
  const summary = [
    supports.length ? `Supports same person: ${supports.join("; ")}.` : "",
    against.length ? `Supports different people: ${against.join("; ")}.` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className="pl-model-details" aria-labelledby="pl-model-heading">
      <header className="pl-model-head">
        <div>
          <p className="eyebrow">Review ordering</p>
          <h2 id="pl-model-heading">Evidence contributions</h2>
        </div>
        <div className="pl-model-badges">
          <span className="pl-model-band">{band}</span>
          {reviewPercentile !== null && reviewPercentile <= 0.25 ? (
            <span className="pl-model-rank">
              Top {Math.max(0.1, reviewPercentile * 100).toFixed(1)}%
            </span>
          ) : null}
        </div>
      </header>
      <p className="pl-model-note">
        Bar direction shows how each recorded feature affects review order. It is not a verdict.
        {reviewRank !== null ? ` Queue rank #${reviewRank.toLocaleString()}.` : ""}
      </p>
      <div className="pl-model-body">
        <div className="pl-waterfall" role="img" aria-label={summary}>
          <div className="pl-water-head" aria-hidden="true">
            <span>Supports different people</span>
            <span>Supports same person</span>
          </div>
          {rankedComparisons.map((row, index) => {
            const tooltipId = `pl-evidence-tip-${row.comparison}-${index}`;
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
                  <strong>{row.label}</strong>
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
        {contextualRows.length ? (
          <details className="pl-no-evidence">
            <summary>
              {contextualRows.length} contextual or unrecorded field{contextualRows.length === 1 ? "" : "s"}
            </summary>
            <ul>
              {contextualRows.map((row, index) => (
                <li key={`${row.comparison}-${index}`}>
                  <strong>{row.comparison_label ?? row.comparison}:</strong> {
                    row.comparison === "role" ? `${row.label} (shown for context; not used in review order)` : row.label
                  }
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        <details className="pl-model-technical">
          <summary>Probability and technical details</summary>
          <div className="pl-water-totals">
            <span>
              <small>Starting prior</small>
              <strong>{priorBits !== null ? `${priorBits} bits` : "—"}</strong>
            </span>
            <span aria-hidden="true">+</span>
            <span>
              <small>Recorded evidence</small>
              <strong>{evidenceBits !== null ? `${evidenceBits} bits` : "—"}</strong>
            </span>
            <span aria-hidden="true">=</span>
            <span>
              <small>Final estimate</small>
              <strong>
                {finalBits === null ? "—" : `${finalBits} bits`}
                {probability === null ? "" : ` · ${formatProbability(probability)}`}
              </strong>
            </span>
          </div>
          <p className="muted">
            The raw saved-model estimate below includes partnership role and is not calibrated against a
            substantial reviewed sample.
            {recall !== null ? ` The model prior assumes rule recall of ${recall}.` : ""}
          </p>
          <p className="pl-model-provenance">
            Model {modelHash ? modelHash.slice(0, 12) : "—"} · trained {trainedAt ? trainedAt.slice(0, 10) : "—"} ·
            suggestion cache {runAt ? runAt.slice(0, 10) : "—"}
          </p>
          {Object.keys(networkDiagnostics).length || Object.keys(firmTokenDiagnostics).length ? (
            <section className="pl-experimental-diagnostics">
              <h3>Experimental diagnostics · not scored</h3>
              {Object.keys(networkDiagnostics).length ? (
                <p>
                  Unfiltered network: {Number(networkDiagnostics.common_neighbor_count ?? 0)} shared neighbors ·
                  weighted Jaccard {Number(networkDiagnostics.weighted_jaccard ?? 0).toFixed(3)} ·
                  Adamic–Adar {Number(networkDiagnostics.adamic_adar ?? 0).toFixed(3)} · ego sizes{" "}
                  {Number(networkDiagnostics.left_ego_size ?? 0)} / {Number(networkDiagnostics.right_ego_size ?? 0)}.
                </p>
              ) : null}
              {Object.keys(firmTokenDiagnostics).length ? (
                <p>
                  Firm-token IDF: weighted Jaccard{" "}
                  {Number(firmTokenDiagnostics.weighted_jaccard ?? 0).toFixed(3)} · cosine{" "}
                  {Number(firmTokenDiagnostics.cosine ?? 0).toFixed(3)}.
                </p>
              ) : null}
            </section>
          ) : null}
        </details>
      </div>
    </section>
  );
}
