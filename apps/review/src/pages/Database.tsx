import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { hideRecord, imageUrl, loadDbRecord, restoreRecord, searchDb } from "../api";
import AddInvestorPanel from "../components/AddInvestorPanel";
import CreateRecordForm from "../components/CreateRecordForm";
import InlineFieldEditor from "../components/InlineFieldEditor";
import ManuscriptLightbox from "../components/ManuscriptLightbox";
import PersonPicker, { type PersonPick } from "../components/PersonPicker";
import WordSourceDrawer from "../components/WordSourceDrawer";
import WordSummaryInline from "../components/WordSummaryInline";
import { manuscriptImageCaption } from "../utils/manuscriptImages";
import type {
  ChangeHistoryItem,
  DbBrowseTable,
  DbEditableCell,
  DbField,
  DbLinkStatus,
  DbPartnerRow,
  DbRecord,
  DbSearchResult,
} from "../types";

// Word summaries are frozen provenance attached to DB records; the statuses
// speak in attachment language, not matcher language.
const STATUS_LABEL: Record<DbLinkStatus, string> = {
  confirmed: "Attached",
  proposed: "Suggested",
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
  const [searchParams] = useSearchParams();
  const routeTable = isBrowseTable(params.table) ? params.table : "contract";
  const routeId = params.id ?? "";
  // `/database/<table>/new` renders the creation form (ids are numeric, so
  // "new" is unambiguous); `?parent=` anchors a new act on its contract.
  const isCreating = routeId === "new" && (routeTable === "contract" || routeTable === "sub_contract");

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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
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

  const runSearch = useCallback((nextTable: DbBrowseTable, term: string, includeHidden: boolean) => {
    searchDb(nextTable, term, includeHidden)
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
    debounce.current = window.setTimeout(() => runSearch(table, search, showHidden), 220);
    return () => window.clearTimeout(debounce.current);
  }, [table, search, showHidden, runSearch]);

  // Hide/restore flow through the governed, audited op-log.
  const setHidden = useCallback(
    async (hidden: boolean) => {
      if (!record) return;
      setActionError("");
      setActionMessage("");
      if (!reviewer.trim()) {
        setActionError("Enter your initials first.");
        return;
      }
      if (hidden && !reason.trim()) {
        setActionError("A reason is required to hide a record.");
        return;
      }
      localStorage.setItem("floracco_reviewer", reviewer.trim());
      try {
        const body = { reviewer: reviewer.trim(), reason: reason.trim() };
        if (hidden) {
          await hideRecord(record.table, record.id, body);
        } else {
          await restoreRecord(record.table, record.id, body);
        }
        setReason("");
        setActionMessage(hidden ? "Record hidden." : "Record restored.");
        refreshRecord();
        runSearch(table, search, showHidden);
      } catch (err) {
        setActionError((err as Error).message);
      }
    },
    [record, reviewer, reason, refreshRecord, runSearch, table, search, showHidden],
  );

  useEffect(() => {
    setActionError("");
    setActionMessage("");
    if (!routeId || routeId === "new") {
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

  // Picker → open the person's own record, where every name field is editable
  // in place (no detour through a drawer).
  const pickPerson = useCallback(
    (person: PersonPick) => {
      setPickerOpen(false);
      const id = person.row_id.split(":")[1];
      if (id) navigate(`/database/person/${id}`);
    },
    [navigate],
  );

  return (
    <div className="db-browser">
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Database</p>
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
          {table === "contract" && (
            <button
              type="button"
              className="db-new-button"
              onClick={() => navigate("/database/contract/new")}
            >
              + New contract
            </button>
          )}
          <label className="db-show-hidden">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            Show hidden
          </label>
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
        {isCreating && (
          <CreateRecordForm
            table={routeTable as "contract" | "sub_contract"}
            parentId={searchParams.get("parent")}
          />
        )}
        {!isCreating && loadingRecord && <p className="muted">Loading record…</p>}
        {!isCreating && recordError && !loadingRecord && <p className="error-text">{recordError}</p>}
        {!isCreating && !record && !loadingRecord && !recordError && (
          <div className="db-detail-empty">
            <p className="eyebrow">Record viewer</p>
            <h2>Pick a record to inspect</h2>
            <p className="muted">
              Foreign keys are resolved to readable values; the attached Word summary, the manuscript
              page, and the full change history are shown. Hover any editable field to fix it in
              place — every change is audited and revertible.
            </p>
          </div>
        )}
        {record && !loadingRecord && (
          <RecordDetail
            record={record}
            onOpen={openRecord}
            onOpenSource={setOpenSourceId}
            onRefresh={refreshRecord}
            onOpenCreateAct={(id) => navigate(`/database/sub_contract/new?parent=${id}`)}
            onCorrectName={record.table === "contract" ? () => setPickerOpen(true) : undefined}
            reviewer={reviewer}
            onReviewerChange={setReviewer}
            reason={reason}
            onReasonChange={setReason}
            onSetHidden={setHidden}
            actionError={actionError}
            actionMessage={actionMessage}
          />
        )}
      </section>

      {openSourceId && (
        <WordSourceDrawer sourceEntryId={openSourceId} onClose={() => setOpenSourceId(null)} />
      )}

      {pickerOpen && record && record.table === "contract" && (
        <PersonPicker contractId={record.id} onPick={pickPerson} onClose={() => setPickerOpen(false)} />
      )}
    </div>
  );
}

function historyLabel(item: ChangeHistoryItem): string {
  if (item.op === "delete") return "Hidden (soft-deleted)";
  if (item.op === "restore") return "Restored";
  if (item.op === "create") return "Created";
  if (item.op === "update" || item.op === "relink") {
    return `${item.field}: ${String(item.before_value ?? "∅")} → ${String(item.after_value ?? "∅")}`;
  }
  return item.op;
}

function partnerChipLabel(c: NonNullable<DbEditableCell["correction"]>): string {
  if (c.status === "applied") return `Corrected → ${c.proposed_value ?? ""}`;
  if (c.status === "reverted") return "Correction reverted";
  return `Change ${c.status}: → ${c.proposed_value ?? "(flag)"}`;
}

/** One Partners cell: shows the value with a hover ✎, swaps to the shared
 * InlineFieldEditor while editing, and surfaces any pending correction chip. */
function PartnerCell({
  cell,
  editing,
  onEdit,
  onCancel,
  onSaved,
  disabled,
}: {
  cell: DbEditableCell | null;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSaved: () => void;
  disabled: boolean;
}) {
  if (!cell) return <span className="muted">—</span>;
  if (editing) {
    return (
      <InlineFieldEditor
        dbRowId={cell.db_row_id}
        column={cell.column}
        label={cell.column}
        inputType={cell.input_type}
        options={cell.options}
        currentValue={cell.current}
        onSaved={onSaved}
        onCancel={onCancel}
      />
    );
  }
  return (
    <div className="partner-cell">
      <span className="partner-cell-value">{cell.value}</span>
      {cell.editable && !disabled && (
        <button type="button" className="field-fix" onClick={onEdit} title={`Fix ${cell.column}`}>
          ✎
        </button>
      )}
      {cell.correction && (
        <span className={`field-correction is-${cell.correction.status}`}>
          {partnerChipLabel(cell.correction)}
        </span>
      )}
    </div>
  );
}

/** Investors + investments as one editable table. Role comes from the linked
 * investment (gp/lp); a joint investment is shared, so its cash is shown once
 * as shared rather than repeated per partner. */
function PartnersBlock({
  partners,
  hidden,
  onOpen,
  onCorrectName,
  onAddInvestor,
  onRefresh,
}: {
  partners: DbRecord["partners"];
  hidden: boolean;
  onOpen: (table: DbBrowseTable, id: string) => void;
  onCorrectName?: () => void;
  onAddInvestor: () => void;
  onRefresh: () => void;
}) {
  // Keyed by `${rowKey}:${column}` (not the cell's db_row_id) so a shared joint
  // investment opens just the one cell you clicked, not both partners' at once.
  const [editing, setEditing] = useState<string | null>(null);
  const rows = partners?.rows ?? [];
  const count = partners?.count ?? 0;

  const renderCell = (rowKey: string, cell: DbEditableCell | null) => {
    const key = cell ? `${rowKey}:${cell.column}` : "";
    return (
      <PartnerCell
        cell={cell}
        editing={Boolean(cell) && editing === key}
        onEdit={() => setEditing(key)}
        onCancel={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          onRefresh();
        }}
        disabled={hidden}
      />
    );
  };

  return (
    <section className="db-block">
      <div className="db-block-head">
        <h3>Partners ({count})</h3>
        {!hidden && onCorrectName && (
          <button type="button" className="field-fix" onClick={onCorrectName} title="Correct a person’s name">
            ✎ Fix a name
          </button>
        )}
        {!hidden && (
          <button
            type="button"
            className="field-fix"
            onClick={onAddInvestor}
            title="Add a person to this contract (role + capital)"
          >
            + Add investor
          </button>
        )}
      </div>
      {count === 0 ? (
        <p className="muted">
          No investors are recorded yet — every accomandita needs at least an accomandatario (gp) and an
          accomandante (lp).
        </p>
      ) : (
        <table className="db-table partners-table">
          <thead>
            <tr>
              <th>Person</th>
              <th>Role</th>
              <th>Capital</th>
              <th>Profession</th>
              <th>Residence</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row: DbPartnerRow) => (
              <tr key={row.key}>
                <td>
                  {row.person ? (
                    <button
                      type="button"
                      className="db-person-link"
                      onClick={() => onOpen("person", row.person!.id)}
                    >
                      {row.person.name}
                    </button>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>{renderCell(row.key, row.role)}</td>
                <td>
                  <div className="partner-cash">
                    {row.cash.field ? (
                      renderCell(row.key, row.cash.field)
                    ) : (
                      <span>{row.cash.display}</span>
                    )}
                    {row.cash.joint && (
                      <span
                        className="partner-badge"
                        title={
                          row.cash.joint_count > 1
                            ? `One tranche shared by ${row.cash.joint_count} partners — this is the shared figure, not per-person`
                            : "Recorded as a joint stake (parallel investments)"
                        }
                      >
                        joint{row.cash.joint_count > 1 ? ` · ${row.cash.joint_count}` : ""}
                      </span>
                    )}
                  </div>
                  {!["", "—", "0"].includes(row.cash.non_cash.trim()) && (
                    <p className="partner-noncash muted">+ {row.cash.non_cash}</p>
                  )}
                </td>
                <td>{renderCell(row.key, row.profession)}</td>
                <td>{row.residence}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RecordDetail({
  record,
  onOpen,
  onOpenSource,
  onRefresh,
  onOpenCreateAct,
  onCorrectName,
  reviewer,
  onReviewerChange,
  reason,
  onReasonChange,
  onSetHidden,
  actionError,
  actionMessage,
}: {
  record: DbRecord;
  onOpen: (table: DbBrowseTable, id: string) => void;
  onOpenSource: (sourceEntryId: string) => void;
  onRefresh: () => void;
  onOpenCreateAct: (contractId: string) => void;
  onCorrectName?: () => void;
  reviewer: string;
  onReviewerChange: (value: string) => void;
  reason: string;
  onReasonChange: (value: string) => void;
  onSetHidden: (hidden: boolean) => void;
  actionError: string;
  actionMessage: string;
}) {
  const [manuscriptPath, setManuscriptPath] = useState<string | null>(null);
  const [editingColumn, setEditingColumn] = useState<string | null>(null);
  const [addingInvestor, setAddingInvestor] = useState(false);
  const [investorMessage, setInvestorMessage] = useState("");
  const history = record.change_history ?? [];
  // A DB-native row was created on the platform after the Word-corpus freeze:
  // its provenance is the create op's source line, not a Word summary.
  const createdOp = history.find((item) => item.op === "create");
  const hasSubSection = record.sections.some((s) => s.title.startsWith("Sub-contracts"));
  const manuscriptImages = record.manuscript_images ?? [];
  const documentEditable = !record.is_deleted && (record.table === "contract" || record.table === "sub_contract");
  const documentCorrection = record.document_correction ?? null;
  const deps = record.dependents;
  const depParts = deps
    ? [
        deps.sub_contract && `${deps.sub_contract} sub-contract(s)`,
        deps.investor && `${deps.investor} investor(s)`,
        deps.investment && `${deps.investment} investment(s)`,
        deps.contract_place && `${deps.contract_place} place link(s)`,
      ].filter(Boolean)
    : [];

  // Close any open editor when the record changes.
  useEffect(() => {
    setEditingColumn(null);
  }, [record.row_id]);

  const closeAndRefresh = () => {
    setEditingColumn(null);
    onRefresh();
  };

  return (
    <article className={`db-record${record.is_deleted ? " is-hidden-record" : ""}`}>
      <header className="db-record-head">
        <div className="db-record-titles">
          <p className="eyebrow">{record.subtitle}</p>
          <h2>
            {record.title}
            {record.is_deleted && <span className="db-hidden-badge">Hidden</span>}
          </h2>
        </div>
        {!record.is_deleted && (
          <details className="db-actions">
            <summary>Actions</summary>
            <div className="db-actions-body">
              <div className="db-danger-body">
                <p className="muted">
                  <strong>Hide this record</strong> — soft-delete: reversible, removed from search and
                  matching, kept for audit.
                </p>
                {depParts.length > 0 && (
                  <p className="db-deps-warning">
                    ⚠ This contract has {depParts.join(", ")}. They are <strong>not</strong> hidden
                    automatically yet and will point at a hidden contract.
                  </p>
                )}
                <div className="db-record-actions">
                  <input
                    className="actionbar-reviewer"
                    value={reviewer}
                    onChange={(e) => onReviewerChange(e.target.value)}
                    placeholder="initials"
                  />
                  <input
                    className="actionbar-note"
                    value={reason}
                    onChange={(e) => onReasonChange(e.target.value)}
                    placeholder="reason (required) — e.g. duplicate of #1922"
                  />
                  <button type="button" className="pill-button is-danger" onClick={() => onSetHidden(true)}>
                    Hide record
                  </button>
                </div>
              </div>
              {actionError && <p className="error-text">{actionError}</p>}
            </div>
          </details>
        )}
      </header>

      {actionMessage && <div className="notice success">{actionMessage}</div>}

      {record.is_deleted && (
        <div className="db-hidden-banner">
          <p>
            <strong>This record is hidden</strong> — excluded from search, browse, and matching, but kept in
            full for the audit trail.
          </p>
          <div className="db-record-actions">
            <input
              className="actionbar-reviewer"
              value={reviewer}
              onChange={(e) => onReviewerChange(e.target.value)}
              placeholder="initials"
            />
            <button type="button" className="pill-button is-active" onClick={() => onSetHidden(false)}>
              Restore record
            </button>
          </div>
          {actionError && <p className="error-text">{actionError}</p>}
        </div>
      )}

      <dl className="db-fields">
        {record.fields.map((field) => (
          <div key={field.label} className="db-field">
            <dt>
              {field.label}
              {field.editable && !record.is_deleted && (
                <button
                  type="button"
                  className="field-fix"
                  onClick={() => setEditingColumn(field.column)}
                  title={`Fix ${field.label}`}
                >
                  ✎ Fix
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
            {editingColumn !== null && editingColumn === field.column && (
              <InlineFieldEditor
                dbRowId={record.row_id}
                column={field.column!}
                label={field.label}
                inputType={field.input_type ?? "text"}
                options={field.options}
                currentValue={field.current ?? ""}
                onSaved={closeAndRefresh}
                onCancel={() => setEditingColumn(null)}
              />
            )}
          </div>
        ))}
      </dl>

      {record.document != null && (
        <section className="db-block db-narrative-block">
          <div className="db-block-head">
            <h3>Narrative</h3>
            <span className="db-block-sub muted">the database’s own text</span>
            {documentEditable && editingColumn !== "document" && (
              <button
                type="button"
                className="field-fix"
                onClick={() => setEditingColumn("document")}
                title="Edit the narrative"
              >
                ✎ Edit
              </button>
            )}
          </div>
          {documentCorrection && (
            <span className={`field-correction is-${documentCorrection.status}`}>
              {documentCorrection.status === "applied"
                ? "Narrative corrected"
                : documentCorrection.status === "reverted"
                  ? "Correction reverted"
                  : `Narrative change ${documentCorrection.status}`}
            </span>
          )}
          {editingColumn === "document" ? (
            <InlineFieldEditor
              dbRowId={record.row_id}
              column="document"
              label="Narrative"
              inputType="textarea"
              currentValue={record.document ?? ""}
              onSaved={closeAndRefresh}
              onCancel={() => setEditingColumn(null)}
            />
          ) : (
            <p className="reading-text narrative db-narrative">{record.document}</p>
          )}

          {record.table !== "person" && record.word_sources.length > 0 && (
            <div className="ws-inline-list">
              <p className="ws-inline-eyebrow muted">
                Word summar{record.word_sources.length > 1 ? "ies" : "y"}, for
                comparison
              </p>
              {record.word_sources.map((source) => (
                <WordSummaryInline
                  key={`${source.via_row_id ?? ""}-${source.source_entry_id}`}
                  source={source}
                />
              ))}
            </div>
          )}
          {record.table !== "person" && record.word_sources.length === 0 && createdOp && (
            <p className="db-native-provenance muted">
              Added directly to the database by <strong>{createdOp.created_by}</strong> ·{" "}
              {createdOp.created_at.slice(0, 10)}
              {createdOp.reason ? <> · {createdOp.reason}</> : null} — no Word summary exists for
              this record (the Word corpus is frozen); this narrative is its primary text.
            </p>
          )}
        </section>
      )}

      {record.table === "person" && (record.word_sources.length > 0 || record.word_sources_note) && (
        <section className="db-block">
          <div className="db-block-head">
            <h3>Word & manuscript context</h3>
          </div>
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
                  title={source.source_entry_id}
                >
                  <div className="db-source-main">
                    <span className="db-source-top">
                      <span className={`db-source-badge is-${source.status}`}>
                        {STATUS_LABEL[source.status]}
                      </span>
                      <span className="db-source-id">
                        {[source.label, source.date].filter(Boolean).join(" · ") || source.source_entry_id}
                      </span>
                    </span>
                    {source.via && <span className="db-source-via">via {source.via}</span>}
                    <span className="db-source-meta">{source.folio ? `cc. ${source.folio}` : ""}</span>
                  </div>
                  <span className="db-source-open" aria-hidden="true">
                    Open ›
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {manuscriptImages.length > 0 && (
        <section className="db-block">
          <div className="db-block-head">
            <h3>Manuscript page</h3>
            <span className="db-block-sub muted">
              found by register & folio — the page mapping is provisional
            </span>
          </div>
          <div className="db-ms-images">
            {manuscriptImages.map((img) => (
              <figure key={img.path} className="db-ms-figure">
                <button
                  type="button"
                  className="image-zoom-button"
                  onClick={() => setManuscriptPath(img.path)}
                >
                  <img src={imageUrl(img.path)} alt={manuscriptImageCaption(img)} loading="lazy" />
                </button>
                <figcaption>
                  {manuscriptImageCaption(img)}
                  {img.needs_review ? " · mapping needs review" : ""}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      {record.table === "contract" && (
        <PartnersBlock
          partners={record.partners}
          hidden={Boolean(record.is_deleted)}
          onOpen={onOpen}
          onCorrectName={onCorrectName}
          onAddInvestor={() => setAddingInvestor((v) => !v)}
          onRefresh={closeAndRefresh}
        />
      )}

      {record.sections.map((section) => (
        <section key={section.title} className="db-block">
          <div className="db-block-head">
            <h3>{section.title}</h3>
            {record.table === "contract" && section.title.startsWith("Sub-contracts") && !record.is_deleted && (
              <button
                type="button"
                className="field-fix"
                onClick={() => onOpenCreateAct(record.id)}
                title="Add a later act (disdetta, bilancio, …) on this contract"
              >
                + Add act
              </button>
            )}
          </div>
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

      {investorMessage && <div className="notice success">{investorMessage}</div>}
      {addingInvestor && record.table === "contract" && !record.is_deleted && (
        <AddInvestorPanel
          contractId={record.id}
          contractTitle={record.title}
          onSaved={(message) => {
            setInvestorMessage(message);
            onRefresh();
          }}
          onClose={() => {
            setAddingInvestor(false);
            setInvestorMessage("");
          }}
        />
      )}

      {record.table === "contract" && !hasSubSection && !record.is_deleted && (
        <section className="db-block">
          <div className="db-block-head">
            <h3>Sub-contracts (0)</h3>
            <button
              type="button"
              className="field-fix"
              onClick={() => onOpenCreateAct(record.id)}
              title="Add a later act (disdetta, bilancio, …) on this contract"
            >
              + Add act
            </button>
          </div>
          <p className="muted">No later acts are recorded on this contract yet.</p>
        </section>
      )}

      {history.length > 0 && (
        <section className="db-block db-history">
          <h3>Change history</h3>
          <ul className="db-history-list">
            {history.map((item) => (
              <li key={item.request_id} className={`db-history-item op-${item.op} status-${item.status}`}>
                <span className="db-history-op">{historyLabel(item)}</span>
                <span className="db-history-meta">
                  {item.created_by} · {item.created_at.slice(0, 16).replace("T", " ")}
                  {item.status !== "applied" ? ` · ${item.status}` : ""}
                </span>
                {item.reason && <span className="db-history-reason">“{item.reason}”</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {manuscriptPath && (
        <ManuscriptLightbox
          src={imageUrl(manuscriptPath)}
          alt="Manuscript folio enlarged"
          onClose={() => setManuscriptPath(null)}
        />
      )}
    </article>
  );
}
