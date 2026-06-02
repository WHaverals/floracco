import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { loadDbRecord, searchDb } from "../api";
import ProposeFixDrawer, { type ProposeSeed } from "../components/ProposeFixDrawer";
import PersonPicker, { type PersonPick } from "../components/PersonPicker";
import WordSourceDrawer from "../components/WordSourceDrawer";
import type { DbBrowseTable, DbField, DbLinkStatus, DbRecord, DbSearchResult } from "../types";

const STATUS_LABEL: Record<DbLinkStatus, string> = {
  confirmed: "Confirmed",
  proposed: "Proposed",
  rejected: "Rejected",
};

const TABS: { id: DbBrowseTable; label: string }[] = [
  { id: "contract", label: "Contracts" },
  { id: "sub_contract", label: "Sub-contracts" },
  { id: "person", label: "People" },
];

function isBrowseTable(value: string | undefined): value is DbBrowseTable {
  return value === "contract" || value === "sub_contract" || value === "person";
}

export default function Database() {
  const navigate = useNavigate();
  const params = useParams<{ table?: string; id?: string }>();
  const routeTable = isBrowseTable(params.table) ? params.table : "contract";
  const routeId = params.id ?? "";

  const [table, setTable] = useState<DbBrowseTable>(routeTable);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<DbSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [shown, setShown] = useState(0);
  const [record, setRecord] = useState<DbRecord | null>(null);
  const [listError, setListError] = useState("");
  const [recordError, setRecordError] = useState("");
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);
  const [proposeSeed, setProposeSeed] = useState<ProposeSeed | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const debounce = useRef<number | undefined>(undefined);

  const refreshRecord = useCallback(() => {
    if (!routeId) return;
    loadDbRecord(routeTable, routeId)
      .then(setRecord)
      .catch((err: Error) => setRecordError(err.message));
  }, [routeTable, routeId]);

  useEffect(() => {
    setTable(routeTable);
  }, [routeTable]);

  const runSearch = useCallback((nextTable: DbBrowseTable, term: string) => {
    searchDb(nextTable, term)
      .then((response) => {
        setResults(response.results);
        setTotal(response.total);
        setShown(response.shown);
        setListError("");
      })
      .catch((err: Error) => setListError(err.message));
  }, []);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => runSearch(table, search), 220);
    return () => window.clearTimeout(debounce.current);
  }, [table, search, runSearch]);

  useEffect(() => {
    if (!routeId) {
      setRecord(null);
      return;
    }
    setLoadingRecord(true);
    loadDbRecord(routeTable, routeId)
      .then((data) => {
        setRecord(data);
        setRecordError("");
      })
      .catch((err: Error) => {
        setRecord(null);
        setRecordError(err.message);
      })
      .finally(() => setLoadingRecord(false));
  }, [routeTable, routeId]);

  const openRecord = useCallback(
    (nextTable: DbBrowseTable, id: string) => {
      if (!id) return;
      navigate(`/database/${nextTable}/${encodeURIComponent(id)}`);
    },
    [navigate],
  );

  const changeTable = useCallback(
    (nextTable: DbBrowseTable) => {
      setSearch("");
      navigate(`/database/${nextTable}`);
    },
    [navigate],
  );

  const proposeFieldFix = useCallback(
    (field: DbField) => {
      if (!record || !field.column) return;
      setProposeSeed({
        dbRowId: record.row_id,
        recordTitle: record.title,
        fieldLabel: field.label,
        column: field.column,
        inputType: field.input_type ?? "text",
        options: field.options,
        currentValue: field.current ?? "",
        wordSources: record.word_sources,
        prefillProposed: true,
      });
    },
    [record],
  );

  // Picker → correct the chosen person's surname. The contract's Word sources travel
  // along as citable evidence (the name appears in that narrative).
  const pickPersonFix = useCallback(
    (person: PersonPick) => {
      if (!record) return;
      setPickerOpen(false);
      setProposeSeed({
        dbRowId: person.row_id,
        recordTitle: person.display_name,
        fieldLabel: "Last name",
        column: "last_name",
        inputType: "text",
        options: null,
        currentValue: person.last_name,
        wordSources: record.word_sources,
        prefillProposed: Boolean(person.last_name),
      });
    },
    [record],
  );

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Database · read-only</p>
          <div className="db-tabs">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={tab.id === table ? "db-tab is-active" : "db-tab"}
                onClick={() => changeTable(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <input
            className="db-search"
            type="search"
            placeholder="Search firm, folio, name, or id…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <p className="db-count muted">
            {listError ? listError : `Showing ${shown} of ${total.toLocaleString()}`}
          </p>
        </div>
        <ul className="db-results">
          {results.map((item) => (
            <li key={item.row_id}>
              <button
                type="button"
                className={item.id === routeId && table === routeTable ? "db-result is-active" : "db-result"}
                onClick={() => openRecord(table, item.id)}
              >
                <span className="db-result-title">{item.title}</span>
                <span className="db-result-meta">{item.meta || `#${item.id}`}</span>
              </button>
            </li>
          ))}
          {results.length === 0 && !listError && (
            <li className="db-empty muted">No records match.</li>
          )}
        </ul>
      </aside>

      <section className="db-detail">
        {loadingRecord && <p className="muted">Loading record…</p>}
        {recordError && !loadingRecord && <p className="error-text">{recordError}</p>}
        {!record && !loadingRecord && !recordError && (
          <div className="db-detail-empty">
            <p className="eyebrow">Record viewer</p>
            <h2>Pick a record to inspect</h2>
            <p className="muted">
              This view is read-only. Foreign keys are resolved to readable values, and any linked
              Word source entries are listed. Edits happen in the Corrections tool, not here.
            </p>
          </div>
        )}
        {record && !loadingRecord && (
          <RecordDetail
            record={record}
            onOpen={openRecord}
            onOpenSource={setOpenSourceId}
            onProposeFix={proposeFieldFix}
            onCorrectName={record.table === "contract" ? () => setPickerOpen(true) : undefined}
          />
        )}
      </section>

      {openSourceId && (
        <WordSourceDrawer sourceEntryId={openSourceId} onClose={() => setOpenSourceId(null)} />
      )}

      {pickerOpen && record && record.table === "contract" && (
        <PersonPicker contractId={record.id} onPick={pickPersonFix} onClose={() => setPickerOpen(false)} />
      )}

      {proposeSeed && (
        <ProposeFixDrawer
          seed={proposeSeed}
          onClose={() => setProposeSeed(null)}
          onSubmitted={() => {
            setProposeSeed(null);
            refreshRecord();
          }}
        />
      )}
    </div>
  );
}

