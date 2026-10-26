/** The archive export wrote 0000-00-00 where no date was recorded. Reading
 * surfaces treat it as absent, so each one's own empty wording ("Undated",
 * "no date", "—") applies; stored values and the date editor keep it. */
export function recordedDate(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  return text === "0000-00-00" ? "" : text;
}
