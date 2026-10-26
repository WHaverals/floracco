/* gp/lp are the stored codes (investment.type); the reader sees the words.
 * Pages keep the code on hover where it helps, so the old shorthand is never
 * lost. Labels (cells, options) use the capitalised word; running prose uses
 * roleLabel(code).toLowerCase(). */
export const ROLE_LABELS: Record<string, string> = {
  gp: "General partner",
  lp: "Limited partner",
};

export function roleLabel(code: string | null | undefined): string {
  const raw = String(code ?? "").trim();
  return ROLE_LABELS[raw.toLowerCase()] ?? raw;
}

/** A list as the server sends it ("gp,lp") → "General partner, Limited partner". */
export function roleListLabel(codes: string | null | undefined): string {
  return String(codes ?? "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
    .map(roleLabel)
    .join(", ");
}
