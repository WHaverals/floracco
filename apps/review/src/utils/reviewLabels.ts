/** Human-readable labels for the Reconcile queue.
 *
 * The pipeline carries internal ids — register slugs ("Camera_di_Commercio_1262"),
 * source-entry ids ("…_entry_00056"), and table-keyed db ids ("sub_contract:18").
 * Those read like variable names to a historian. These helpers turn them into what
 * a reviewer actually recognises — the register written normally, plus the act's
 * date and folio — while the raw id stays available on hover for traceability.
 *
 * Grounded in the 549-case packet: 544 have a date and 545 a folio, so the
 * date-led label populates for ~99%; the 4 db-only cases (no Word entry, hence no
 * date/folio) fall back to the database row; every register de-underscores cleanly
 * except the 4 "Unknown_NNNN" placeholders, which never reach Reconcile.
 */
import type { CasePreview } from "../types";

/** "Camera_di_Commercio_1262" → "Camera di Commercio 1262"; placeholders read sensibly. */
export function prettyRegister(registerId: string | null | undefined): string {
  const id = String(registerId ?? "").trim();
  if (!id) return "Unknown register";
  if (id.startsWith("Unknown_")) return `Unknown register ${id.slice("Unknown_".length)}`;
  return id.replace(/_/g, " ");
}

/** Folio range with a scholarly leaf prefix: "21r-21v" → "cc. 21r–21v"; "21r" → "c. 21r". */
export function prettyFolio(range: string | null | undefined): string {
  const parts = String(range ?? "")
    .split(/\s*-\s*/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return "";
  // A range whose endpoints match ("38r-38r") is really a single leaf.
  if (parts.length === 1 || parts.every((part) => part === parts[0])) return `c. ${parts[0]}`;
  return `cc. ${parts.join("–")}`;
}

/** Table + number from a db id: "sub_contract:18" → "Sub-contract 18". */
function dbRowIdLabel(dbRowId: string): string {
  const [table, num] = dbRowId.split(":");
  const noun = table === "sub_contract" ? "Sub-contract" : "Contract";
  return num ? `${noun} ${num}` : noun;
}

/** Two-line label for a queue case: an act's date on top, register · folio beneath.
 *  Db-only cases (no Word date/folio) fall back to the suggested database row. */
export function caseLabel(item: CasePreview): { primary: string; secondary: string } {
  const date = (item.word_registration_date ?? "").trim();
  const register = prettyRegister(item.register_id);
  const folio = prettyFolio(item.word_folio_range ?? "");
  if (date) {
    return { primary: date, secondary: [register, folio].filter(Boolean).join(" · ") };
  }
  const firstDbId = String(item.suggested_db_row_ids ?? "").split(";")[0].trim();
  if (firstDbId.includes(":")) {
    return { primary: dbRowIdLabel(firstDbId), secondary: register };
  }
  return { primary: register, secondary: "" };
}

/** Typed label for a database record heading, built from the row itself.
 *  Subs: "Sub-contract · variation" (always has a type). Contracts: "Contract · {firm}"
 *  when a firm is recorded, else "Contract" (firm is blank when the firm is just its
 *  partners). The registration date trails as the secondary line. */
export function dbRecordLabel(
  dbRowId: string,
  dbRow: Record<string, unknown>,
): { primary: string; secondary: string } {
  const [table] = dbRowId.split(":");
  const text = (key: string): string => String(dbRow[key] ?? "").trim();
  const date = text("registration_date");
  if (table === "sub_contract") {
    const type = text("sub_type");
    return { primary: type ? `Sub-contract · ${type}` : "Sub-contract", secondary: date };
  }
  const firm = text("firm_name");
  return { primary: firm ? `Contract · ${firm}` : "Contract", secondary: date };
}
