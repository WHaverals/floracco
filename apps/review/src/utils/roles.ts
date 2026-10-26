/* gp/lp are the stored codes (investment.type); the reader sees the words.
 * Pages keep the code on hover where it helps, so the old shorthand is never
 * lost. Labels (cells, options) use the capitalised word; running prose uses
 * roleLabel(code).toLowerCase(). The accomandante is an "external investor
 * (accomandante)", the term of the home-page essay (WH, 2026-10-26). */
export const ROLE_LABELS: Record<string, string> = {
  gp: "General partner",
  lp: "External investor (accomandante)",
};

export function roleLabel(code: string | null | undefined): string {
  const raw = String(code ?? "").trim();
  return ROLE_LABELS[raw.toLowerCase()] ?? raw;
}

/** The role in running prose with its article: "a general partner", "an external investor (accomandante)". */
export function aRole(code: string | null | undefined): string {
  const word = roleLabel(code).toLowerCase();
  return `${/^[aeiou]/.test(word) ? "an" : "a"} ${word}`;
}

/** A list as the server sends it ("gp,lp") → "General partner, External investor (accomandante)". */
export function roleListLabel(codes: string | null | undefined): string {
  return String(codes ?? "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
    .map(roleLabel)
    .join(", ");
}
