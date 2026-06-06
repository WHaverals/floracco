import type { ActComponent, ReviewCase } from "../types";

/** True when the Word entry really describes more than one act.
 *
 * Two independent signals, never the bare "more than one DB candidate" count
 * (which is exactly the sibling-pile-up false positive): either the parser found
 * ≥2 event components in the Word text (e.g. `[disdetta] + [nuova]`,
 * `[bilancio+modifica]`), or the suggested rows mix a `contract` (a new
 * accomandita) with a `sub_contract` (a later act on it). A single `[Bilancio]`
 * pointing at three sibling balances is NOT multi-act — it is one act with
 * several candidates, and the reviewer should pick one.
 */
export function isGenuineMultiAct(reviewCase: ReviewCase): boolean {
  const components = reviewCase.act_components ?? [];
  if (components.length >= 2) {
    return true;
  }
  // The matcher kept ≥2 `primary` links — a confirmed combined act (e.g.
  // `[Disdetta] di 669 e 798`, one narrative terminating several accomandite).
  // Distinct from a sibling pile-up, which the matcher reduces to one primary.
  if (primaryDbRowIds(reviewCase).length >= 2) {
    return true;
  }
  const ids = reviewCase.suggested_db_row_ids;
  const hasContract = ids.some((id) => id.startsWith("contract:"));
  const hasSubContract = ids.some((id) => id.startsWith("sub_contract:"));
  return hasContract && hasSubContract;
}

/** Only show the Word-act → DB-row map for genuinely compound entries. */
export function shouldShowActComponentMap(reviewCase: ReviewCase): boolean {
  const componentCount = reviewCase.act_components?.length ?? 0;
  return componentCount >= 2 || (isGenuineMultiAct(reviewCase) && componentCount >= 1);
}

/** Headline match strength for one suggested row (max of similarity/containment). */
export function matchStrengthForRow(reviewCase: ReviewCase, dbRowId: string): number | null {
  const metric = reviewCase.link_metrics?.[dbRowId];
  return metric?.match_strength ?? null;
}

/** Rows the matcher tagged `primary` (its chosen twin); alternatives are demoted siblings. */
export function primaryDbRowIds(reviewCase: ReviewCase): string[] {
  const metrics = reviewCase.link_metrics ?? {};
  return reviewCase.suggested_db_row_ids.filter((id) => metrics[id]?.link_role === "primary");
}

/** The single best-matching suggested row. Prefers the matcher's sole `primary`,
 * else falls back to narrative strength (then first). */
export function bestDbRowId(reviewCase: ReviewCase): string | null {
  const ids = reviewCase.suggested_db_row_ids;
  if (ids.length === 0) {
    return null;
  }
  const primaries = primaryDbRowIds(reviewCase);
  if (primaries.length === 1) {
    return primaries[0];
  }
  let bestId = ids[0];
  let bestStrength = matchStrengthForRow(reviewCase, bestId) ?? -1;
  for (const id of ids.slice(1)) {
    const strength = matchStrengthForRow(reviewCase, id) ?? -1;
    if (strength > bestStrength) {
      bestStrength = strength;
      bestId = id;
    }
  }
  return bestId;
}

export function shortDbRowId(dbRowId: string): string {
  const colon = dbRowId.indexOf(":");
  return colon >= 0 ? dbRowId.slice(colon + 1) : dbRowId;
}

export function mappedDbRowIds(reviewCase: ReviewCase): Set<string> {
  const components = reviewCase.act_components ?? [];
  return new Set(
    components.map((component) => component.suggested_db_row_id).filter((value): value is string => Boolean(value)),
  );
}

/** DB row ids for the jump index — all rows when no act map, otherwise only unmapped extras. */
export function jumpIndexDbRowIds(reviewCase: ReviewCase): string[] {
  const seen = new Set<string>();
  const unique = reviewCase.suggested_db_row_ids.filter((id) => (seen.has(id) ? false : (seen.add(id), true)));
  if (!shouldShowActComponentMap(reviewCase)) {
    return unique;
  }
  const mappedIds = mappedDbRowIds(reviewCase);
  return unique.filter((id) => !mappedIds.has(id));
}

export function actBadgeTitle(component: ActComponent): string {
  const parts = [component.label_display];
  if (component.suggested_db_row_id) {
    parts.push(`→ ${component.suggested_db_row_id}`);
  }
  if (component.mapping_confidence === "heuristic") {
    parts.push("(matched by act type)");
  }
  if (component.mapping_confidence === "unmapped") {
    parts.push("(confirm link below)");
  }
  return parts.join(" ");
}
