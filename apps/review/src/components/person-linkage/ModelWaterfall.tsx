import type { WaterfallContribution } from "../../types";
import EvidenceBars from "./EvidenceBars";

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
        <EvidenceBars rows={rows} />
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
