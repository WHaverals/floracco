import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { loadWordEntry } from "../../api";
import type {
  DbWordSource,
  PersonAppearance,
  PersonCaseContract,
  PersonLinkagePerson,
  PersonSeamSide,
  PersonSeamView,
  WordEntryDetail,
} from "../../types";
import { recordedDate } from "../../utils/dates";
import { roleListLabel } from "../../utils/roles";
import WordSummaryInline from "../WordSummaryInline";
import SeamRibbon, { SEAM_LANES, appearanceYear, type SeamMark } from "./SeamRibbon";

const SIDE_WORD: Record<SeamMark, string> = {
  this: "This entry",
  unsure: "Unsure",
  other: "Someone else",
  unmarked: "Not marked",
};

/** The full recorded name: given name, di father, di grandfather, family name. */
export function recordedName(person: PersonLinkagePerson): string {
  const f = person.fields;
  const chain = [f.first_name, f.father_mother ? `di ${f.father_mother}` : "", f.grandfather ? `di ${f.grandfather}` : "", f.last_name]
    .map((part) => (part == null ? "" : String(part).trim()))
    .filter(Boolean)
    .join(" ");
  return chain || person.display_name;
}

function dateKey(appearance: PersonAppearance): string {
  const raw = String(appearance.registration_date ?? "");
  return raw && raw !== "0000-00-00" ? raw : "9999";
}

/** Each distinct value over the dated appearances with its first and last
 *  year, in order of first appearance ("none" when the field is empty). Per
 *  value rather than per act, so an entry doubled on one act does not read
 *  as a title that flips every year. */
function runs(appearances: PersonAppearance[], pick: (a: PersonAppearance) => string | null | undefined) {
  const spans = new Map<string, { value: string; first: number; last: number }>();
  appearances.forEach((appearance) => {
    const year = appearanceYear(appearance);
    if (year === null) return;
    const value = String(pick(appearance) ?? "").trim() || "none";
    const span = spans.get(value);
    if (span) {
      span.first = Math.min(span.first, year);
      span.last = Math.max(span.last, year);
    } else {
      spans.set(value, { value, first: year, last: year });
    }
  });
  return [...spans.values()].sort((a, b) => a.first - b.first || a.last - b.last);
}

function runText(items: Array<{ value: string; first: number; last: number }>): string {
  return items
    .map((run, index) => {
      const span = run.first === run.last ? String(run.first) : `${run.first}–${run.last}`;
      if (run.value === "none") return index === 0 && items.length > 1 ? `none until ${run.last}` : `none ${span}`;
      return `${run.value} ${span}`;
    })
    .join(" · ");
}

/** The largest silence between consecutive dated living appearances. */
function largestGap(appearances: PersonAppearance[]) {
  const living = appearances
    .filter((a) => !a.heirs_of && appearanceYear(a) !== null)
    .sort((a, b) => dateKey(a).localeCompare(dateKey(b)));
  let best: { years: number; before: PersonAppearance; after: PersonAppearance } | null = null;
  for (let i = 1; i < living.length; i += 1) {
    const years = (appearanceYear(living[i]) as number) - (appearanceYear(living[i - 1]) as number);
    if (!best || years > best.years) best = { years, before: living[i - 1], after: living[i] };
  }
  return best && best.years >= 10 ? best : null;
}

