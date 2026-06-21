import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  dismissFlag,
  hideRecord,
  imageUrl,
  loadDbRecord,
  loadFlags,
  relinkField,
  removePartner,
  restorePartner,
  restoreRecord,
  searchDb,
} from "../api";
import LookupCombobox from "../components/LookupCombobox";
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
  DbFlag,
  DbFlagGroup,
  DbPartnerAttrField,
  DbPartnerRow,
  DbRecord,
  DbRelink,
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

  // "Needs review" worklist (a query-param mode, orthogonal to the shown record).
  const reviewMode = searchParams.get("review") === "1";
  const [flagGroups, setFlagGroups] = useState<DbFlagGroup[]>([]);
  const [flagTotal, setFlagTotal] = useState(0);

  const loadFlagsNow = useCallback(() => {
    loadFlags()
      .then((r) => {
        setFlagGroups(r.groups);
        setFlagTotal(r.total);
      })
      .catch(() => undefined);
  }, []);

  const refreshRecord = useCallback(() => {
    if (!routeId) return;
    // include_hidden so removed partners stay visible (greyed, restorable).
    loadDbRecord(routeTable, routeId, true)
      .then(setRecord)
      .catch((err: Error) => setRecordError(err.message));
    if (reviewMode) loadFlagsNow(); // a fix drops the flag from the live list
  }, [routeTable, routeId, reviewMode, loadFlagsNow]);

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
    loadDbRecord(routeTable, routeId, true)
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
      navigate(`/database/${nextTable}`);   // (drops ?review)
    },
    [navigate],
  );

  // Keep the flag count/worklist fresh: on mount, and whenever the record changes
  // (so the tab badge is live and a just-fixed record drops off the list).
  useEffect(() => {
    loadFlagsNow();
  }, [routeId, loadFlagsNow]);

  const flagHref = useCallback((flag: DbFlag) => {
    const param = flag.fix.field ?? flag.fix.kind;
    const inv = flag.fix.investor_id ? `&inv=${flag.fix.investor_id}` : "";
    return `/database/${flag.table}/${encodeURIComponent(flag.pk)}?review=1&fix=${encodeURIComponent(param)}${inv}`;
  }, []);

  const dismissFlagNow = useCallback(
    (flag: DbFlag) => {
      let who = localStorage.getItem("floracco_reviewer") ?? "";
      if (!who.trim()) {
        who = window.prompt("Your initials (for the audit trail):") ?? "";
        if (!who.trim()) return;
        localStorage.setItem("floracco_reviewer", who.trim());
      }
      dismissFlag(flag.key, { reviewer: who.trim(), reason: "" }).then(loadFlagsNow).catch(() => undefined);
    },
    [loadFlagsNow],
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
                className={!reviewMode && tab.id === table ? "db-tab is-active" : "db-tab"}
                onClick={() => changeTable(tab.id)}
              >
                {tab.label}
              </button>
            ))}
            <button
              type="button"
              className={reviewMode ? "db-tab db-tab-review is-active" : "db-tab db-tab-review"}
              onClick={() => navigate(routeId ? `/database/${routeTable}/${routeId}?review=1` : "/database?review=1")}
              title="Records the data flags as possibly needing a fix"
            >
              ⚑ Needs review{flagTotal ? ` (${flagTotal})` : ""}
            </button>
          </div>
          {reviewMode ? (
            <p className="db-count muted">
              {flagTotal === 0
                ? "All clear — no records flagged."
                : `${flagTotal} record${flagTotal === 1 ? "" : "s"} flagged. Suggestions only — you decide; nothing changes until you edit.`}
            </p>
          ) : (
            <>
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
            </>
          )}
        </div>
        {reviewMode ? (
          <div className="db-worklist">
            {flagGroups.length === 0 && <p className="db-empty muted">All clear.</p>}
            {flagGroups.map((group) => (
              <section key={group.group} className={`worklist-group sev-${group.severity}`}>
                <header className="worklist-group-head">
                  <span className="worklist-dot" aria-hidden />
                  <h4>
                    {group.label} <span className="worklist-count">{group.items.length}</span>
                  </h4>
                </header>
                <p className="worklist-why muted">{group.explanation}</p>
                <ul>
                  {group.items.map((flag) => (
                    <li key={flag.key} className={flag.table === routeTable && flag.pk === routeId ? "is-active" : undefined}>
                      <button type="button" className="worklist-item" onClick={() => navigate(flagHref(flag))}>
                        {flag.title}
                      </button>
                      <button
                        type="button"
                        className="worklist-dismiss"
                        title="Not an issue — dismiss"
                        onClick={() => dismissFlagNow(flag)}
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
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
        )}
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
            autoFixField={searchParams.get("fix") ?? ""}
            autoFixInv={searchParams.get("inv") ?? ""}
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

/** Human-readable proposed value — maps a bool's 0/1 to No/Yes for chips. */
function proposedLabel(value: string | null | undefined, inputType?: string | null): string {
  if (inputType === "bool") return value === "1" ? "Yes" : value === "0" ? "No" : value ?? "";
  return value ?? "";
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

/** A self-contained confirm panel for removing or restoring a partner: captures
 * the reviewer + (for removal) a required reason, states the consequence, and
 * runs the audited cascade. Mirrors InlineFieldEditor's pattern. */
function PartnerActionConfirm({
  mode,
  consequence,
  warning,
  onConfirm,
  onClose,
}: {
  mode: "remove" | "restore";
  consequence: string;
  warning: string;
  onConfirm: (reviewer: string, reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Initials needed.");
      return;
    }
    if (mode === "remove" && !reason.trim()) {
      setError("A reason is required to remove a partner.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await onConfirm(reviewer.trim(), reason.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="partner-confirm">
      <p className="partner-confirm-lead">
        {mode === "remove" ? "Remove this partner? " : "Restore this partner? "}
        <span className="muted">{consequence}</span>
      </p>
      {warning && <p className="partner-confirm-warn">⚠ {warning}</p>}
      <div className="inline-editor-row">
        {mode === "remove" && (
          <input
            className="inline-editor-note"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (required)"
            aria-label="Reason for removing this partner"
          />
        )}
        <input
          className="inline-editor-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
          aria-label="Your initials"
        />
        <button type="button" className="pill-button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="pill-button is-active" onClick={run} disabled={busy}>
          {busy ? "Working…" : mode === "remove" ? "Remove" : "Restore"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

/** In-place editor for an FK field: search/pick an existing lookup phrase,
 * type a new one (stored verbatim), or clear it. Re-points via the relink
 * endpoint (create?+update) — never edits the shared phrase in place. */
function InlineLookupEditor({
  relink,
  onSaved,
  onCancel,
}: {
  relink: DbRelink;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(relink.current);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Initials needed.");
      return;
    }
    if (value.trim() === relink.current.trim()) {
      setError("Value is unchanged.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await relinkField(relink.table, relink.pk, {
        field: relink.field,
        value: value.trim(),
        reviewer: reviewer.trim(),
        reason: reason.trim(),
      });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-editor lookup-editor">
      <LookupCombobox
        kind={relink.kind}
        label=""
        value={value}
        onChange={setValue}
        placeholder="search, or type a new phrase — empty = none"
      />
      <div className="inline-editor-row">
        <input
          className="inline-editor-note"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="why? (optional)"
        />
        <input
          className="inline-editor-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
        />
        <button type="button" className="pill-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="pill-button is-active" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <p className="inline-editor-foot muted">
        Re-points to a lookup phrase (reuse / create verbatim / clear) — audited; the phrase is never edited in place.
      </p>
    </div>
  );
}

/** An FK value shown read-with-✎: click to re-point it (combobox). */
function LookupField({
  relink,
  value,
  disabled,
  onRefresh,
  autoOpen = false,
}: {
  relink: DbRelink;
  value: string;
  disabled: boolean;
  onRefresh: () => void;
  autoOpen?: boolean;
}) {
  const [editing, setEditing] = useState(autoOpen && !disabled);
  if (editing) {
    return (
      <InlineLookupEditor
        relink={relink}
        onSaved={() => {
          setEditing(false);
          onRefresh();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }
  return (
    <span className="lookup-field">
      <span className="lookup-field-value">{value || "—"}</span>
      {!disabled && (
        <button type="button" className="field-fix" onClick={() => setEditing(true)} title="Re-point to a lookup phrase">
          ✎
        </button>
      )}
    </span>
  );
}

/** The expand panel for one partner: the investor's full per-appearance record,
 * grouped, with set values emphasised and unset/empty ones muted (the ✎ appears
 * on hover). Everything stays visible so a reviewer can add a missing flag.
 * Title/place fields are read-only for now (relink deferred). */
function PartnerDetailPanel({
  attributes,
  rowKey,
  renderCell,
  hidden,
  onRefresh,
}: {
  attributes: NonNullable<DbPartnerRow["attributes"]>;
  rowKey: string;
  renderCell: (rowKey: string, cell: DbEditableCell | null) => ReactNode;
  hidden: boolean;
  onRefresh: () => void;
}) {
  const isSet = (f: DbPartnerAttrField): boolean => {
    if (f.cell) {
      if (f.cell.input_type === "bool") return f.cell.current === "1";
      return (f.cell.current ?? "").trim() !== "";
    }
    return Boolean(f.value && f.value !== "—");
  };
  return (
    <div className="partner-detail">
      {attributes.groups.map((group) => (
        <section className="partner-detail-group" key={group.label}>
          <h5>{group.label}</h5>
          <dl>
            {group.fields.map((f) => (
              <div className={`partner-attr ${isSet(f) ? "is-set" : "is-unset"}`} key={f.label}>
                <dt>{f.label}</dt>
                <dd>
                  {f.cell ? (
                    renderCell(rowKey, f.cell)
                  ) : f.relink ? (
                    <LookupField relink={f.relink} value={f.value ?? ""} disabled={hidden} onRefresh={onRefresh} />
                  ) : (
                    <span className="partner-attr-locked">{f.value}</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}

/** Investors + investments as one editable table. Role comes from the linked
 * investment (gp/lp); a joint investment is shared, so its cash is shown once
 * as shared rather than repeated per partner. Partners can be removed (an
 * audited soft-delete cascade) and restored — removed rows show greyed below. */
function PartnersBlock({
  partners,
  contractId,
  hidden,
  autoExpandInvestor = null,
  onOpen,
  onCorrectName,
  onAddInvestor,
  onRefresh,
}: {
  partners: DbRecord["partners"];
  contractId: string;
  hidden: boolean;
  autoExpandInvestor?: string | null;
  onOpen: (table: DbBrowseTable, id: string) => void;
  onCorrectName?: () => void;
  onAddInvestor: () => void;
  onRefresh: () => void;
}) {
  // Keyed by `${rowKey}:${column}` (not the cell's db_row_id) so a shared joint
  // investment opens just the one cell you clicked, not both partners' at once.
  const [editing, setEditing] = useState<string | null>(null);
  // The partner row (key) with an open remove/restore confirm.
  const [pending, setPending] = useState<{ key: string; mode: "remove" | "restore" } | null>(null);
  // The partner row (key) whose detail panel is expanded (one at a time).
  // A deep-link from the worklist (?inv=) expands the flagged partner on arrival.
  const [expanded, setExpanded] = useState<string | null>(
    autoExpandInvestor ? `investor:${autoExpandInvestor}` : null,
  );
  useEffect(() => {
    if (autoExpandInvestor) setExpanded(`investor:${autoExpandInvestor}`);
  }, [autoExpandInvestor]);
  const rows = partners?.rows ?? [];
  const count = partners?.count ?? 0;
  const liveRows = rows.filter((r) => !r.removed);
  const removedRows = rows.filter((r) => r.removed);

  const investorId = (key: string) => key.split(":")[1];

  const consequenceFor = (row: DbPartnerRow): { text: string; warning: string } => {
    const shared = row.cash.joint && row.cash.joint_count > 1;
    const text = shared
      ? "The shared tranche stays with the other partner(s); it will no longer show as joint."
      : "Their stake will be left unattached on this contract (not deleted).";
    let warning = "";
    const role = row.role?.value;
    if (role === "gp" || role === "lp") {
      const sameRole = liveRows.filter((r) => r.person && r.role?.value === role).length;
      if (sameRole <= 1) {
        warning = `This is the contract's last ${role === "gp" ? "general (gp)" : "limited (lp)"} partner.`;
      }
    }
    return { text, warning };
  };

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

  const editableCells = (row: DbPartnerRow) => (
    <>
      <td>{renderCell(row.key, row.role)}</td>
      <td>
        <div className="partner-cash">
          {row.cash.field ? renderCell(row.key, row.cash.field) : <span>{row.cash.display}</span>}
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
    </>
  );

  const personCell = (row: DbPartnerRow) =>
    row.person ? (
      <button type="button" className="db-person-link" onClick={() => onOpen("person", row.person!.id)}>
        {row.person.name}
      </button>
    ) : (
      <span className="muted">—</span>
    );

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
              <th aria-label="Expand" />
              <th>Person</th>
              <th>Role</th>
              <th>Capital</th>
              <th>Profession</th>
              <th>Residence</th>
              <th>Status</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {liveRows.map((row: DbPartnerRow) => {
              const notable = row.attributes?.notable ?? 0;
              const isOpen = expanded === row.key;
              return (
              <Fragment key={row.key}>
                <tr className={isOpen ? "partner-row-open" : undefined}>
                  <td className="partner-expand-cell">
                    {row.person && row.attributes && (
                      <button
                        type="button"
                        className="partner-expand"
                        aria-expanded={isOpen}
                        onClick={() => setExpanded(isOpen ? null : row.key)}
                        title={notable ? `${notable} recorded attribute${notable === 1 ? "" : "s"} — click to view/edit` : "View / edit all attributes"}
                      >
                        <span className="partner-chevron">{isOpen ? "▾" : "▸"}</span>
                        {notable > 0 && <span className="partner-cue">{notable}</span>}
                      </button>
                    )}
                  </td>
                  <td>{personCell(row)}</td>
                  {editableCells(row)}
                  <td className="partner-actions">
                    {!hidden && row.person && (
                      <button
                        type="button"
                        className="field-fix partner-remove"
                        onClick={() =>
                          setPending(pending?.key === row.key ? null : { key: row.key, mode: "remove" })
                        }
                        title="Remove this partner from the contract"
                      >
                        ✕ Remove
                      </button>
                    )}
                  </td>
                </tr>
                {isOpen && row.attributes && (
                  <tr className="partner-detail-row">
                    <td colSpan={8}>
                      <PartnerDetailPanel attributes={row.attributes} rowKey={row.key} renderCell={renderCell} hidden={hidden} onRefresh={onRefresh} />
                    </td>
                  </tr>
                )}
                {pending?.key === row.key && pending.mode === "remove" && (
                  <tr className="partner-confirm-row">
                    <td colSpan={8}>
                      <PartnerActionConfirm
                        mode="remove"
                        consequence={consequenceFor(row).text}
                        warning={consequenceFor(row).warning}
                        onClose={() => setPending(null)}
                        onConfirm={async (reviewer, reason) => {
                          await removePartner(contractId, investorId(row.key), { reviewer, reason });
                          setPending(null);
                          onRefresh();
                        }}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
              );
            })}
          </tbody>
        </table>
      )}

      {removedRows.length > 0 && (
        <div className="partners-removed">
          <p className="partners-removed-head muted">
            Removed partner{removedRows.length > 1 ? "s" : ""} ({removedRows.length}) — hidden from the
            record, kept in the audit trail.
          </p>
          <table className="db-table partners-table is-removed">
            <tbody>
              {removedRows.map((row: DbPartnerRow) => (
                <Fragment key={row.key}>
                  <tr className="partner-row-removed">
                    <td>{personCell(row)}</td>
                    <td className="muted">{row.role?.value ?? "—"}</td>
                    <td className="muted">{row.cash.display}</td>
                    <td className="muted">{row.profession?.value ?? "—"}</td>
                    <td className="muted">{row.residence}</td>
                    <td className="muted">removed</td>
                    <td className="partner-actions">
                      {!hidden && (
                        <button
                          type="button"
                          className="field-fix"
                          onClick={() =>
                            setPending(pending?.key === row.key ? null : { key: row.key, mode: "restore" })
                          }
                          title="Restore this partner"
                        >
                          ↩ Restore
                        </button>
                      )}
                    </td>
                  </tr>
                  {pending?.key === row.key && pending.mode === "restore" && (
                    <tr className="partner-confirm-row">
                      <td colSpan={7}>
                        <PartnerActionConfirm
                          mode="restore"
                          consequence="The partner and their link to the stake come back; a joint tranche is re-formed if applicable."
                          warning=""
                          onClose={() => setPending(null)}
                          onConfirm={async (reviewer, reason) => {
                            await restorePartner(contractId, investorId(row.key), { reviewer, reason });
                            setPending(null);
                            onRefresh();
                          }}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RecordDetail({
  record,
  autoFixField,
  autoFixInv,
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
  autoFixField: string;
  autoFixInv: string;
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

  // Close any open editor when the record changes, then act on a deep-link
  // `?fix=` from the "Needs review" worklist: open the right editor on arrival.
  // (Contract relink fields + partner-scoped fixes are auto-opened by the
  // LookupField/PartnersBlock props below; here we handle scalar + add-investor.)
  useEffect(() => {
    setEditingColumn(null);
    setAddingInvestor(false);
    if (!autoFixField) return;
    if (autoFixField === "add_investor") {
      setAddingInvestor(true);
    } else if (autoFixField !== "review_partners" && !autoFixInv) {
      const f = record.fields.find((x) => x.column === autoFixField);
      if (f) setEditingColumn(autoFixField); // scalar/bool/date/enum field
    }
    // Deps exclude record.fields on purpose: fire once per navigation, not on
    // every refresh (a save reloads the record but must not re-open the editor).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record.row_id, autoFixField, autoFixInv]);

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
            <dd>
              {field.relink ? (
                <LookupField
                  relink={field.relink}
                  value={field.value}
                  disabled={Boolean(record.is_deleted)}
                  onRefresh={closeAndRefresh}
                  autoOpen={!autoFixInv && field.relink.field === autoFixField}
                />
              ) : (
                field.value
              )}
            </dd>
            {field.correction && (
              <span className={`field-correction is-${field.correction.status}`}>
                {field.correction.status === "applied"
                  ? `Corrected → ${proposedLabel(field.correction.proposed_value, field.input_type)}`
                  : field.correction.status === "reverted"
                    ? "Correction reverted"
                    : `Change ${field.correction.status}: → ${proposedLabel(field.correction.proposed_value, field.input_type) || "(flag)"}`}
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
          contractId={record.id}
          hidden={Boolean(record.is_deleted)}
          autoExpandInvestor={autoFixInv || null}
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
