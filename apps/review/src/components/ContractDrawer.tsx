import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { loadDbRecord } from "../api";
import type { DbField, DbRecord } from "../types";
import { useEscapeLayer } from "../utils/escapeLayers";
import { useLatest } from "../utils/latest";
import { prettyFolio } from "../utils/reviewLabels";
import { roleLabel } from "../utils/roles";
import WordSummaryInline from "./WordSummaryInline";

/* Find's contract drawer (review 2026-10-27, 12.1): a contract opens at the
 * right of the page with what a reader needs to judge the row, and its Word
 * summary as frozen evidence, so the list underneath keeps its scroll, sort
 * and opened Count cell. Read-only: editing stays on the Database page, one
 * click away. The record is the Database page's own (/api/db/record), so the
 * two never disagree; hidden partners and places are left out there. Previous
 * and next step through the contracts in the order the list shows them. */

const LINK_ATTR = "data-contract-drawer-link";
const ROW_ATTR = "data-drawer-row";

/** A contract number in a Find list. A plain click opens the drawer; a
 * cmd/ctrl-click, shift-click or middle click opens the record page as any
 * link does. */
export function ContractLink({ id, onOpen }: { id: number; onOpen: (id: number) => void }) {
  return (
    <Link
      to={`/database/contract/${id}`}
      {...{ [LINK_ATTR]: "" }}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onOpen(id);
      }}
    >
      {id}
    </Link>
  );
}

/** Props for a list row, so the drawer can find it (to keep it in view) and mark it. */
export function drawerRow(id: number, open: number | null) {
  return { [ROW_ATTR]: id, className: open === id ? "is-in-drawer" : undefined };
}

/** The contracts of a list in the order shown, each once. */
export function drawerOrder(ids: (string | number | null)[]): number[] {
  return [...new Set(ids.map(Number).filter((id) => Number.isFinite(id)))];
}

const EMPTY = new Set(["", "—", "not recorded"]);
/** A stored value as the drawer writes it: blanks read "not recorded". */
function shown(value: string | null | undefined): string {
  const text = String(value ?? "").trim();
  return EMPTY.has(text) ? "not recorded" : text;
}

function byColumn(record: DbRecord, column: string): DbField | undefined {
  return record.fields.find((f) => f.column === column || f.relink?.field === column);
}

