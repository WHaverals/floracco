import type { WaterfallContribution } from "../../types";

// Andrea di Neri Corsini, entries 5305 and 11633: the evidence rows exactly as
// the suggestion cache holds them (run pl-cdb536ef60e2cef33722, read from
// data/sqlite/person_cache.db read-only on 2026-09-02). Only the fields the
// chart uses are kept. The primer draws them with the case page's own chart.
export const CORSINI_EVIDENCE: WaterfallContribution[] = [
  { kind: "prior", comparison: "prior", label: "Chance before comparing these records", weight_bits: -19.154230955472354, cumulative_weight_bits: -19.154230955472354, direction: "against" },
  { kind: "comparison", comparison: "name", comparison_label: "Recorded name", label: "Exact match on full_name_norm", weight_bits: 10.001606553274357, cumulative_weight_bits: -9.152624402197997, direction: "supports" },
  { kind: "comparison", comparison: "lineage", comparison_label: "Father and grandfather", label: "father agrees, grandfather unrecorded", weight_bits: 7.209050638831334, cumulative_weight_bits: -1.943573763366663, direction: "supports" },
  { kind: "comparison", comparison: "contemporaneity", comparison_label: "Career and business context", label: "career <= 30 years, the same firm", weight_bits: 9.770913340751381, cumulative_weight_bits: 7.827339577384718, direction: "supports" },
  { kind: "comparison", comparison: "role", comparison_label: "Partnership role", label: "same role throughout", weight_bits: 0.652500595221736, cumulative_weight_bits: 8.479840172606455, direction: "supports" },
  { kind: "comparison", comparison: "husband", comparison_label: "Husband's name", label: "husband_last_norm is NULL", weight_bits: 0, cumulative_weight_bits: 8.479840172606455, direction: "none" },
];
