import type { ActComponent, ReviewCase } from "../types";

export function shouldShowActComponentMap(reviewCase: ReviewCase): boolean {
  const linkCount = reviewCase.suggested_db_row_ids.length;
  const componentCount = reviewCase.act_components?.length ?? 0;
  return componentCount >= 2 || (linkCount > 1 && componentCount >= 1);
}

export function shortDbRowId(dbRowId: string): string {
  const colon = dbRowId.indexOf(":");
  return colon >= 0 ? dbRowId.slice(colon + 1) : dbRowId;
}

export function unmappedDbRowIds(reviewCase: ReviewCase): string[] {
  const components = reviewCase.act_components ?? [];
  const mappedIds = new Set(
    components.map((component) => component.suggested_db_row_id).filter((value): value is string => Boolean(value)),
  );
  return reviewCase.suggested_db_row_ids.filter((id) => !mappedIds.has(id));
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