function RecordDetail({
  record,
  onOpen,
  onOpenSource,
  onProposeFix,
  onCorrectName,
}: {
  record: DbRecord;
  onOpen: (table: DbBrowseTable, id: string) => void;
  onOpenSource: (sourceEntryId: string) => void;
  onProposeFix: (field: DbField) => void;
  onCorrectName?: () => void;
}) {
  return (
    <article className="db-record">
      <header className="db-record-head">
        <p className="eyebrow">{record.subtitle}</p>
        <h2>{record.title}</h2>
        <code className="db-row-id">{record.row_id}</code>
        {onCorrectName && (
          <button type="button" className="field-fix db-correct-name" onClick={onCorrectName}>
            Correct a person’s name →
          </button>
        )}
      </header>

      <dl className="db-fields">
        {record.fields.map((field) => (
          <div key={field.label} className="db-field">
            <dt>
              {field.label}
              {field.editable && (
                <button
                  type="button"
                  className="field-fix"
                  onClick={() => onProposeFix(field)}
                  title="Suggest a fix"
                >
                  Suggest fix
                </button>
              )}
            </dt>
            <dd>{field.value}</dd>
            {field.correction && (
              <span className={`field-correction is-${field.correction.status}`}>
                {field.correction.status === "applied"
                  ? `Corrected → ${field.correction.proposed_value ?? ""}`
                  : field.correction.status === "reverted"
                    ? "Correction reverted"
                    : `Change ${field.correction.status}: → ${field.correction.proposed_value ?? "(flag)"}`}
              </span>
            )}
          </div>
        ))}
      </dl>

      {(record.word_sources.length > 0 || record.word_sources_note) && (
        <section className="db-block">
          <h3>
            {record.table === "person"
              ? "Word & manuscript context"
              : `Linked Word source${record.word_sources.length > 1 ? "s" : ""}`}
          </h3>
          {record.word_sources_note && (
            <p className="db-sources-note muted">{record.word_sources_note}</p>
          )}
          <ul className="db-sources">
            {record.word_sources.map((source) => (
              <li key={`${source.via_row_id ?? ""}-${source.source_entry_id}`}>
                <button
                  type="button"
                  className={`db-source is-${source.status}`}
                  onClick={() => onOpenSource(source.source_entry_id)}
                >
                  <div className="db-source-main">
                    <span className="db-source-top">
                      <span className={`db-source-badge is-${source.status}`}>
                        {STATUS_LABEL[source.status]}
                      </span>
                      <span className="db-source-id">{source.source_entry_id}</span>
                    </span>
                    {source.via && <span className="db-source-via">via {source.via}</span>}
                    <span className="db-source-meta">
                      {[source.date, source.folio, source.relationship]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </div>
                  {source.strength != null && (
                    <span className="db-source-strength">text {Math.round(source.strength * 100)}%</span>
                  )}
                  <span className="db-source-open" aria-hidden="true">
                    Open ›
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {record.sections.map((section) => (
        <section key={section.title} className="db-block">
          <h3>{section.title}</h3>
          <table className="db-table">
            <thead>
              <tr>
                {section.columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.rows.map((row, rowIndex) => {
                const clickable = Boolean(section.link_table && row.id);
                return (
                  <tr
                    key={`${section.title}-${row.id || rowIndex}`}
                    className={clickable ? "db-row-link" : undefined}
                    onClick={
                      clickable && section.link_table
                        ? () => onOpen(section.link_table as DbBrowseTable, row.id)
                        : undefined
                    }
                  >
                    {row.cells.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ))}

      {record.document && (
        <section className="db-block">
          <h3>Stored narrative (document field)</h3>
          <p className="reading-text narrative db-narrative">{record.document}</p>
        </section>
      )}
    </article>
  );
}
