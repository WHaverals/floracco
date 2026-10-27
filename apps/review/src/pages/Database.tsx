import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  addPlace,
  decidePersonLinkage,
  dismissFlag,
  editPlaceAddress,
  hideRecord,
  imageUrl,
  loadDbFacets,
  loadDbRecord,
  loadFlags,
  refuteJewishAttribution,
  relinkField,
  restoreFlag,
  removePartner,
  restorePartner,
  removeStake,
  restoreStake,
  restoreRecord,
  searchDb,
  setPlaceRemoved,
} from "../api";
import LookupCombobox, { lookupCheckBlock, type LookupCheck } from "../components/LookupCombobox";
import AddInvestorPanel from "../components/AddInvestorPanel";
import CreateRecordForm from "../components/CreateRecordForm";
import DbFilters from "../components/DbFilters";
import InlineFieldEditor from "../components/InlineFieldEditor";
import ManuscriptLightbox from "../components/ManuscriptLightbox";
import PersonPicker, { type PersonPick } from "../components/PersonPicker";
import RecordHistory, { historyDay } from "../components/RecordHistory";
import CopyLinkButton from "../components/CopyLinkButton";
import WordSourceDrawer from "../components/WordSourceDrawer";
import WordSummaryInline from "../components/WordSummaryInline";
import { manuscriptImageCaption } from "../utils/manuscriptImages";
import { useLatest } from "../utils/latest";
import {
  LEAVE_UNSAVED,
  confirmDiscard,
  leaveQuestion,
  unsavedLabels,
  useUnsavedChange,
} from "../utils/unsavedChanges";
import { ROLE_LABELS, roleLabel } from "../utils/roles";
import { isToolHidden } from "../features";
import type {
  DbBrowseTable,
  DbEditableCell,
  DbFacets,
  DbField,
  DbLinkStatus,
  DbFlag,
  DbFlagGroup,
  DbPartnerAttrField,
  DbPartnerRow,
  DbPlace,
  DbRecord,
  WordDateCheck,
  DbRelink,
  DbSearchResult,
  PersonIdentityHint,
  PersonSplitFlag,
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

function identityHintKind(hint?: PersonIdentityHint): "split" | "family" | "waiting" | null {
  if (!hint) return null;
  if (hint.split_flagged) return "split";
  if (hint.linked_count > 1) return "family";
  if (hint.open_count > 0) return "waiting";
  return null;
}

function identityHintLabel(kind: ReturnType<typeof identityHintKind>): string {
  if (kind === "split") return "Flagged: possibly two people in one entry";
  if (kind === "family") return "Reviewed identity family";
  if (kind === "waiting") return "Possible related records awaiting review";
  return "";
}

function formatIdentityWhen(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/* The yellow block on a flagged person: who flagged it, when, the note, and
   the two things a reviewer can do about it — read it in People, or lift it
   here with their initials. Lifting is a reopen on the flag's own case; the
   note stays in the history. */
function SplitFlagBlock({
  flag,
  reviewer,
  onReviewerChange,
  onOpenIdentity,
  onLifted,
}: {
  flag: PersonSplitFlag;
  reviewer: string;
  onReviewerChange: (value: string) => void;
  onOpenIdentity: (caseId: string) => void;
  onLifted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lift = async () => {
    if (!reviewer.trim()) {
      setError("Initials needed to lift the flag.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      localStorage.setItem("floracco_reviewer", reviewer.trim());
      await decidePersonLinkage(flag.case_id, {
        reviewer: reviewer.trim(),
        action: "reopen",
        person_ids: [flag.person_id],
        reason_code: "",
        rationale: "",
        expected_status: "flag_split",
        expected_event_id: flag.event_id,
        citations: [],
        review_mode: "standard",
      });
      onLifted();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const note = (flag.rationale ?? "").trim();
  return (
    <div className="pl-identity-split">
      <p>
        Possibly two people in one entry — flagged by {flag.created_by || "a reviewer"}
        {flag.created_at ? ` on ${formatIdentityWhen(flag.created_at)}` : ""}
        {note ? <>: &ldquo;{note}&rdquo;</> : "."}
      </p>
      {flag.marks ? (
        <p>
          {flag.marks.other} of {flag.marks.total} appearance{flag.marks.total === 1 ? "" : "s"} below{" "}
          {flag.marks.other === 1 ? "is" : "are"} marked as another person&rsquo;s
          {flag.marks.unsure ? `, ${flag.marks.unsure} as unsure` : ""}
          {flag.changed_by ? ` (marks last changed by ${flag.changed_by}${flag.changed_at ? ` on ${formatIdentityWhen(flag.changed_at)}` : ""})` : ""}
          . A reader&rsquo;s marking, not a change to the record: the entry keeps every act.
        </p>
      ) : null}
      <div className="pl-identity-split-actions">
        <button type="button" className="pill-button" onClick={() => onOpenIdentity(flag.case_id)}>
          Open in People →
        </button>
        <input
          className="inline-editor-initials"
          aria-label="Your initials"
          placeholder="Initials"
          value={reviewer}
          onChange={(event) => onReviewerChange(event.target.value)}
          disabled={busy}
        />
        <button
          type="button"
          className="pill-button"
          disabled={busy || !reviewer.trim()}
          onClick={() => void lift()}
          title="Lift this flag; the note stays in the history"
        >
          {busy ? "Lifting…" : "Lift this flag"}
        </button>
      </div>
      {error ? <p className="pl-identity-split-error">{error}</p> : null}
    </div>
  );
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
  const [sort, setSort] = useState("");
  const [results, setResults] = useState<DbSearchResult[]>([]);
  // null until the first list answer lands: the rail reads "Loading…", not "No
  // records match" (#45). Later searches keep the last total until they answer.
  const [total, setTotal] = useState<number | null>(null);
  // Browse filters (contract & sub_contract only) + their facet values.
  const [facets, setFacets] = useState<DbFacets | null>(null);
  const [facetsFailed, setFacetsFailed] = useState(false);
  const [facetsAttempt, setFacetsAttempt] = useState(0);
  const beginFacets = useLatest();
  // Open by default: register and years are the main way through 4,866 rows.
  // A reviewer who collapses the panel stays collapsed (remembered in this browser).
  // Where there is no room the panel starts closed whatever the remembered
  // preference: in the stacked layout (below 800 px) the rail sits above the
  // record, and in a short window (an iPad, a 768 px laptop) the open panel
  // leaves the list two rows tall. Wider and taller than that, it opens.
  const [filtersOpen, setFiltersOpen] = useState(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 800px), (max-height: 820px)").matches
    ) {
      return false;
    }
    try {
      return localStorage.getItem("floracco_db_filters_open") !== "0";
    } catch {
      return true;
    }
  });
  const toggleFilters = () => {
    const next = !filtersOpen;
    try {
      localStorage.setItem("floracco_db_filters_open", next ? "1" : "0");
    } catch {
      // storage unavailable: the panel still toggles for this visit
    }
    setFiltersOpen(next);
  };
  const [register, setRegister] = useState("");
  const [yearRange, setYearRange] = useState<[number, number] | null>(null);
  const [subType, setSubType] = useState("");
  const [gender, setGender] = useState("");
  const [record, setRecord] = useState<DbRecord | null>(null);
  const [listError, setListError] = useState("");
  // True while a list request is in flight, so the count line can say
  // "Searching…" instead of leaving the old list up with no sign of life.
  const [searching, setSearching] = useState(false);
  const [recordError, setRecordError] = useState("");
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  // A reason typed in "Hide this record…" is reviewer work like any edit.
  useUnsavedChange(
    Boolean(record && !record.is_deleted) && reason.trim() !== "",
    "the reason for hiding this record",
  );
  const debounce = useRef<number | undefined>(undefined);

  // "Needs review" worklist (a query-param mode, orthogonal to the shown record).
  const reviewMode = searchParams.get("review") === "1";
  const [flagGroups, setFlagGroups] = useState<DbFlagGroup[]>([]);
  // The Needs-review categories fold. The set of open ones is remembered in
  // this browser; the category holding the open record unfolds by itself; a
  // long category shows its first rows and offers the rest on request.
  const WORKLIST_OPEN_KEY = "floracco_db_worklist_open";
  const WORKLIST_FOLD_AT = 25;
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(WORKLIST_OPEN_KEY) ?? "[]");
      return new Set(Array.isArray(stored) ? stored.filter((v) => typeof v === "string") : []);
    } catch {
      return new Set();
    }
  });
  const [showAllGroups, setShowAllGroups] = useState<Set<string>>(new Set());
  const setGroupOpen = (id: string, open: boolean) =>
    setOpenGroups((prev) => {
      if (prev.has(id) === open) return prev;
      const next = new Set(prev);
      if (open) next.add(id);
      else next.delete(id);
      try {
        localStorage.setItem(WORKLIST_OPEN_KEY, JSON.stringify([...next]));
      } catch {
        // storage unavailable: the fold still works for this visit
      }
      return next;
    });
  useEffect(() => {
    if (!routeId) return;
    const holder = flagGroups.find((g) => g.items.some((f) => f.table === routeTable && f.pk === routeId));
    if (!holder) return;
    setGroupOpen(holder.group, true);
    const position = holder.items.findIndex((f) => f.table === routeTable && f.pk === routeId);
    if (position >= WORKLIST_FOLD_AT) setShowAllGroups((prev) => new Set(prev).add(holder.group));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flagGroups, routeTable, routeId]);
  // stale-response guards (one per stream — record loads, list searches, flags)
  const beginRecord = useLatest();
  const beginList = useLatest();
  const beginFlags = useLatest();
  // null until the first flags answer lands (the tab badge reads "(…)"); a
  // refresh keeps the last total, so the badge and "All clear" never flash (#45).
  const [flagTotal, setFlagTotal] = useState<number | null>(null);
  // True until the first flags response lands (and again during a refresh) so we
  // never show "All clear" while the check is still running — only once it has
  // genuinely come back with nothing.
  const [flagsLoading, setFlagsLoading] = useState(true);
  // Set when the last flags request failed; cleared by the next successful one,
  // so a check that did not run never reads as "All clear".
  const [flagsFailed, setFlagsFailed] = useState(false);

  const loadFlagsNow = useCallback(() => {
    setFlagsLoading(true);
    const fresh = beginFlags();
    loadFlags()
      .then((r) => {
        if (!fresh()) return; // e.g. a post-dismiss reload overtaken by a newer one
        setFlagGroups(r.groups);
        setFlagTotal(r.total);
        setFlagsFailed(false);
      })
      .catch(() => {
        if (fresh()) setFlagsFailed(true); // an overtaken request must not flag the newer one
      })
      .finally(() => fresh() && setFlagsLoading(false));
  }, [beginFlags]);

  const refreshRecord = useCallback(() => {
    if (!routeId) return;
    const fresh = beginRecord();
    // include_hidden so removed partners stay visible (greyed, restorable).
    loadDbRecord(routeTable, routeId, true)
      .then((data) => {
        if (!fresh()) return;
        setRecord(data);
        setRecordError(""); // a refresh that works clears an earlier failure
      })
      .catch((err: Error) => fresh() && setRecordError(err.message));
    if (reviewMode) loadFlagsNow(); // a fix drops the flag from the live list
  }, [routeTable, routeId, reviewMode, loadFlagsNow, beginRecord]);

  useEffect(() => {
    setTable(routeTable);
  }, [routeTable]);

  // Everything that determines a search result set, in one object so the search
  // effect, "Load more", and the post-edit refresh all stay in lock-step.
  const searchArgs = useMemo(
    () => ({
      table,
      q: search,
      includeHidden: showHidden,
      sort,
      register,
      yearFrom: yearRange ? yearRange[0] : null,
      yearTo: yearRange ? yearRange[1] : null,
      subType,
      gender,
    }),
    [table, search, showHidden, sort, register, yearRange, subType, gender],
  );

  const runSearch = useCallback((args: typeof searchArgs, nextOffset: number) => {
    const fresh = beginList();
    setSearching(true);
    searchDb(args.table, args.q, { ...args, offset: nextOffset })
      .then((response) => {
        if (!fresh()) return; // a stale Load-more must not append under a newer query
        // offset 0 replaces the list; a "Load more" (offset > 0) appends.
        setResults((prev) => (nextOffset > 0 ? [...prev, ...response.results] : response.results));
        setTotal(response.total);
        setListError("");
      })
      .catch((err: Error) => fresh() && setListError(err.message))
      .finally(() => fresh() && setSearching(false));
  }, [beginList]);

  // Facet values for the filters: registers/dates/types for contracts & sub-
  // contracts, gender for people.
  useEffect(() => {
    setFacetsFailed(false);
    const fresh = beginFacets();
    loadDbFacets(table)
      .then((f) => fresh() && setFacets(f))
      .catch(() => {
        if (!fresh()) return;
        setFacets(null);
        setFacetsFailed(true); // without facets the filter bar is hidden: say why
      });
  }, [table, facetsAttempt, beginFacets]);

  useEffect(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => runSearch(searchArgs, 0), 220);
    return () => window.clearTimeout(debounce.current);
  }, [searchArgs, runSearch]);

  // Hide/restore flow through the governed, audited op-log.
  const [actionBusy, setActionBusy] = useState(false);
  const setHidden = useCallback(
    async (hidden: boolean) => {
      if (!record || actionBusy) return;
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
      setActionBusy(true);
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
        runSearch(searchArgs, 0);
      } catch (err) {
        setActionError((err as Error).message);
      } finally {
        setActionBusy(false);
      }
    },
    [record, actionBusy, reviewer, reason, refreshRecord, runSearch, searchArgs],
  );

  useEffect(() => {
    setActionError("");
    setActionMessage("");
    setReason(""); // a reason belongs to the record it was typed on
    if (!routeId || routeId === "new") {
      setRecord(null);
      return;
    }
    setLoadingRecord(true);
    const fresh = beginRecord();
    loadDbRecord(routeTable, routeId, true)
      .then((data) => {
        if (!fresh()) return; // navigated on — person A must never render under B's URL
        setRecord(data);
        setRecordError("");
      })
      .catch((err: Error) => {
        if (!fresh()) return;
        setRecord(null);
        setRecordError(err.message);
      })
      .finally(() => fresh() && setLoadingRecord(false));
  }, [routeTable, routeId, beginRecord]);

  // Stacked layout (800 px and below, the breakpoint of the stacking rule at the
  // end of styles.css): the record sits under the list, inside the scrolling
  // .db-browser, so a record opened from the page lands below the fold (UI
  // review 2026-10-14, #39). Once the newly opened record has rendered, scroll
  // its top into view, unless its head is already on screen. Not on the page's
  // first load: the list stays on top.
  const browserRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const shownRoute = useRef(`${routeTable}/${routeId}`);
  const revealRoute = useRef("");
  useEffect(() => {
    const key = `${routeTable}/${routeId}`;
    if (key === shownRoute.current) return; // first load, and StrictMode's second run
    shownRoute.current = key;
    revealRoute.current = key;
  }, [routeTable, routeId]);
  useEffect(() => {
    if (!record || loadingRecord || revealRoute.current !== `${record.table}/${record.id}`) return;
    revealRoute.current = "";
    const browser = browserRef.current;
    const detail = detailRef.current;
    if (!browser || !detail || !window.matchMedia("(max-width: 800px)").matches) return;
    const offset = detail.getBoundingClientRect().top - browser.getBoundingClientRect().top;
    if (offset >= 0 && offset + 160 <= browser.clientHeight) return; // head already in view
    browser.scrollTo({ top: browser.scrollTop + offset, behavior: "smooth" });
  }, [record, loadingRecord]);

  const openRecord = useCallback(
    (nextTable: DbBrowseTable, id: string) => {
      if (!id) return;
      navigate(`/database/${nextTable}/${encodeURIComponent(id)}`);
    },
    [navigate],
  );

  const changeTable = useCallback(
    (nextTable: DbBrowseTable) => {
      // Ask before the filters below are reset: a "stay" must leave the list as it was.
      const unsaved = unsavedLabels();
      if (unsaved.length > 0 && !window.confirm(leaveQuestion(unsaved))) return;
      setSearch("");
      setSort(""); // sort keys are tab-specific (date vs name); fall back to the default
      // Facets differ per table (registers, types), so the filters reset on switch.
      setRegister("");
      setYearRange(null);
      setSubType("");
      setGender("");
      navigate(`/database/${nextTable}`, LEAVE_UNSAVED);   // (drops ?review; already asked)
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

  // "Not an issue": an in-place confirmation under the item (initials, optional
  // reason), then the item leaves the list; Undo brings it back (append-only log).
  const [dismissingKey, setDismissingKey] = useState<string | null>(null);
  const [lastDismissed, setLastDismissed] = useState<{ key: string; title: string; reviewer: string } | null>(null);
  const [dismissSaving, setDismissSaving] = useState(false);
  const [undoBusy, setUndoBusy] = useState(false);
  const [flagActionError, setFlagActionError] = useState("");
  // A decision about the open record also adds a line to its change history.
  const reloadAfterFlag = useCallback(
    (key: string) => {
      const [flagTable, flagPk] = key.split(":");
      if (flagTable === routeTable && flagPk === routeId) refreshRecord();
      else loadFlagsNow();
    },
    [routeTable, routeId, refreshRecord, loadFlagsNow],
  );
  const openDismiss = useCallback((key: string | null) => {
    if (!confirmDiscard("worklist-dismiss")) return;
    setFlagActionError("");
    setDismissingKey(key);
  }, []);
  const dismissFlagNow = useCallback(
    async (flag: DbFlag, reviewer: string, reason: string) => {
      setFlagActionError("");
      // While one dismissal saves, no other confirmation opens, so its error
      // always has a panel to show in.
      setDismissSaving(true);
      try {
        await dismissFlag(flag.key, { reviewer, reason });
      } finally {
        setDismissSaving(false);
      }
      setDismissingKey((k) => (k === flag.key ? null : k));
      setLastDismissed({ key: flag.key, title: flag.title, reviewer });
      reloadAfterFlag(flag.key);
    },
    [reloadAfterFlag],
  );
  const undoDismiss = useCallback(() => {
    if (!lastDismissed || undoBusy) return;
    const { key, reviewer } = lastDismissed;
    setFlagActionError("");
    setUndoBusy(true);
    restoreFlag(key, { reviewer })
      .then(() => {
        setLastDismissed((cur) => (cur?.key === key ? null : cur));
        reloadAfterFlag(key);
      })
      .catch((err: Error & { status?: number }) => {
        if (err.status === 409) {
          // Already back (a retried Undo whose first reply was lost): show the list as it is.
          setLastDismissed((cur) => (cur?.key === key ? null : cur));
          reloadAfterFlag(key);
          return;
        }
        setFlagActionError(`The item could not be brought back: ${err.message}`);
      })
      .finally(() => setUndoBusy(false));
  }, [lastDismissed, undoBusy, reloadAfterFlag]);
  // Opening another record, another item of the same record or leaving the
  // worklist closes an open confirmation, so the leave question about a typed
  // reason is true.
  const routeFix = searchParams.get("fix");
  const routeInv = searchParams.get("inv");
  useEffect(() => {
    setDismissingKey(null);
  }, [routeTable, routeId, routeFix, routeInv, reviewMode]);

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

  // "Load more" appends the next page (the list grows; scroll position is kept).
  const loadMore = useCallback(
    () => runSearch(searchArgs, results.length),
    [runSearch, searchArgs, results.length],
  );

  // Sort options are tab-specific: contracts/sub-contracts sort by date or id;
  // people (no date column) by name or id. Folio is omitted — its raw strings
  // ("154r-v", "97r [ORIG. 96r]") do not sort meaningfully.
  const sortOptions: { value: string; label: string }[] =
    table === "person"
      ? [
          { value: "", label: "Name (A–Z)" },
          { value: "id_asc", label: "Number (lowest)" },
          { value: "id_desc", label: "Number (highest)" },
        ]
      : [
          { value: "", label: "Date (oldest)" },
          { value: "date_desc", label: "Date (newest)" },
          { value: "id_asc", label: "Number (lowest)" },
          { value: "id_desc", label: "Number (highest)" },
        ];
  // The rail box filters THIS list; the nav box searches everything and leaves
  // the page. The label above the box says so, and the placeholder shows by
  // example what the box matches (a name, a trade, a folio, a number) in the
  // ~30 characters the rail can show without clipping.
  const searchPlaceholder =
    table === "person"
      ? "e.g. Rucellai, Bicci or 134"
      : table === "contract"
        ? "e.g. Rucellai, lana, 7r or 1558"
        : "e.g. a firm, 25v or 3641";

  // Filters: register/date/type on contract & sub-contract; gender on people.
  const showFilters =
    facets !== null && (facets.registers.length > 0 || facets.genders.length > 0);
  const registerLabel = facets?.registers.find((r) => r.folder === register)?.label ?? register;
  const genderLabel = facets?.genders.find((g) => g.value === gender)?.label ?? gender;
  const activeFilterCount =
    (register ? 1 : 0) + (yearRange ? 1 : 0) + (subType ? 1 : 0) + (gender ? 1 : 0);
  const clearFilters = () => {
    setRegister("");
    setYearRange(null);
    setSubType("");
    setGender("");
  };

  return (
    <div className="db-browser" ref={browserRef}>
      <aside className="db-rail">
        <div className="db-rail-head">
          <p className="eyebrow">Database</p>
          <div className="db-tabs db-tabs-grid">
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
              ⚑ Needs review{flagTotal === null ? (flagsFailed && !flagsLoading ? "" : " (…)") : flagTotal ? ` (${flagTotal})` : ""}
            </button>
          </div>
          {reviewMode ? (
            <p className={flagsFailed && !flagsLoading ? "db-count error-text" : "db-count muted"}>
              {flagsLoading && (flagTotal === null || flagsFailed)
                ? "Checking records…"
                : flagsFailed
                ? "The check for records that need review did not run."
                : flagTotal === 0
                ? "All clear — no records flagged."
                : `${flagTotal} record${flagTotal === 1 ? "" : "s"} flagged in ${flagGroups.length} categor${flagGroups.length === 1 ? "y" : "ies"}. Click a category to open it. Suggestions only — you decide; nothing changes until you edit.`}
            </p>
          ) : (
            <>
              <p className="db-rail-search-label">In this list</p>
              <input
                className="db-search"
                type="search"
                placeholder={searchPlaceholder}
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
              <div className="db-controls">
                <label className="db-sort">
                  <span className="db-sort-label">Sort</span>
                  <select value={sort} onChange={(event) => setSort(event.target.value)}>
                    {sortOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="db-show-hidden">
                  <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
                  Show hidden
                </label>
              </div>
              <p className={listError ? "db-count error-text" : "db-count muted"} aria-live="polite">
                {listError
                  ? listError
                  : total === null
                    ? "Loading…"
                    : searching
                    ? "Searching…"
                    : total === 0
                      ? "No records match"
                      : `Showing 1–${results.length.toLocaleString()} of ${total.toLocaleString()}`}
              </p>
              {facetsFailed && (
                <p className="db-count error-text">
                  {table === "person" ? "The filters could not be loaded." : "The register and year filters could not be loaded."}{" "}
                  <button type="button" className="link-like" onClick={() => setFacetsAttempt((n) => n + 1)}>
                    Try again
                  </button>
                </p>
              )}
              {showFilters && (
                <div className="db-filter-bar">
                  <button
                    type="button"
                    className="db-filter-toggle"
                    onClick={toggleFilters}
                    aria-expanded={filtersOpen}
                  >
                    {table === "person" ? "Filters" : "Register & years"}
                    {activeFilterCount ? ` (${activeFilterCount})` : ""} {filtersOpen ? "▴" : "▾"}
                  </button>
                  {activeFilterCount > 0 && (
                    <button type="button" className="db-filter-clear" onClick={clearFilters}>
                      Clear all
                    </button>
                  )}
                </div>
              )}
              {activeFilterCount > 0 && (
                <div className="db-active-chips">
                  {register && (
                    <button type="button" className="db-chip" onClick={() => setRegister("")}>
                      {registerLabel} <span aria-hidden>✕</span>
                    </button>
                  )}
                  {yearRange && (
                    <button type="button" className="db-chip" onClick={() => setYearRange(null)}>
                      {yearRange[0]}–{yearRange[1]} <span aria-hidden>✕</span>
                    </button>
                  )}
                  {subType && (
                    <button type="button" className="db-chip" onClick={() => setSubType("")}>
                      {subType} <span aria-hidden>✕</span>
                    </button>
                  )}
                  {gender && (
                    <button type="button" className="db-chip" onClick={() => setGender("")}>
                      {genderLabel} <span aria-hidden>✕</span>
                    </button>
                  )}
                </div>
              )}
              {showFilters && filtersOpen && facets && (
                <DbFilters
                  facets={facets}
                  register={register}
                  onRegister={setRegister}
                  yearRange={yearRange}
                  onYearRange={setYearRange}
                  subType={subType}
                  onSubType={setSubType}
                  showTypes={table === "sub_contract"}
                  gender={gender}
                  onGender={setGender}
                />
              )}
            </>
          )}
        </div>
        {reviewMode ? (
          <div className="db-worklist">
            {lastDismissed && (
              <p className="worklist-undo">
                Marked as not an issue: “{lastDismissed.title}”.{" "}
                <button type="button" className="link-like" onClick={undoDismiss} disabled={undoBusy}>
                  {undoBusy ? "Undoing…" : "Undo"}
                </button>
                {flagActionError && <span className="error-text"> {flagActionError}</span>}
              </p>
            )}
            {flagsFailed && !flagsLoading && (
              <p className="db-empty error-text">
                {flagGroups.length === 0
                  ? "The records that need review could not be loaded."
                  : "The list could not be refreshed and may be out of date."}{" "}
                <button type="button" className="link-like" onClick={loadFlagsNow}>
                  Try again
                </button>
              </p>
            )}
            {flagGroups.length === 0 &&
              (flagsLoading && (flagTotal === null || flagsFailed) ? (
                <p className="db-empty muted">Checking records for anything that needs review…</p>
              ) : flagsFailed ? null : (
                <p className="db-empty muted">All clear.</p>
              ))}
            {flagGroups.map((group) => {
              const open = openGroups.has(group.group);
              const visible = showAllGroups.has(group.group) ? group.items : group.items.slice(0, WORKLIST_FOLD_AT);
              return (
                <section key={group.group} className={`worklist-group sev-${group.severity}${open ? " is-open" : ""}`}>
                  <button
                    type="button"
                    className="worklist-group-head"
                    aria-expanded={open}
                    onClick={() => {
                      // Folding a category closes its open confirmation: ask first.
                      if (open && dismissingKey && group.items.some((f) => f.key === dismissingKey)) {
                        if (!confirmDiscard("worklist-dismiss")) return;
                        setDismissingKey(null);
                      }
                      setGroupOpen(group.group, !open);
                    }}
                  >
                    <span className="worklist-chevron" aria-hidden>
                      {open ? "▾" : "▸"}
                    </span>
                    <span className="worklist-dot" aria-hidden />
                    <span className="worklist-group-title">
                      {group.label} <span className="worklist-count">{group.items.length}</span>
                    </span>
                  </button>
                  {open && (
                    <>
                      <p className="worklist-why muted">{group.explanation}</p>
                      <ul>
                        {visible.map((flag) => (
                          <li key={flag.key} className={flag.table === routeTable && flag.pk === routeId ? "is-active" : undefined}>
                            <button type="button" className="worklist-item" onClick={() => navigate(flagHref(flag))}>
                              <span>{flag.title}</span>
                              {flag.meta && <span className="worklist-meta">{flag.meta}</span>}
                            </button>
                            <button
                              type="button"
                              className={dismissingKey === flag.key ? "worklist-dismiss is-open" : "worklist-dismiss"}
                              title="Reviewed, and the record is right: take this item off the list"
                              disabled={dismissSaving}
                              onClick={() => openDismiss(dismissingKey === flag.key ? null : flag.key)}
                            >
                              Not an issue
                            </button>
                            {dismissingKey === flag.key && (
                              <WorklistDismissConfirm
                                onConfirm={(reviewer, reason) => dismissFlagNow(flag, reviewer, reason)}
                                onClose={() => setDismissingKey(null)}
                              />
                            )}
                          </li>
                        ))}
                      </ul>
                      {visible.length < group.items.length && (
                        <button
                          type="button"
                          className="worklist-more"
                          onClick={() => setShowAllGroups((prev) => new Set(prev).add(group.group))}
                        >
                          Show all {group.items.length}
                        </button>
                      )}
                    </>
                  )}
                </section>
              );
            })}
          </div>
        ) : (
        <ul className="db-results">
          {results.map((item) => {
            const hintKind =
              table === "person" && !isToolHidden("people")
                ? identityHintKind(item.identity_hint)
                : null;
            return (
              <li key={item.row_id}>
                <button
                  type="button"
                  className={item.id === routeId && table === routeTable ? "db-result is-active" : "db-result"}
                  aria-current={item.id === routeId && table === routeTable ? "true" : undefined}
                  onClick={() => openRecord(table, item.id)}
                >
                  <span className="db-result-title-row">
                    <span className="db-result-title">{item.title}</span>
                    {hintKind ? (
                      <span
                        className="db-identity-mark"
                        role="img"
                        title={identityHintLabel(hintKind)}
                        aria-label={identityHintLabel(hintKind)}
                      />
                    ) : null}
                  </span>
                  <span className="db-result-meta">{item.meta || `#${item.id}`}</span>
                </button>
              </li>
            );
          })}
          {results.length === 0 && !listError && total !== null && (
            <li className="db-empty muted">No records match.</li>
          )}
          {results.length > 0 && total !== null && results.length < total && (
            <li className="db-load-more">
              {/* Not while a new search runs: its next page would land under the old rows. */}
              <button type="button" className="db-load-more-btn" onClick={loadMore} disabled={searching}>
                Load more <span className="db-load-more-count">({(total - results.length).toLocaleString()} more)</span>
              </button>
            </li>
          )}
        </ul>
        )}
      </aside>

      <section className="db-detail" ref={detailRef}>
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
            <h2>Pick a contract, sub-contract or person on the left</h2>
            <p className="muted">
              You will see its details, partners and places, the Word summary, the manuscript page and
              every change made so far. Move the mouse over a value and click Edit to correct it. Every
              change is recorded and can be undone.
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
            onOpenIdentity={(caseId) => navigate(`/people/${encodeURIComponent(caseId)}`)}
            onCorrectName={record.table === "contract" ? () => setPickerOpen(true) : undefined}
            reviewer={reviewer}
            onReviewerChange={setReviewer}
            reason={reason}
            onReasonChange={setReason}
            onSetHidden={setHidden}
            actionBusy={actionBusy}
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

function partnerChipLabel(c: NonNullable<DbEditableCell["correction"]>, column: string): string {
  const shown = column === "type" ? roleLabel(c.proposed_value) : (c.proposed_value ?? "");
  if (c.status === "applied") return `Corrected → ${shown}`;
  if (c.status === "reverted") return "Correction reverted";
  return `Change ${c.status}: → ${shown || "(flag)"}`;
}

/** Human-readable proposed value — maps a bool's 0/1 to No/Yes for chips. */
function proposedLabel(value: string | null | undefined, inputType?: string | null): string {
  if (inputType === "bool") return value === "1" ? "Yes" : value === "0" ? "No" : value ?? "";
  return value ?? "";
}

/** Reviewer-facing names for the editable partner columns (tooltips, editor
 * labels). Column names are storage names, not something a reader should see. */
const PARTNER_CELL_LABELS: Record<string, string> = {
  type: "role",
  investment_cash: "capital",
  profession: "profession",
};

function partnerCellLabel(column: string): string {
  return PARTNER_CELL_LABELS[column] ?? column.replace(/_/g, " ");
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
  label,
  guardScopes,
}: {
  cell: DbEditableCell | null;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSaved: () => void;
  disabled: boolean;
  /** The server's own label for a panel attribute (e.g. "Citizen of Florence"). */
  label?: string;
  guardScopes: string[];
}) {
  if (!cell) return <span className="muted">—</span>;
  if (editing) {
    return (
      <InlineFieldEditor
        dbRowId={cell.db_row_id}
        column={cell.column}
        label={label ?? partnerCellLabel(cell.column)}
        inputType={cell.input_type}
        options={cell.options}
        optionLabels={cell.column === "type" ? ROLE_LABELS : null}
        currentValue={cell.current}
        onSaved={onSaved}
        onCancel={onCancel}
        guardScopes={guardScopes}
      />
    );
  }
  const isRole = cell.column === "type";
  const shown = isRole ? (ROLE_LABELS[cell.value] ?? cell.value) : cell.value;
  return (
    <div className="partner-cell">
      <span
        className="partner-cell-value"
        title={isRole && ROLE_LABELS[cell.value] ? `Recorded as “${cell.value}”` : undefined}
      >
        {shown}
      </span>
      {cell.editable && !disabled && (
        <button type="button" className="field-fix" onClick={onEdit} title={`Edit ${partnerCellLabel(cell.column)}`}>
          ✎ Edit
        </button>
      )}
      {cell.correction && (
        <span className={`field-correction is-${cell.correction.status}`}>
          {partnerChipLabel(cell.correction, cell.column)}
        </span>
      )}
    </div>
  );
}

/** The Needs-review "Not an issue" confirmation, under the item on the dark rail:
 * initials and an optional reason; the record itself is not changed. */
function WorklistDismissConfirm({
  onConfirm,
  onClose,
}: {
  onConfirm: (reviewer: string, reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useUnsavedChange(reason.trim() !== "" && !busy, "the reason for marking this item as not an issue", [
    "worklist-dismiss",
  ]);

  const run = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Initials needed.");
      return;
    }
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await onConfirm(reviewer.trim(), reason.trim());
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="worklist-confirm">
      <p className="worklist-confirm-lead">
        Take this item off the list? The record is not changed, and you can undo this right after.
      </p>
      <input
        className="worklist-confirm-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="reason (optional)"
        aria-label="Reason (optional)"
      />
      <div className="worklist-confirm-row">
        <input
          className="worklist-confirm-initials"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="initials"
          aria-label="Your initials"
        />
        <button type="button" className="worklist-confirm-button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="worklist-confirm-button is-primary" onClick={run} disabled={busy}>
          {busy ? "Saving…" : "Not an issue"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

/** A self-contained confirm panel for removing or restoring a partner: captures
 * the reviewer + (for removal) a required reason, states the consequence, and
 * runs the audited cascade. Mirrors InlineFieldEditor's pattern. */
function PartnerActionConfirm({
  mode,
  subject = "partner",
  consequence,
  warning,
  onConfirm,
  onClose,
  guardScope,
}: {
  mode: "remove" | "restore";
  /** What is being removed or restored — a partner, a stake nobody holds, or a place. */
  subject?: "partner" | "stake" | "place";
  consequence: string;
  warning: string;
  onConfirm: (reviewer: string, reason: string) => Promise<void>;
  onClose: () => void;
  /** Lets the parent ask before replacing this panel while a reason is typed. */
  guardScope?: string;
}) {
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useUnsavedChange(
    mode === "remove" && reason.trim() !== "" && !busy,
    `the reason for removing this ${subject}`,
    guardScope ? [guardScope] : [],
  );

  const run = async () => {
    setError("");
    if (!reviewer.trim()) {
      setError("Initials needed.");
      return;
    }
    if (mode === "remove" && !reason.trim()) {
      setError(`A reason is required to remove a ${subject}.`);
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
        {mode === "remove" ? `Remove this ${subject}? ` : `Restore this ${subject}? `}
        <span className="muted">{consequence}</span>
      </p>
      {warning && <p className="partner-confirm-warn">{warning}</p>}
      <div className="inline-editor-row">
        {mode === "remove" && (
          <input
            className="inline-editor-note"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (required)"
            aria-label={`Reason for removing this ${subject}`}
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
  label,
  guardScopes,
}: {
  relink: DbRelink;
  onSaved: () => void;
  onCancel: () => void;
  label?: string;
  guardScopes?: string[];
}) {
  const [value, setValue] = useState(relink.current);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [check, setCheck] = useState<LookupCheck>("idle");
  useUnsavedChange(
    (value !== relink.current || reason.trim() !== "") && !busy,
    label ? `“${label}”` : `the ${relink.kind.replace(/_/g, " ")}`,
    guardScopes,
  );

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
    const wait = lookupCheckBlock(check);
    if (wait) {
      setError(wait);
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
        placeholder="Search, or type the phrase as written (empty for none)"
        onCheckChange={setCheck}
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
        Saving links this field to the phrase you pick, or to a new phrase stored exactly as you typed it; an empty
        box clears the field. The phrase itself is not changed here, since other records may share it. The change is
        recorded under your initials.
      </p>
    </div>
  );
}

/** What clicking ✎ on a lookup value does, in the reviewer's words. */
const LOOKUP_EDIT_TITLES: Record<DbRelink["kind"], string> = {
  currency: "Choose another currency",
  economic_activity: "Choose another economic activity",
  place: "Choose another place",
  title: "Choose another title",
};

/** An FK value shown read-with-✎: click to re-point it (combobox). */
function LookupField({
  relink,
  value,
  disabled,
  onRefresh,
  autoOpen = false,
  label,
  guardScopes,
}: {
  relink: DbRelink;
  value: string;
  disabled: boolean;
  onRefresh: () => void;
  autoOpen?: boolean;
  /** The field's label, so a leave question can name it. */
  label?: string;
  guardScopes?: string[];
}) {
  const [editing, setEditing] = useState(autoOpen && !disabled);
  if (editing) {
    return (
      <InlineLookupEditor
        relink={relink}
        label={label}
        guardScopes={guardScopes}
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
        <button
          type="button"
          className="field-fix"
          onClick={() => setEditing(true)}
          title={LOOKUP_EDIT_TITLES[relink.kind] ?? "Choose another value"}
        >
          ✎ Edit
        </button>
      )}
    </span>
  );
}

/** The one sanctioned write to the legacy editorial layer: judge a 2010s Jewish
 * attribution erroneous (jewish_db 1→0), audited with a required reason. The
 * platform never SETS that flag — this control only removes it. */
function RefuteAttribution({
  investorId,
  onDone,
  guardScopes = [],
}: {
  investorId: string;
  onDone: () => void;
  guardScopes?: string[];
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Before the early return, so the hook order stays the same.
  useUnsavedChange(open && reason.trim() !== "" && !busy, "the reason for refuting the attribution", guardScopes);
  if (!open) {
    return (
      <button
        type="button"
        className="field-fix"
        onClick={() => setOpen(true)}
        title="Mark this 2010s attribution as mistaken. A reason is required; the change is recorded under your initials."
      >
        ✕ Refute
      </button>
    );
  }
  const save = async () => {
    if (!reviewer.trim() || !reason.trim()) {
      setError("Initials and a reason are required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await refuteJewishAttribution(investorId, { reviewer: reviewer.trim(), reason: reason.trim() });
      localStorage.setItem("floracco_reviewer", reviewer.trim());
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };
  return (
    <span className="refute-inline">
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="why is it wrong? (required)"
        aria-label="Reason for refuting the attribution"
      />
      <input
        className="refute-initials"
        value={reviewer}
        onChange={(e) => setReviewer(e.target.value)}
        placeholder="initials"
        aria-label="Reviewer initials"
      />
      <button type="button" className="pill-button" onClick={() => setOpen(false)} disabled={busy}>
        Cancel
      </button>
      <button type="button" className="pill-button is-danger" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Refute"}
      </button>
      {error && <span className="error-text">{error}</span>}
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
  renderCell: (rowKey: string, cell: DbEditableCell | null, opts?: { label?: string; panel?: boolean }) => ReactNode;
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
                <dt title={f.note ?? undefined} className={f.note ? "has-note" : undefined}>
                  {f.label}
                </dt>
                <dd>
                  {f.cell ? (
                    renderCell(rowKey, f.cell, { label: f.label, panel: true })
                  ) : f.relink ? (
                    <LookupField
                      relink={f.relink}
                      value={f.value ?? ""}
                      label={f.label}
                      guardScopes={[`partner-panel:${rowKey}`]}
                      disabled={hidden}
                      onRefresh={onRefresh}
                    />
                  ) : (
                    <span className="partner-attr-locked">{f.value}</span>
                  )}
                  {f.refute && !hidden && (
                    <RefuteAttribution
                      investorId={f.refute.investor_id}
                      guardScopes={[`partner-panel:${rowKey}`]}
                      onDone={onRefresh}
                    />
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
  onAttachTo,
  onRefresh,
}: {
  partners: DbRecord["partners"];
  contractId: string;
  hidden: boolean;
  autoExpandInvestor?: string | null;
  onOpen: (table: DbBrowseTable, id: string) => void;
  onCorrectName?: () => void;
  onAddInvestor: () => void;
  /** Open the add-investor panel on a stake nobody holds, so a person can take it over. */
  onAttachTo?: (investmentId: string) => void;
  onRefresh: () => void;
}) {
  // Keyed by `${rowKey}:${column}` (not the cell's db_row_id) so a shared joint
  // investment opens just the one cell you clicked, not both partners' at once.
  const [editing, setEditing] = useState<string | null>(null);
  // The partner row (key) with an open remove/restore confirm.
  const [pending, setPending] = useState<{ key: string; mode: "remove" | "restore" } | null>(null);
  const unattachedLabel = (
    <span
      className="partner-unattached"
      title="A role and a sum recorded without a person: left over from data entry, or a partner who was removed. Attach the right person, or remove the stake if it was entered in error."
    >
      No partner attached
    </span>
  );
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
      ? row.cash.joint_count === 2
        ? "The shared stake stays with the other partner; it will no longer show as joint."
        : "The shared stake stays with the other partners."
      : "Their stake stays on the contract with no partner attached. You can attach someone else or remove it afterwards.";
    let warning = "";
    const role = row.role?.value;
    if (role === "gp" || role === "lp") {
      const sameRole = liveRows.filter((r) => r.person && r.role?.value === role).length;
      if (sameRole <= 1) {
        warning = `This is the contract's last ${roleLabel(role).toLowerCase()}.`;
      }
    }
    return { text, warning };
  };
  const unattachedCount = liveRows.filter((r) => r.unattached).length;

  const renderCell = (rowKey: string, cell: DbEditableCell | null, opts?: { label?: string; panel?: boolean }) => {
    const key = cell ? `${rowKey}:${cell.column}` : "";
    return (
      <PartnerCell
        cell={cell}
        editing={Boolean(cell) && editing === key}
        onEdit={() => {
          // One editor at a time: ask before the open one is replaced.
          if (editing && editing !== key && !confirmDiscard("partner-cell")) return;
          setEditing(key);
        }}
        onCancel={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          onRefresh();
        }}
        disabled={hidden}
        label={opts?.label}
        guardScopes={opts?.panel ? ["partner-cell", `partner-panel:${rowKey}`] : ["partner-cell"]}
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
                  ? `One stake shared by ${row.cash.joint_count} partners. This is the shared figure, not each partner's share.`
                  : "Recorded as joint; the co-holders' stakes were entered separately"
              }
            >
              {/* Non-breaking spaces: at most two lines ("shared by 2" / "partners"). */}
              {row.cash.joint_count > 1 ? `shared\u00a0by\u00a0${row.cash.joint_count} partners` : "joint stake"}
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
    ) : row.unattached ? (
      unattachedLabel
    ) : (
      <span className="muted">—</span>
    );

  return (
    <section className="db-block">
      <div className="db-block-head">
        <h3>Partners ({count})</h3>
        {!hidden && onCorrectName && (
          <button type="button" className="field-fix" onClick={onCorrectName} title="Correct a person’s name">
            ✎ Edit a name
          </button>
        )}
        {!hidden && (
          <button
            type="button"
            className="field-fix"
            onClick={onAddInvestor}
            title="Add a person to this contract, with role and capital"
          >
            + Add partner
          </button>
        )}
      </div>
      {unattachedCount > 0 && (
        <p className="notice warning partners-notice">
          {unattachedCount === 1 ? "One stake" : `${unattachedCount} stakes`} on this contract{" "}
          {unattachedCount === 1 ? "has" : "have"} no partner attached. Attach the right person, or remove
          the stake if it was entered in error.
        </p>
      )}
      {count === 0 ? (
        <p className="muted">
          No partners are recorded yet. Every accomandita needs at least one general partner (accomandatario)
          and one external investor (accomandante).
        </p>
      ) : (
        <div className="partners-scroll">
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
              const panelTitle = notable
                ? `${notable} recorded attribute${notable === 1 ? "" : "s"} — click to view/edit`
                : "View / edit all attributes";
              const togglePanel = () => {
                // Collapsing or switching the panel closes its editors: ask first.
                if (expanded && !confirmDiscard(`partner-panel:${expanded}`)) return;
                setExpanded(isOpen ? null : row.key);
              };
              return (
              <Fragment key={row.key}>
                <tr className={isOpen ? "partner-row-open" : undefined}>
                  <td className="partner-expand-cell">
                    {row.person && row.attributes && (
                      <button
                        type="button"
                        className="partner-expand"
                        aria-expanded={isOpen}
                        onClick={togglePanel}
                        title={panelTitle}
                      >
                        <span className="partner-chevron">{isOpen ? "▾" : "▸"}</span>
                      </button>
                    )}
                  </td>
                  <td>
                    {personCell(row)}
                    {/* Under the name, not beside the chevron: a cue column wider than the
                        names pushed the table past a laptop-size record (#38). */}
                    {row.person && row.attributes && notable > 0 && (
                      <button type="button" className="partner-cue" onClick={togglePanel} title={panelTitle}>
                        {notable} more {notable === 1 ? "detail" : "details"}
                      </button>
                    )}
                  </td>
                  {editableCells(row)}
                  <td className="partner-actions">
                    {!hidden && row.person && (
                      <button
                        type="button"
                        className="field-fix partner-remove"
                        onClick={() => {
                          if (pending && !confirmDiscard("partner-confirm")) return;
                          setPending(pending?.key === row.key ? null : { key: row.key, mode: "remove" });
                        }}
                        title="Remove this partner from the contract"
                      >
                        ✕ Remove
                      </button>
                    )}
                    {!hidden && row.unattached && row.investment_id && (
                      <span className="partner-stake-actions">
                        {onAttachTo && (
                          <button
                            type="button"
                            className="field-fix"
                            onClick={() => onAttachTo(row.investment_id!)}
                            title="Add a person who takes over this stake"
                          >
                            + Attach a person
                          </button>
                        )}
                        <button
                          type="button"
                          className="field-fix partner-remove"
                          onClick={() => {
                            if (pending && !confirmDiscard("partner-confirm")) return;
                            setPending(pending?.key === row.key ? null : { key: row.key, mode: "remove" });
                          }}
                          title="Remove this stake from the contract (reversible)"
                        >
                          ✕ Remove stake
                        </button>
                      </span>
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
                      {row.unattached && row.investment_id ? (
                        <PartnerActionConfirm
                          guardScope="partner-confirm"
                          mode="remove"
                          subject="stake"
                          consequence="The role and sum leave the record; nothing else changes. Restorable under Show hidden."
                          warning=""
                          onClose={() => setPending(null)}
                          onConfirm={async (reviewer, reason) => {
                            await removeStake(contractId, row.investment_id!, { reviewer, reason });
                            setPending(null);
                            onRefresh();
                          }}
                        />
                      ) : (
                        <PartnerActionConfirm
                          guardScope="partner-confirm"
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
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
      )}

      {removedRows.length > 0 && (
        <div className="partners-removed">
          <p className="partners-removed-head muted">
            Removed ({removedRows.length}) — hidden from the record, kept in the audit trail.
          </p>
          <div className="partners-scroll">
          <table className="db-table partners-table is-removed">
            <tbody>
              {removedRows.map((row: DbPartnerRow) => (
                <Fragment key={row.key}>
                  <tr className="partner-row-removed">
                    <td>{personCell(row)}</td>
                    <td
                      className="muted"
                      title={row.role && ROLE_LABELS[row.role.value] ? `Recorded as “${row.role.value}”` : undefined}
                    >
                      {row.role ? roleLabel(row.role.value) : "—"}
                    </td>
                    <td className="muted">{row.cash.display}</td>
                    <td className="muted">{row.profession?.value ?? "—"}</td>
                    <td className="muted">{row.residence}</td>
                    <td className="muted">{row.unattached ? "removed stake" : "removed"}</td>
                    <td className="partner-actions">
                      {!hidden && (
                        <button
                          type="button"
                          className="field-fix"
                          onClick={() => {
                            if (pending && !confirmDiscard("partner-confirm")) return;
                            setPending(pending?.key === row.key ? null : { key: row.key, mode: "restore" });
                          }}
                          title={row.unattached ? "Restore this stake" : "Restore this partner"}
                        >
                          Restore{row.unattached ? " stake" : ""}
                        </button>
                      )}
                    </td>
                  </tr>
                  {pending?.key === row.key && pending.mode === "restore" && (
                    <tr className="partner-confirm-row">
                      <td colSpan={7}>
                        {row.unattached && row.investment_id ? (
                          <PartnerActionConfirm
                            guardScope="partner-confirm"
                            mode="restore"
                            subject="stake"
                            consequence="The role and sum come back on the record, still with no partner attached."
                            warning=""
                            onClose={() => setPending(null)}
                            onConfirm={async (reviewer, reason) => {
                              await restoreStake(contractId, row.investment_id!, { reviewer, reason });
                              setPending(null);
                              onRefresh();
                            }}
                          />
                        ) : (
                          <PartnerActionConfirm
                            guardScope="partner-confirm"
                            mode="restore"
                            consequence="The partner and their link to the stake come back; a stake they shared is joint again."
                            warning=""
                            onClose={() => setPending(null)}
                            onConfirm={async (reviewer, reason) => {
                              await restorePartner(contractId, investorId(row.key), { reviewer, reason });
                              setPending(null);
                              onRefresh();
                            }}
                          />
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </section>
  );
}

/** Inline editor for a place's free-text address (composite-PK row → its own
 * endpoint, not the shared correction flow). */
function PlaceAddressEditor({
  contractId,
  placeId,
  current,
  onSaved,
  onCancel,
}: {
  contractId: string;
  placeId: string;
  current: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(current);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useUnsavedChange(value !== current && !busy, "the address", ["place-address"]);
  const save = async () => {
    setError("");
    if (!reviewer.trim()) return setError("Initials needed.");
    if (value.trim() === current.trim()) return setError("Address is unchanged.");
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await editPlaceAddress(contractId, placeId, { address: value.trim(), reviewer: reviewer.trim(), reason: "" });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="inline-editor">
      <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="address (leave empty for none)" autoFocus />
      <div className="inline-editor-row">
        <input className="inline-editor-initials" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="initials" />
        <button type="button" className="pill-button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="pill-button is-active" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

/** Add a place to a contract: pick/create the place (combobox) + optional address. */
function AddPlaceForm({ contractId, onSaved, onCancel }: { contractId: string; onSaved: () => void; onCancel: () => void }) {
  const [place, setPlace] = useState("");
  const [address, setAddress] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("floracco_reviewer") ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [placeCheck, setPlaceCheck] = useState<LookupCheck>("idle");
  useUnsavedChange((place.trim() !== "" || address.trim() !== "") && !busy, "the new place", ["place-add"]);
  const save = async () => {
    setError("");
    if (!place.trim()) return setError("A place is required.");
    const wait = lookupCheckBlock(placeCheck, "the place");
    if (wait) return setError(wait);
    if (!reviewer.trim()) return setError("Initials needed.");
    localStorage.setItem("floracco_reviewer", reviewer.trim());
    setBusy(true);
    try {
      await addPlace(contractId, { place: place.trim(), address: address.trim(), reviewer: reviewer.trim(), reason: "" });
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
        kind="place"
        label=""
        value={place}
        onChange={setPlace}
        placeholder="search, or type a new place"
        onCheckChange={setPlaceCheck}
      />
      <div className="inline-editor-row">
        <input className="inline-editor-note" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="address (optional)" />
        <input className="inline-editor-initials" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="initials" />
        <button type="button" className="pill-button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="pill-button is-active" onClick={save} disabled={busy}>{busy ? "Adding…" : "Add place"}</button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

/** The contract's place(s), editable: edit the address, remove/restore a place,
 * or add one. Re-pointing a place = remove + add (its id is part of the key). */
function PlacesBlock({
  places,
  contractId,
  hidden,
  onRefresh,
}: {
  places: DbRecord["places"];
  contractId: string;
  hidden: boolean;
  onRefresh: () => void;
}) {
  const [pending, setPending] = useState<{ key: string; mode: "remove" | "restore" } | null>(null);
  const [editingAddr, setEditingAddr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const rows = places?.rows ?? [];
  const count = places?.count ?? 0;
  const live = rows.filter((r) => !r.removed);
  const removed = rows.filter((r) => r.removed);
  const placeId = (key: string) => key.split(":")[1];

  return (
    <section className="db-block">
      <div className="db-block-head">
        <h3>Places ({count})</h3>
        {!hidden && (
          <button type="button" className="field-fix" onClick={() => {
              if (adding && !confirmDiscard("place-add")) return;
              setAdding((v) => !v);
            }} title="Add a place this firm operated in">
            + Add place
          </button>
        )}
      </div>
      {adding && !hidden && (
        <AddPlaceForm contractId={contractId} onCancel={() => setAdding(false)} onSaved={() => { setAdding(false); onRefresh(); }} />
      )}
      {count === 0 && !adding ? (
        <p className="muted">No place is recorded for this contract.</p>
      ) : (
        <table className="db-table">
          <thead>
            <tr>
              <th>Place</th>
              <th>Address</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {live.map((row: DbPlace) => (
              <Fragment key={row.key}>
                <tr>
                  <td>{row.place}</td>
                  <td>
                    {editingAddr === row.key ? (
                      <PlaceAddressEditor
                        contractId={contractId}
                        placeId={row.place_id}
                        current={row.address}
                        onCancel={() => setEditingAddr(null)}
                        onSaved={() => { setEditingAddr(null); onRefresh(); }}
                      />
                    ) : (
                      <span className="lookup-field">
                        <span className="lookup-field-value">{row.address || "—"}</span>
                        {!hidden && (
                          <button type="button" className="field-fix" onClick={() => {
                          if (editingAddr && !confirmDiscard("place-address")) return;
                          setEditingAddr(row.key);
                        }} title="Edit the address">✎ Edit</button>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="partner-actions">
                    {!hidden && (
                      <button
                        type="button"
                        className="field-fix partner-remove"
                        onClick={() => {
                          if (pending && !confirmDiscard("place-confirm")) return;
                          setPending(pending?.key === row.key ? null : { key: row.key, mode: "remove" });
                        }}
                        title="Remove this place from the contract"
                      >
                        ✕ Remove
                      </button>
                    )}
                  </td>
                </tr>
                {pending?.key === row.key && pending.mode === "remove" && (
                  <tr className="partner-confirm-row">
                    <td colSpan={3}>
                      <PartnerActionConfirm
                        subject="place"
                        guardScope="place-confirm"
                        mode="remove"
                        consequence={`"${row.place}" will be removed from this contract.`}
                        warning=""
                        onClose={() => setPending(null)}
                        onConfirm={async (reviewer, reason) => {
                          await setPlaceRemoved(contractId, row.place_id, true, { reviewer, reason });
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
      )}

      {removed.length > 0 && (
        <div className="partners-removed">
          <p className="partners-removed-head muted">
            Removed place{removed.length > 1 ? "s" : ""} ({removed.length}) — kept in the audit trail.
          </p>
          <table className="db-table is-removed">
            <tbody>
              {removed.map((row: DbPlace) => (
                <Fragment key={row.key}>
                  <tr className="partner-row-removed">
                    <td className="muted">{row.place}</td>
                    <td className="muted">{row.address || "—"}</td>
                    <td className="partner-actions">
                      {!hidden && (
                        <button
                          type="button"
                          className="field-fix"
                          onClick={() => {
                            if (pending && !confirmDiscard("place-confirm")) return;
                            setPending(pending?.key === row.key ? null : { key: row.key, mode: "restore" });
                          }}
                          title="Restore this place"
                        >
                          Restore
                        </button>
                      )}
                    </td>
                  </tr>
                  {pending?.key === row.key && pending.mode === "restore" && (
                    <tr className="partner-confirm-row">
                      <td colSpan={3}>
                        <PartnerActionConfirm
                          subject="place"
                          guardScope="place-confirm"
                          mode="restore"
                          consequence="The place comes back on this contract."
                          warning=""
                          onClose={() => setPending(null)}
                          onConfirm={async (reviewer, reason) => {
                            await setPlaceRemoved(contractId, row.place_id, false, { reviewer, reason });
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

/** Evidence-only "Word source — differs" affordance on the registration-date field.
 * Shows the calendar-normalized comparison, the tracked-change lens (T1), and the folio
 * image (correct page + adjacent leaves for the dual-numbering off-by-one). It never
 * asserts the DB is wrong and never edits — the reviewer verifies, then uses ✎ Edit. */
function WordDateCheckPanel({ check, autoOpen = false }: { check: WordDateCheck; autoOpen?: boolean }) {
  const [open, setOpen] = useState(autoOpen);
  const [lightbox, setLightbox] = useState(false);
  // Open on a worklist deep-link (?fix=word_date); reset to default when the record changes.
  useEffect(() => {
    setOpen(autoOpen);
  }, [check.db_row_id]); // eslint-disable-line react-hooks/exhaustive-deps
  // Leaf order prev · primary · next, so the reviewer can leaf around an off-by-one link.
  const leaves = (["prev", "primary", "next"] as const)
    .map((k) => check.images[k])
    .filter((p): p is string => Boolean(p));
  const startIdx = Math.max(0, leaves.indexOf(check.images.primary ?? ""));
  const [leaf, setLeaf] = useState(startIdx);
  const tracked = check.tier === "tracked_change";

  const nav =
    leaves.length > 1 ? (
      <>
        <button type="button" className="img-nav-btn" disabled={leaf === 0} onClick={() => setLeaf((i) => Math.max(0, i - 1))} aria-label="Previous leaf">◀</button>
        <span className="img-counter">leaf {leaf + 1} / {leaves.length}</span>
        <button type="button" className="img-nav-btn" disabled={leaf >= leaves.length - 1} onClick={() => setLeaf((i) => Math.min(leaves.length - 1, i + 1))} aria-label="Next leaf">▶</button>
      </>
    ) : null;

  return (
    <div className={`word-check ${tracked ? "is-tracked" : "is-clear"}`}>
      <button type="button" className="word-check-tag" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="word-check-dot" aria-hidden />
        Word source — differs
        <span className="word-check-kind">{tracked ? "revision" : `±${check.gap_days}d`}</span>
        <span className="word-check-chev" aria-hidden>{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="word-check-body">
          <p className="word-check-statement">
            Word source records this registration date as <strong>{check.word_display}</strong>; the
            database records <strong>{check.db_display}</strong>. Consult the narrative and folio image
            before correcting.
          </p>
          <p className="word-check-raw muted">transcribed: “{check.word_raw}”</p>

          {check.revision && (
            <p className="word-check-rev">
              <span className="word-check-rev-label">Revision in the Word source:</span>{" "}
              {check.revision.removed.map((t, i) => (
                <span key={`d${i}`} className="rev-del">{t}</span>
              ))}
              {check.revision.removed.length > 0 && check.revision.added.length > 0 ? " → " : null}
              {check.revision.added.map((t, i) => (
                <span key={`a${i}`} className="rev-ins">{t}</span>
              ))}
              {check.revision.author ? <span className="muted"> · {check.revision.author}</span> : null}
            </p>
          )}

          {leaves.length > 0 && (
            <button type="button" className="manuscript-thumb word-check-thumb" onClick={() => { setLeaf(startIdx); setLightbox(true); }} title="Open the folio — zoom and pan">
              <img src={imageUrl(leaves[startIdx])} alt={`Folio ${check.folio ?? ""}`} loading="lazy" />
              <span className="manuscript-thumb-label">
                Folio {check.folio ?? "?"}{check.page_side ? ` · ${check.page_side}` : ""}
                <span className="manuscript-thumb-zoom">⤢ inspect</span>
              </span>
            </button>
          )}

          <p className="word-check-hint muted">
            Word is evidence, not the truth — verify against the image, then correct with ✎ Edit above
            only if the database is wrong.
          </p>
        </div>
      )}

      {lightbox && leaves.length > 0 && (
        <ManuscriptLightbox
          src={imageUrl(leaves[leaf])}
          alt={`Folio ${check.folio ?? ""}`}
          label={`Folio ${check.folio ?? ""}${check.page_side ? ` · ${check.page_side}` : ""}`}
          toolbarExtra={nav}
          onClose={() => setLightbox(false)}
        />
      )}
    </div>
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
  onOpenIdentity,
  onCorrectName,
  reviewer,
  onReviewerChange,
  reason,
  onReasonChange,
  onSetHidden,
  actionBusy,
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
  onOpenIdentity: (caseId: string) => void;
  onCorrectName?: () => void;
  reviewer: string;
  onReviewerChange: (value: string) => void;
  reason: string;
  onReasonChange: (value: string) => void;
  onSetHidden: (hidden: boolean) => void;
  actionBusy: boolean;
  actionError: string;
  actionMessage: string;
}) {
  const [manuscriptPath, setManuscriptPath] = useState<string | null>(null);
  const [editingColumn, setEditingColumn] = useState<string | null>(null);
  const [addingInvestor, setAddingInvestor] = useState(false);
  // "Attach a person" on a stake nobody holds opens the add panel preset to
  // that stake; the key remounts the panel so the preset takes effect.
  const [attachInvestmentId, setAttachInvestmentId] = useState<string | null>(null);
  const [investorMessage, setInvestorMessage] = useState("");
  const historyEntries = record.history?.entries ?? [];
  const lastChange = historyEntries.find((entry) => entry.kind !== "dismissed") ?? null;
  // A DB-native row was created on the platform after the Word-corpus freeze:
  // its provenance is its own create op's source line, not a Word summary
  // (never a partner or stake created on it).
  const created = record.history?.created ?? null;
  const hasSubSection = record.sections.some((s) => s.title.startsWith("Sub-contracts"));
  const manuscriptImages = record.manuscript_images ?? [];
  const documentEditable = !record.is_deleted && (record.table === "contract" || record.table === "sub_contract");
  const documentCorrection = record.document_correction ?? null;
  const deps = record.dependents;
  const counted = (n: number | undefined, word: string) => (n ? `${n} ${word}${n === 1 ? "" : "s"}` : "");
  const depParts = deps
    ? [
        counted(deps.sub_contract, "sub-contract"),
        counted(deps.investor, "partner"),
        counted(deps.investment, "stake"),
        counted(deps.contract_place, "place"),
      ].filter(Boolean)
    : [];
  const depList =
    depParts.length > 1 ? `${depParts.slice(0, -1).join(", ")} and ${depParts[depParts.length - 1]}` : depParts[0] ?? "";

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
    } else if (autoFixField === "review_partners") {
      // a worklist item about the partners (duplicate, no gp, stake nobody holds):
      // bring the block into view rather than opening any one editor
      window.setTimeout(
        () => document.getElementById("rec-partners")?.scrollIntoView({ behavior: "smooth", block: "start" }),
        50,
      );
    } else if (!autoFixInv) {
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
  // One field editor at a time: ask before an open one with typed text is replaced.
  const openFieldEditor = (column: string) => {
    if (editingColumn && editingColumn !== column && !confirmDiscard("record-field")) return;
    setEditingColumn(column);
  };
  // Saving a partner/place/lookup cell reloads the record WITHOUT closing an
  // unrelated scalar editor that may hold typed text (finding D2); only the
  // scalar editor's own save (closeAndRefresh) closes it.
  const refreshRecordOnly = () => onRefresh();

  // In-page jump chips for long contract records (partners, places, acts,
  // narrative). Person pages are short enough to scan; chips there only
  // repeated the section titles. Buttons scroll rather than #hash anchors so
  // the router URL — which carries ?review/?fix state — is never touched.
  const relatedSection = record.sections[0] ?? null;
  const jumpChips: { id: string; label: string }[] = [
    { id: "rec-details", label: "Details" },
    ...(record.table === "contract" && record.partners
      ? [{ id: "rec-partners", label: `Partners (${record.partners.count})` }]
      : []),
    ...(record.table === "contract" && record.places
      ? [{ id: "rec-places", label: `Places (${record.places.count})` }]
      : []),
    ...(record.table === "contract" && (hasSubSection || !record.is_deleted)
      ? [{ id: "rec-related", label: `Sub-contracts (${relatedSection ? relatedSection.rows.length : 0})` }]
      : []),
    ...(record.table !== "contract" && relatedSection
      ? [{ id: "rec-related", label: relatedSection.title }]
      : []),
    ...(record.document != null ? [{ id: "rec-narrative", label: "Narrative" }] : []),
    ...(record.table === "person" && (record.word_sources.length > 0 || record.word_sources_note)
      ? [{ id: "rec-wordctx", label: "Word & manuscript" }]
      : []),
    ...(manuscriptImages.length > 0 ? [{ id: "rec-manuscript", label: "Manuscript" }] : []),
    ...(historyEntries.length > 0 ? [{ id: "rec-history", label: `Changes (${historyEntries.length})` }] : []),
  ];

  return (
    <article className={`db-record${record.is_deleted ? " is-hidden-record" : ""}`}>
      <header className="db-record-head">
        <div className="db-record-titles">
          <p className="eyebrow db-record-sub">{record.subtitle}</p>
          <h2>
            {record.title}
            {record.is_deleted && <span className="db-hidden-badge">Hidden</span>}
          </h2>
          {historyEntries.length > 0 && (
            <p className="db-record-changed muted">
              {lastChange ? `Last changed ${historyDay(lastChange.at)} by ${lastChange.by} · ` : ""}
              <button
                type="button"
                className="link-like"
                onClick={() =>
                  document.getElementById("rec-history")?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                Change history ({historyEntries.length})
              </button>
            </p>
          )}
        </div>
        <div className="db-record-head-actions">
        {/* A clean link: never the ?review / ?fix worklist state. */}
        <CopyLinkButton path={`/database/${record.table}/${record.id}`} title="Copy a link to this record, to send or to reopen later" />
        {!record.is_deleted && (
          <details className="db-actions">
            <summary>Hide this record…</summary>
            <div className="db-actions-body">
              <div className="db-danger-body">
                <p className="muted">
                  Hiding removes the record from lists and matching. It stays in the archive and can be
                  restored.
                </p>
                {depParts.length > 0 && (
                  <p className="db-deps-warning">
                    This contract has {depList}. They stay visible and will point to a hidden contract.
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
                  <button type="button" className="pill-button is-danger" disabled={actionBusy} onClick={() => onSetHidden(true)}>
                    Hide record
                  </button>
                </div>
              </div>
              {actionError && <p className="error-text">{actionError}</p>}
            </div>
          </details>
        )}
        </div>
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
            <button type="button" className="pill-button is-active" disabled={actionBusy} onClick={() => onSetHidden(false)}>
              Restore record
            </button>
          </div>
          {actionError && <p className="error-text">{actionError}</p>}
        </div>
      )}

      {record.table !== "person" && jumpChips.length > 2 && (
        <nav className="db-jumpnav" aria-label="Jump to a section of this record">
          {jumpChips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() =>
                document.getElementById(chip.id)?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              {chip.label}
            </button>
          ))}
        </nav>
      )}

      <dl className="db-fields" id="rec-details">
        {record.fields.map((field) => (
          <div key={field.label} className="db-field">
            <dt>
              {field.label}
              {field.editable && !record.is_deleted && (
                <button
                  type="button"
                  className="field-fix"
                  onClick={() => openFieldEditor(field.column!)}
                  title={`Edit ${field.label}`}
                >
                  ✎ Edit
                </button>
              )}
            </dt>
            <dd>
              {field.relink ? (
                <LookupField
                  relink={field.relink}
                  value={field.value}
                  label={field.label}
                  disabled={Boolean(record.is_deleted)}
                  onRefresh={refreshRecordOnly}
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
            {field.word_check && !record.is_deleted && (
              <WordDateCheckPanel check={field.word_check} autoOpen={autoFixField === "word_date"} />
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
                guardScopes={["record-field"]}
              />
            )}
          </div>
        ))}
      </dl>

      {record.table === "person" && record.identity_status && !isToolHidden("people") && (
        <section className="db-block pl-identity-status" id="rec-identity">
          <div className="db-block-head">
            <h3>Reviewed identity</h3>
            {record.identity_status.split_flags && record.identity_status.split_flags.length > 0 ? (
              <span className="status-chip status-review">Possibly two people</span>
            ) : record.identity_status.person_ids.length > 1 ? (
              <span className="status-chip status-strong">
                {record.identity_status.person_ids.length} entries linked
              </span>
            ) : null}
          </div>
          {(record.identity_status.split_flags ?? []).map((flag) => (
            <SplitFlagBlock
              key={flag.event_id || flag.case_id}
              flag={flag}
              reviewer={reviewer}
              onReviewerChange={onReviewerChange}
              onOpenIdentity={onOpenIdentity}
              onLifted={onRefresh}
            />
          ))}
          {record.identity_status.person_ids.length > 1 ? (
            <p>
              This entry belongs to a reviewed identity family with entries{" "}
              {record.identity_status.person_ids.map((id, index) => (
                <Fragment key={id}>
                  {index > 0 ? ", " : null}
                  {String(id) === record.id ? (
                    <>#{id}</>
                  ) : (
                    <button
                      type="button"
                      className="pl-identity-id"
                      onClick={() => onOpen("person", String(id))}
                    >
                      #{id}
                    </button>
                  )}
                </Fragment>
              ))}
              . The entries remain as entered.
            </p>
          ) : (
            <p className="muted">No reviewer has linked this entry to another entry.</p>
          )}
          {record.identity_status.open_count > 0 && (
            <button
              type="button"
              className="pill-button is-primary"
              onClick={() => onOpenIdentity(record.identity_status!.open_cases[0])}
            >
              Review {record.identity_status.open_count} possible related{" "}
              {record.identity_status.open_count === 1 ? "entry" : "entries"} →
            </button>
          )}
        </section>
      )}

      {record.table === "contract" && (
        <div id="rec-partners">
          <PartnersBlock
            partners={record.partners}
            contractId={record.id}
            hidden={Boolean(record.is_deleted)}
            autoExpandInvestor={autoFixInv || null}
            onOpen={onOpen}
            onCorrectName={onCorrectName}
            onAddInvestor={() => {
              if (addingInvestor && !confirmDiscard("add-partner")) return;
              setAttachInvestmentId(null);
              setAddingInvestor((v) => !v);
            }}
            onAttachTo={(investmentId) => {
              if (addingInvestor && attachInvestmentId !== investmentId && !confirmDiscard("add-partner")) return;
              setAttachInvestmentId(investmentId);
              setInvestorMessage("");
              setAddingInvestor(true);
            }}
            onRefresh={refreshRecordOnly}
          />
        </div>
      )}

      {investorMessage && <div className="notice success">{investorMessage}</div>}
      {addingInvestor && record.table === "contract" && !record.is_deleted && (
        <AddInvestorPanel
          key={attachInvestmentId ?? "own"}
          contractId={record.id}
          contractTitle={record.title}
          presetInvestmentId={attachInvestmentId}
          onSaved={(message) => {
            setInvestorMessage(message);
            setAttachInvestmentId(null);
            onRefresh();
          }}
          onClose={() => {
            setAddingInvestor(false);
            setAttachInvestmentId(null);
            setInvestorMessage("");
          }}
        />
      )}

      {record.table === "contract" && (
        <div id="rec-places">
          <PlacesBlock
            places={record.places}
            contractId={record.id}
            hidden={Boolean(record.is_deleted)}
            onRefresh={refreshRecordOnly}
          />
        </div>
      )}

      {/* A person with zero live appearances gets no "Appears in contracts"
          section from the server, so its absence IS the zero-appearance
          signal. Explain the row instead of leaving a silent gap — these rows
          usually come from superseded data entry, and the identity hint above
          (when present) is the only pointer onward; no auto-navigation. */}
      {record.table === "person" &&
        !record.sections.some((section) => section.title.startsWith("Appears in contracts")) && (
        <section className="db-block" id="rec-related">
          <div className="db-block-head">
            <h3>Appears in contracts (0)</h3>
          </div>
          <p className="muted">
            This entry is not attached to any contract as a partner. Entries like this usually
            come from superseded or abandoned data entry, or record a person named only in the
            narrative of a contract or sub-contract.
          </p>
        </section>
      )}

      {/* Each table serves at most one related-records section (sub-contracts /
          parent contract / appears-in), and the empty-subs block below never
          renders alongside it — so a single jump-anchor id is unambiguous. */}
      {record.sections.map((section) => (
        <section key={section.title} className="db-block" id="rec-related">
          <div className="db-block-head">
            <h3>{section.title}</h3>
            {record.table === "contract" && section.title.startsWith("Sub-contracts") && !record.is_deleted && (
              <button
                type="button"
                className="field-fix"
                onClick={() => onOpenCreateAct(record.id)}
                title="Add a sub-contract (disdetta, bilancio, …) to this contract"
              >
                + Add sub-contract
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
                    key={`${section.title}-${row.key ?? row.id ?? rowIndex}`}
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

      {record.table === "contract" && !hasSubSection && !record.is_deleted && (
        <section className="db-block" id="rec-related">
          <div className="db-block-head">
            <h3>Sub-contracts (0)</h3>
            <button
              type="button"
              className="field-fix"
              onClick={() => onOpenCreateAct(record.id)}
              title="Add a sub-contract (disdetta, bilancio, …) to this contract"
            >
              + Add sub-contract
            </button>
          </div>
          <p className="muted">No sub-contracts are recorded on this contract yet.</p>
        </section>
      )}

      {/* ── Sources & evidence ─────────────────────────────────────────────
          The record's structured claims (fields, partners, places, acts) read
          first; the texts and images that ground them follow below this line. */}
      {(record.document != null ||
        manuscriptImages.length > 0 ||
        (record.table === "person" && (record.word_sources.length > 0 || record.word_sources_note))) && (
        <h2 className="db-evidence-divider" id="rec-evidence">
          Sources &amp; evidence
        </h2>
      )}

      {record.document != null && (
        <section className="db-block db-narrative-block" id="rec-narrative">
          <div className="db-block-head">
            <h3>Narrative</h3>
            <span className="db-block-sub muted">the database’s own text</span>
            {documentEditable && editingColumn !== "document" && (
              <button
                type="button"
                className="field-fix"
                onClick={() => openFieldEditor("document")}
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
              guardScopes={["record-field"]}
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
          {record.table !== "person" && record.word_sources.length === 0 && created && (
            <p className="db-native-provenance muted">
              Added directly to the database by <strong>{created.by}</strong> · {historyDay(created.at)}
              {created.source ? <> · source: {created.source}</> : null} — no Word summary exists for
              this record (the Word corpus is frozen); this narrative is its primary text.
            </p>
          )}
        </section>
      )}

      {record.table === "person" && (record.word_sources.length > 0 || record.word_sources_note) && (
        <section className="db-block" id="rec-wordctx">
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
        <section className="db-block" id="rec-manuscript">
          <div className="db-block-head">
            <h3>Manuscript page</h3>
            <span className="db-block-sub muted">
              located by register and folio; the mapping to leaves is not yet verified, so the opening
              shown may be off by one
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

      {historyEntries.length > 0 && (
        <RecordHistory key={record.row_id} entries={historyEntries} table={record.table} />
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
