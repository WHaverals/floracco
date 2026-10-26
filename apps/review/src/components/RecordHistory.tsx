import { useState } from "react";
import { Link } from "react-router-dom";
import type { DbBrowseTable, HistoryEntry } from "../types";

/* A record's change history (UI review 2026-10-14; LOG 2026-10-27): every logged
 * change that concerns the page — the record itself, and for a contract its
 * partners, stakes, places and sub-contracts — one line per action, worded by the
 * server. Times are shown in the reader's own time zone. */

const COVERS: Record<DbBrowseTable, string> = {
  contract: "This contract, its partners and their person entries, stakes, places and sub-contracts",
  sub_contract: "This sub-contract",
  person: "This person's entry and their appearances on contracts",
};
const FOLD_AT = 10;

const dayFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const fullFormat = new Intl.DateTimeFormat("en-GB", { dateStyle: "long", timeStyle: "short" });

function parsed(at: string | null | undefined): Date | null {
  if (!at) return null;
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "15 September 2026", in the reader's time zone. */
export function historyDay(at: string | null | undefined): string {
  const d = parsed(at);
  return d ? dayFormat.format(d) : "";
}

function dayHeading(d: Date | null): string {
  if (!d) return "Date not recorded";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return dayFormat.format(d);
}

const LINK_PATH: Record<NonNullable<HistoryEntry["link"]>["kind"], string> = {
  person: "/database/person/",
  contract: "/database/contract/",
  sub_contract: "/database/sub_contract/",
  place: "/reference/place/",
};

const shown = (value: string | null) => value ?? "(empty)";
const capitalised = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** "Father / mother: (empty) → Alessandro"; three values or fewer in full, else first → last. */
function ValueChange({ entry }: { entry: HistoryEntry }) {
  const values = entry.chain ?? [entry.before, entry.after];
  const steps = values.length <= 3 ? values : [values[0], values[values.length - 1]];
  return (
    <>
      {entry.field}:{" "}
      {steps.map((value, index) => (
        <span key={index} className={index < steps.length - 1 ? "db-history-old" : undefined}>
          {index > 0 ? " → " : ""}
          {shown(value)}
        </span>
      ))}
      {entry.edits > 1 && (
        <span className="db-history-old" title={values.map(shown).join(" → ")}>
          {" "}
          · {entry.edits} edits
        </span>
      )}
    </>
  );
}

function HistoryLine({ entry }: { entry: HistoryEntry }) {
  const when = parsed(entry.at);
  const text = entry.text ? (entry.subject ? entry.text : capitalised(entry.text)) : "";
  return (
    <li className={`db-history-item kind-${entry.kind}`}>
      <span className="db-history-tag">{entry.tag}</span>
      <span className="db-history-what">
        {entry.subject &&
          (entry.link ? (
            <Link className="db-history-subject" to={`${LINK_PATH[entry.link.kind]}${entry.link.id}`}>
              {entry.subject}
            </Link>
          ) : (
            <span className="db-history-subject">{entry.subject}</span>
          ))}
        {entry.subject && (text || entry.field) ? " · " : ""}
        {text}
        {text && entry.field ? "; " : ""}
        {entry.field && <ValueChange entry={entry} />}
        {entry.detail && <span className="db-history-old"> · {entry.detail}</span>}
        {entry.undone && <span className="db-history-chip">Undone</span>}
        {entry.status !== "applied" && (
          <span className="db-history-chip is-warning">
            {entry.status === "mixed" ? "Partly not in effect" : "Not in effect"}
          </span>
        )}
      </span>
      <span className="db-history-meta" title={when ? fullFormat.format(when) : undefined}>
        {entry.by}
        {when ? ` · ${timeFormat.format(when)}` : ""}
      </span>
      {entry.source && <span className="db-history-reason">Source: {entry.source}</span>}
      {entry.reason && <span className="db-history-reason">“{entry.reason}”</span>}
      {entry.undone && (
        <span className="db-history-note">
          Undone by {entry.undone.by ?? "a reviewer"}
          {entry.undone.at ? `, ${historyDay(entry.undone.at)}` : ""}.
        </span>
      )}
      {entry.status !== "applied" && entry.status_note && <span className="db-history-note">{entry.status_note}</span>}
    </li>
  );
}

export default function RecordHistory({ entries, table }: { entries: HistoryEntry[]; table: DbBrowseTable }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? entries : entries.slice(0, FOLD_AT);
  const days: { label: string; entries: HistoryEntry[] }[] = [];
  for (const entry of visible) {
    const label = dayHeading(parsed(entry.at));
    if (days.length === 0 || days[days.length - 1].label !== label) days.push({ label, entries: [] });
    days[days.length - 1].entries.push(entry);
  }
  return (
    <section className="db-block db-history" id="rec-history">
      <div className="db-block-head">
        <h3>Change history ({entries.length})</h3>
        <span className="db-block-sub muted">{COVERS[table]} · newest first</span>
      </div>
      {days.map((day, index) => (
        <div key={`${day.label}-${index}`}>
          <p className="db-history-day">{day.label}</p>
          <ul className="db-history-list">
            {day.entries.map((entry) => (
              <HistoryLine key={entry.id} entry={entry} />
            ))}
          </ul>
        </div>
      ))}
      {entries.length > FOLD_AT && (
        <button type="button" className="link-like db-history-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${entries.length} changes`}
        </button>
      )}
    </section>
  );
}