function Facts({ record }: { record: DbRecord }) {
  const places = (record.places?.rows ?? []).filter((p) => !p.removed);
  const total = byColumn(record, "total");
  const folio = (byColumn(record, "folio")?.value ?? "").trim();
  const activity = shown(byColumn(record, "economic_sector")?.value);
  const rows: [string, string][] = [
    ["Registration date", shown(byColumn(record, "registration_date")?.value)],
    [
      "Places of activity",
      places.length
        ? places.map((p) => `${p.place}${p.inferred ? " (marked as inferred)" : ""}`).join("; ")
        : "not recorded",
    ],
    ["Activity", activity === "not recorded" || !record.activity_inferred ? activity : `${activity} (marked as inferred)`],
    ["Currency", shown(byColumn(record, "currency_id")?.value)],
    ["Total capital", shown(total?.current ?? total?.value)],
    ["Register", shown(record.register)],
    ["Folio", folio && folio !== "—" ? prettyFolio(folio) : "not recorded"],
  ];
  return (
    <dl className="contract-drawer-facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className={value === "not recorded" ? "muted" : undefined}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Partners({ record }: { record: DbRecord }) {
  const rows = (record.partners?.rows ?? []).filter((r) => !r.removed);
  return (
    <section>
      <h4>Partners ({record.partners?.count ?? rows.length})</h4>
      {rows.length === 0 ? (
        <p className="muted">No partners are recorded.</p>
      ) : (
        <table className="contract-drawer-partners">
          <thead>
            <tr>
              <th>Partner</th>
              <th>Role on the stake</th>
              <th>Jewish, stated in the act</th>
              <th>Jewish, editorial attribution</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.person ? row.person.name : <span className="muted">No partner attached</span>}</td>
                <td title={row.role?.value || undefined}>
                  {row.role?.value ? roleLabel(row.role.value) : <span className="muted">not recorded</span>}
                </td>
                <td>{row.jewish?.stated ? "yes" : ""}</td>
                <td>{row.jewish?.attributed ? "yes" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Evidence({ record }: { record: DbRecord }) {
  const sources = record.word_sources;
  // the first summary not rejected by a reviewer is open; the others one click away
  const first = sources.findIndex((s) => s.status !== "rejected");
  return (
    <section>
      <h4>Word summar{sources.length > 1 ? "ies" : "y"}</h4>
      {sources.length === 0 ? (
        <p className="muted">No Word summary is linked to this contract.</p>
      ) : (
        <div className="ws-inline-list contract-drawer-word">
          {sources.map((source, i) => (
            <WordSummaryInline
              key={`${record.id}-${source.via_row_id ?? ""}-${source.source_entry_id}`}
              source={source}
              defaultOpen={i === first}
            />
          ))}
        </div>
      )}
      {record.document && (
        <details className="contract-drawer-narrative">
          <summary>The database’s own narrative</summary>
          <p className="reading-text narrative db-narrative">{record.document}</p>
        </details>
      )}
    </section>
  );
}

export default function ContractDrawer({
  id,
  order,
  onMove,
  onClose,
}: {
  id: number;
  /** The contracts of the list underneath, in the order it shows them. */
  order: number[];
  onMove: (id: number) => void;
  onClose: () => void;
}) {
  const cache = useRef(new Map<number, DbRecord>());
  const [record, setRecord] = useState<DbRecord | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const begin = useLatest();
  const panel = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const first = useRef(true);

  useEffect(() => {
    const fresh = begin();
    setError("");
    const cached = cache.current.get(id);
    if (cached) {
      setRecord(cached);
      return;
    }
    loadDbRecord("contract", String(id))
      .then((r) => {
        cache.current.set(id, r);
        if (fresh()) setRecord(r);
      })
      .catch((e: Error) => fresh() && setError(e.message));
  }, [id, attempt, begin]);

  // Stepping keeps the list's row in view and starts the drawer at its top;
  // the first opening leaves the list exactly where it was.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    body.current?.scrollTo({ top: 0 });
    document.querySelector(`[${ROW_ATTR}="${id}"]`)?.scrollIntoView({ block: "nearest" });
  }, [id]);

  useEscapeLayer(true, onClose);

  // A click outside closes the drawer; a click on another contract number in
  // the list opens that one instead (ContractLink).
  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || target === document.documentElement) return;
      if (panel.current?.contains(target) || target.closest(`[${LINK_ATTR}]`)) return;
      closeRef.current();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, []);

  const at = order.indexOf(id);
  const prev = at > 0 ? order[at - 1] : null;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : null;
  const current = record && record.id === String(id) ? record : null;

  // On the page's body, so a list fading while it updates never fades the drawer.
  return createPortal(
    <aside className="contract-drawer" ref={panel} role="dialog" aria-label={`Contract ${id}`}>
      <header className="contract-drawer-head">
        <div className="contract-drawer-titles">
          <p className="eyebrow">
            Contract {id}
            {at >= 0 && (
              <span className="contract-drawer-position">
                {" "}
                · {(at + 1).toLocaleString()} of {order.length === 1 ? "1 contract" : `the ${order.length.toLocaleString()} contracts`} in
                this list
              </span>
            )}
          </p>
          <h3>
            {/* the record's title is the firm name, or "Contract N" when none is recorded */}
            {!current ? "…" : current.title === `Contract ${id}` ? <span className="muted">No firm name recorded</span> : current.title}
            {current?.is_deleted && <span className="db-hidden-badge">Hidden</span>}
          </h3>
        </div>
        <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>
      <nav className="contract-drawer-nav" aria-label="Step through the list">
        <button type="button" className="pill-button" disabled={prev === null} onClick={() => prev !== null && onMove(prev)}>
          ‹ Previous
        </button>
        <button type="button" className="pill-button" disabled={next === null} onClick={() => next !== null && onMove(next)}>
          Next ›
        </button>
        <Link
          className="contract-drawer-open"
          to={`/database/contract/${id}`}
          target="_blank"
          rel="noopener"
          title="The full record, where it can be edited, in a new tab"
        >
          Open in the Database
        </Link>
      </nav>

      <div className={current || !record ? "contract-drawer-body" : "contract-drawer-body is-updating"} ref={body}>
        {error ? (
          <p className="error-text">
            {error}{" "}
            <button type="button" className="link-like" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </p>
        ) : !record ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            {record.is_deleted && (
              <p className="notice warning">This contract is hidden. Find, browsing and search leave it out.</p>
            )}
            <Facts record={record} />
            <Partners record={record} />
            <Evidence record={record} />
          </>
        )}
      </div>
    </aside>,
    document.body,
  );
}