/** Every phrase around the given name in a summary's text: found text, no reading. */
function namePhrases(text: string, givenName: string): string[] {
  if (!text || !givenName) return [];
  const needle = givenName.trim().toLowerCase();
  if (!needle) return [];
  const lower = text.toLowerCase();
  const found: string[] = [];
  let from = 0;
  while (found.length < 6) {
    const at = lower.indexOf(needle, from);
    if (at < 0) break;
    const tail = text.slice(at, at + 90);
    const cut = tail.search(/[,;:.“”"()\n]|\s(e|ed|et|che|come|per)\s/);
    const phrase = (cut > 0 ? tail.slice(0, cut) : tail.slice(0, 60)).replace(/\s+/g, " ").trim();
    if (phrase && !found.includes(phrase)) found.push(phrase);
    from = at + needle.length;
  }
  return found;
}

function noteWords(appearance: PersonAppearance): string[] {
  const words: string[] = [];
  if (appearance.profession) words.push(String(appearance.profession));
  if (appearance.via_proxy) words.push("through a proxy");
  if (appearance.heirs_of) words.push("through heirs");
  if (appearance.stake_names) words.push(`stake under ${appearance.stake_names}`);
  return words;
}

/* The one-entry case while its acts are being marked (editable) or once marks
 * stand (read-only): the entry line, the three-lane chart, the observed strip,
 * the selection aids and the table, with the Word summary of each act under
 * its row. Everything here is data or the reader's own marks; nothing is a
 * judgment of the platform. Scope: docs/person_linkage/seam_scope_v2 §3–4. */
export default function SeamMarking({
  person,
  entrySources,
  seam,
  marks,
  editable,
  onMark,
  otherEntry,
  onOtherEntry,
  onOpenSource,
  onUseEvidence,
  citedIds,
}: {
  person: PersonLinkagePerson;
  entrySources: PersonCaseContract[];
  seam: PersonSeamView | null;
  marks: Record<number, SeamMark>;
  editable: boolean;
  onMark?: (investorIds: number[], side: PersonSeamSide) => void;
  otherEntry?: string;
  onOtherEntry?: (value: string) => void;
  onOpenSource: (sourceEntryId: string) => void;
  onUseEvidence?: (entry: WordEntryDetail) => void;
  citedIds?: Set<string>;
}) {
  const appearances = useMemo(
    () => [...person.appearances].sort((a, b) => dateKey(a).localeCompare(dateKey(b)) || a.contract_id - b.contract_id || a.investor_id - b.investor_id),
    [person.appearances],
  );
  const sourcesByAct = useMemo(() => {
    const map = new Map<number, DbWordSource[]>();
    entrySources.forEach((act) => map.set(act.contract_id, act.word_sources));
    return map;
  }, [entrySources]);
  const primarySource = (contractId: number): DbWordSource | null => {
    const sources = sourcesByAct.get(contractId) ?? [];
    return sources.find((s) => s.status === "confirmed") ?? sources.find((s) => s.status === "proposed") ?? sources[0] ?? null;
  };

  // The summaries' texts, for the "named in the summary as" column: loaded
  // once, in parallel, only for acts with a linked summary.
  const [texts, setTexts] = useState<Record<string, string | null>>({});
  const [summaryOpen, setSummaryOpen] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [titleFilter, setTitleFilter] = useState("");
  useEffect(() => {
    const ids = Array.from(new Set(appearances.map((a) => primarySource(a.contract_id)?.source_entry_id).filter((id): id is string => Boolean(id))));
    let cancelled = false;
    ids.forEach((id) => {
      if (texts[id] !== undefined) return;
      loadWordEntry(id)
        .then((entry) => {
          if (cancelled) return;
          setTexts((current) => ({ ...current, [id]: entry.rich?.clean_text || entry.text || "" }));
        })
        .catch(() => {
          if (!cancelled) setTexts((current) => ({ ...current, [id]: null }));
        });
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appearances, sourcesByAct]);

  const givenName = String(person.fields.first_name ?? "").trim();
  const gap = largestGap(appearances);
  const titles = runs(appearances, (a) => a.title);
  const fatherTitles = runs(appearances, (a) => a.father_mother_title);
  const grandfatherTitles = runs(appearances, (a) => a.grandfather_title);
  const proxies = appearances.filter((a) => a.via_proxy);
  const heirs = appearances.filter((a) => a.heirs_of);
  const stakes = appearances.filter((a) => a.stake_names);
  const professions = runs(appearances, (a) => a.profession).filter((r) => r.value !== "none");
  const linkStatuses = entrySources.flatMap((act) => act.word_sources.map((s) => s.status));
  const distinctTitles = Array.from(new Set(appearances.map((a) => String(a.title ?? "").trim()).filter(Boolean)));
  const roleWords = (() => {
    const gp = person.role_profile?.gp ?? 0;
    const lp = person.role_profile?.lp ?? 0;
    if (gp && !lp) return "all as general partner";
    if (lp && !gp) return "all as external investor (accomandante)";
    return [gp ? `${gp} as general partner` : "", lp ? `${lp} as external investor` : ""].filter(Boolean).join(", ");
  })();
  const unmarkedIds = new Set(seam?.unmarked ?? []);

  const observed: string[] = [];
  if (gap) {
    observed.push(`No act for ${gap.years} years: ${recordedDate(gap.before.registration_date) || "?"} → ${recordedDate(gap.after.registration_date) || "?"}.`);
  }
  if (titles.some((r) => r.value !== "none")) observed.push(`His title: ${runText(titles)}.`);
  if (fatherTitles.some((r) => r.value !== "none") || grandfatherTitles.some((r) => r.value !== "none")) {
    const parts: string[] = [];
    if (fatherTitles.some((r) => r.value !== "none")) parts.push(`a father's title: ${runText(fatherTitles.filter((r) => r.value !== "none"))}`);
    if (grandfatherTitles.some((r) => r.value !== "none")) parts.push(`a grandfather's title: ${runText(grandfatherTitles.filter((r) => r.value !== "none"))}`);
    observed.push(`${parts.join("; ")}.`);
  }
  const other: string[] = [];
  if (professions.length) other.push(`profession ${runText(professions)}`);
  if (proxies.length) other.push(`made through a proxy on ${proxies.length} act${proxies.length === 1 ? "" : "s"} (${proxies.map((a) => appearanceYear(a) ?? "undated").join(", ")})`);
  if (heirs.length) other.push(`recorded through heirs on ${heirs.length} act${heirs.length === 1 ? "" : "s"} (${heirs.map((a) => appearanceYear(a) ?? "undated").join(", ")})`);
  if (stakes.length) other.push(`a stake entered under a firm's name on ${stakes.length} act${stakes.length === 1 ? "" : "s"} (${Array.from(new Set(stakes.map((a) => a.stake_names))).join("; ")})`);
  if (other.length) observed.push(`Other: ${other.join(" · ")}.`);
  if (linkStatuses.length) {
    const count = (status: string) => linkStatuses.filter((s) => s === status).length;
    observed.push(`Word summaries: ${linkStatuses.length} linked (${count("confirmed")} confirmed, ${count("proposed")} proposed, ${count("rejected")} rejected).`);
  } else {
    observed.push("Word summaries: none linked to these acts.");
  }

  const toggleSelected = (id: number) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const select = (ids: number[]) => setSelected(new Set(ids));
  const beforeGap = gap ? appearances.filter((a) => dateKey(a) <= dateKey(gap.before)) : [];
  const fromGap = gap ? appearances.filter((a) => dateKey(a) >= dateKey(gap.after)) : [];
  const withTitle = titleFilter ? appearances.filter((a) => String(a.title ?? "").trim() === titleFilter) : [];
  const markSelected = (side: PersonSeamSide) => {
    if (!onMark || selected.size === 0) return;
    onMark([...selected], side);
    setSelected(new Set());
  };

  return (
    <div className={editable ? "pl-seam is-editing" : "pl-seam"}>
      <section className="pl-seam-entry" aria-label="The entry">
        <p className="eyebrow">Entry #{person.person_id}</p>
        <h2>{recordedName(person)}</h2>
        <p className="muted">
          {person.n_appearances} appearance{person.n_appearances === 1 ? "" : "s"}
          {person.first_year && person.last_year ? `, ${person.first_year}–${person.last_year}` : ""}
          {roleWords ? ` · ${roleWords}` : ""}
          {person.fields.is_woman ? " · recorded as a woman" : ""}
        </p>
        <p className="pl-seam-definition">
          “This entry” means the man this recorded name describes, whatever the date of the act.
        </p>
      </section>

      <section className="pl-primary-visual" aria-labelledby="pl-seam-time-heading">
        <div className="pl-primary-visual-head">
          <div>
            <p className="eyebrow">Time</p>
            <h2 id="pl-seam-time-heading">The acts, by the man they are marked for</h2>
          </div>
        </div>
        <SeamRibbon appearances={appearances} marks={marks} />
      </section>

      <section className="pl-seam-observed" aria-label="Observed in the entered data">
        <p className="eyebrow">Observed in the entered data</p>
        <ul>
          {observed.map((line) => <li key={line}>{line}</li>)}
        </ul>
      </section>

      <section className="pl-seam-table" aria-label="The acts">
        <div className="pl-section-head">
          <div>
            <p className="eyebrow">The {appearances.length === 1 ? "act" : `${appearances.length} acts`}</p>
            <h2>{editable ? "Mark each act" : "How each act is marked"}</h2>
          </div>
          {editable && onOtherEntry ? (
            <label className="pl-seam-other-entry">
              Someone else may be entry
              <input
                value={otherEntry ?? ""}
                onChange={(event) => onOtherEntry(event.target.value.replace(/[^0-9]/g, ""))}
                placeholder="#…"
                inputMode="numeric"
                aria-label="Entry number of the other person, if known"
              />
              <small>optional; kept as a note</small>
            </label>
          ) : seam?.other_entry_id ? (
            <p className="muted">Someone else may be entry <Link to={`/people/split:${seam.other_entry_id}`}>#{seam.other_entry_id}</Link> (a note).</p>
          ) : null}
        </div>

        {editable ? (
          <div className="pl-seam-aids" role="group" aria-label="Select acts">
            <span className="pl-seam-aids-label">Select acts</span>
            {gap ? (
              <>
                <button type="button" onClick={() => select(beforeGap.map((a) => a.investor_id))}>
                  Before the {appearanceYear(gap.before)}–{appearanceYear(gap.after)} gap ({beforeGap.length})
                </button>
                <button type="button" onClick={() => select(fromGap.map((a) => a.investor_id))}>
                  From {appearanceYear(gap.after)} on ({fromGap.length})
                </button>
              </>
            ) : null}
            {distinctTitles.length ? (
              <select
                value={titleFilter}
                aria-label="Select acts with a title"
                onChange={(event) => {
                  const value = event.target.value;
                  setTitleFilter(value);
                  if (value) select(appearances.filter((a) => String(a.title ?? "").trim() === value).map((a) => a.investor_id));
                }}
              >
                <option value="">With a title…</option>
                {distinctTitles.map((title) => (
                  <option key={title} value={title}>
                    {title} ({appearances.filter((a) => String(a.title ?? "").trim() === title).length})
                  </option>
                ))}
              </select>
            ) : null}
            {proxies.length ? (
              <button type="button" onClick={() => select(proxies.map((a) => a.investor_id))}>Through a proxy ({proxies.length})</button>
            ) : null}
            {stakes.length ? (
              <button type="button" onClick={() => select(stakes.map((a) => a.investor_id))}>Stake under a firm's name ({stakes.length})</button>
            ) : null}
            <button type="button" onClick={() => select(appearances.map((a) => a.investor_id))}>All ({appearances.length})</button>
            <button type="button" onClick={() => select([])} disabled={selected.size === 0}>None</button>
            <span className="pl-seam-aids-mark">
              Mark selected as
              {SEAM_LANES.map((lane) => (
                <button
                  type="button"
                  key={lane.side}
                  className={`pl-seam-side is-${lane.side}`}
                  disabled={selected.size === 0}
                  onClick={() => markSelected(lane.side)}
                >
                  {lane.label}
                </button>
              ))}
              <small>{selected.size ? `${selected.size} selected` : "nothing selected"}</small>
            </span>
            {withTitle.length && titleFilter ? <small className="muted">{withTitle.length} with “{titleFilter}”</small> : null}
          </div>
        ) : null}
        <p className="muted pl-seam-aids-note">
          {editable
            ? "The buttons above only select acts. Every act starts on “This entry”."
            : "Marked by the reader."}
        </p>

        <div className="pl-table-scroll">
          <table className="pl-contract-table pl-seam-grid">
            <thead>
              <tr>
                {editable ? <th className="pl-seam-col-tick"><span className="sr-only">Select</span></th> : null}
                <th>Which man</th>
                <th>Date · act</th>
                <th>Firm · named in the summary as</th>
                <th>His title</th>
                <th>Father's title</th>
                <th>Notes</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {appearances.map((appearance) => {
                const side = marks[appearance.investor_id] ?? "unmarked";
                const source = primarySource(appearance.contract_id);
                const text = source ? texts[source.source_entry_id] : undefined;
                const phrases = text ? namePhrases(text, givenName) : [];
                const open = summaryOpen.has(appearance.investor_id);
                const notes = noteWords(appearance);
                return [
                  <tr key={appearance.investor_id} className={`pl-seam-row is-${side}${selected.has(appearance.investor_id) ? " is-selected" : ""}`}>
                    {editable ? (
                      <td className="pl-seam-col-tick">
                        <input
                          type="checkbox"
                          checked={selected.has(appearance.investor_id)}
                          onChange={() => toggleSelected(appearance.investor_id)}
                          aria-label={`Select act ${appearance.contract_id}`}
                        />
                      </td>
                    ) : null}
                    <td className="pl-seam-col-side">
                      {editable && onMark ? (
                        <span className="pl-seam-choice" role="group" aria-label={`Which man on act ${appearance.contract_id}`}>
                          {SEAM_LANES.map((lane) => (
                            <button
                              type="button"
                              key={lane.side}
                              className={side === lane.side ? `is-${lane.side} is-on` : ""}
                              aria-pressed={side === lane.side}
                              onClick={() => onMark([appearance.investor_id], lane.side)}
                            >
                              {lane.label}
                            </button>
                          ))}
                        </span>
                      ) : (
                        <span className={`pl-seam-word is-${side}`}>{SIDE_WORD[side]}</span>
                      )}
                      {unmarkedIds.has(appearance.investor_id) ? <small className="pl-seam-drift">added since the marks</small> : null}
                    </td>
                    <td className="pl-seam-col-date">
                      <Link to={`/database/contract/${appearance.contract_id}`}>{recordedDate(appearance.registration_date) || "Undated"}</Link>
                      <small>act {appearance.contract_id}{appearance.roles ? ` · ${roleListLabel(appearance.roles)}` : ""}</small>
                    </td>
                    <td className="pl-seam-col-firm">
                      <span>{appearance.firm_name || "—"}</span>
                      {source ? (
                        <i className="pl-seam-named">
                          {text === undefined ? "…" : text === null ? "summary not readable" : phrases.length ? phrases.join(" · ") : `“${givenName}” not found in the summary`}
                        </i>
                      ) : (
                        <i className="pl-seam-named is-empty">no summary linked</i>
                      )}
                    </td>
                    <td>{appearance.title || "—"}</td>
                    <td>
                      {appearance.father_mother_title || "—"}
                      {appearance.grandfather_title ? <small>grandfather: {appearance.grandfather_title}</small> : null}
                    </td>
                    <td className="pl-seam-col-notes">{notes.length ? notes.join(" · ") : "—"}</td>
                    <td className="pl-seam-col-summary">
                      {source ? (
                        <button
                          type="button"
                          className="link-like"
                          aria-expanded={open}
                          onClick={() => setSummaryOpen((current) => {
                            const next = new Set(current);
                            if (next.has(appearance.investor_id)) next.delete(appearance.investor_id);
                            else next.add(appearance.investor_id);
                            return next;
                          })}
                        >
                          {open ? "Hide ▴" : "Summary ▾"}
                        </button>
                      ) : "—"}
                    </td>
                  </tr>,
                  open && source ? (
                    <tr key={`${appearance.investor_id}-summary`} className="pl-seam-summary-row">
                      <td colSpan={editable ? 8 : 7}>
                        <WordSummaryInline source={source} defaultOpen />
                        <div className="pl-seam-summary-tools">
                          <button type="button" className="link-like" onClick={() => onOpenSource(source.source_entry_id)}>
                            Read in full, with the folio ›
                          </button>
                          {onUseEvidence && text ? (
                            <button
                              type="button"
                              className="link-like"
                              onClick={() => loadWordEntry(source.source_entry_id).then(onUseEvidence).catch(() => undefined)}
                            >
                              {citedIds?.has(source.source_entry_id) ? "✓ Attached as evidence (press to remove)" : "Use as evidence for the note"}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        </div>
        {seam?.hidden.length ? (
          <p className="muted">Hidden since the marks: {seam.hidden.map((id) => `#${id}`).join(", ")}.</p>
        ) : null}
        <p className="muted pl-seam-footnote">
          “Named in the summary as”: the words around “{givenName || "the given name"}” in each linked Word summary,
          found by a plain text search and shown as written; open the summary to check.
        </p>
      </section>
    </div>
  );
}
