import type { HighlightValue } from "./types";

const MAX_HIGHLIGHTS = 80;
const MIN_TERM_LENGTH = 3;

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Dedupe + length-sort highlight terms so longer phrases win when overlapping. */
export function normalizedTerms(highlights: HighlightValue[]): HighlightValue[] {
  const seen = new Set<string>();
  return highlights
    .filter((item) => item.value.trim().length >= MIN_TERM_LENGTH)
    .sort((left, right) => right.value.length - left.value.length)
    .filter((item) => {
      const key = item.value.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .slice(0, MAX_HIGHLIGHTS);
}

export type HighlightPart = { text: string; match: HighlightValue | null };

/**
 * Split a run of text into matched/unmatched parts against the field-overlap
 * terms. Shared by the plain Word view and the tracked-changes renderer so the
 * background highlight layer composes with change/comment markup on one run.
 */
export function splitWithHighlights(text: string, terms: HighlightValue[]): HighlightPart[] {
  if (!text) {
    return [];
  }
  if (!terms.length) {
    return [{ text, match: null }];
  }
  const pattern = new RegExp(`(${terms.map((item) => escapeRegExp(item.value)).join("|")})`, "gi");
  return text.split(pattern).map((part) => {
    const match = terms.find((item) => item.value.toLowerCase() === part.toLowerCase()) ?? null;
    return { text: part, match };
  });
}
