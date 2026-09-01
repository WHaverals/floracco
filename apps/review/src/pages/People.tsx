import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  decidePersonLinkage,
  loadPersonLinkageCase,
  loadPersonLinkageCases,
  loadPersonLinkageSummary,
  revokePersonDecision,
} from "../api";
import CareerSpanRibbon from "../components/person-linkage/CareerSpanRibbon";
import ModelWaterfall from "../components/person-linkage/ModelWaterfall";
import WordSourceDrawer from "../components/WordSourceDrawer";
import { useEscapeLayer } from "../utils/escapeLayers";
import { useLatest } from "../utils/latest";
import type {
  PersonLinkageCase,
  PersonLinkageLane,
  PersonLinkagePerson,
  PersonLinkagePreview,
  PersonLinkageSummary,
} from "../types";

const REVIEWER_KEY = "floracco_reviewer";

const LANES: Array<{ id: PersonLinkageLane; label: string; note: string }> = [
  { id: "labeling_round", label: "Labeling round", note: "Frozen, randomized cases with model output hidden" },
  { id: "likely_duplicates", label: "Likely duplicates", note: "Transparent data-entry patterns to confirm" },
  { id: "high_concordance", label: "High-concordance variants", note: "Name variation with aligned lineage, time, or business context" },
  { id: "read_source", label: "Read the source", note: "The structured fields cannot decide" },
  { id: "other_matches", label: "Other possible matches", note: "Ranked evidence; the human decides" },
  { id: "possible_splits", label: "Possible combined identities", note: "One entered record may contain several people" },
  { id: "decided", label: "Decided", note: "Reviewed links, refusals, and deferrals" },
  { id: "rule_exclusions", label: "Rule-based exclusions", note: "Impossible careers and explicit generations" },
];

const FIELD_LABELS: Array<[keyof PersonLinkagePerson["fields"], string]> = [
  ["first_name", "Given name"],
  ["father_mother", "Father / mother"],
  ["grandfather", "Grandfather"],
  ["last_name", "Family name"],
  ["nickname", "Nickname"],
  ["is_woman", "Recorded as woman"],
];

function displayField(key: keyof PersonLinkagePerson["fields"], value: string | number | null): string {
  if (value === null || value === "") return "Not recorded";
  if (key === "is_woman") return Number(value) ? "Yes" : "No";
  return String(value);
}

function fieldStatus(persons: PersonLinkagePerson[], key: keyof PersonLinkagePerson["fields"]): string {
  const values = persons.map((person) => person.fields[key]);
  const recorded = values.filter((value) => value !== null && value !== "");
  if (recorded.length === 0) return "not-recorded";
  if (recorded.length !== values.length) return "missing";
  return new Set(recorded.map(String)).size === 1 ? "same" : "different";
}

function fieldStatusLabel(status: string): string {
  if (status === "same") return "same recorded value";
  if (status === "different") return "different";
  if (status === "missing") return "missing on some records";
  return "not recorded";
}

function laneQuestion(detail: PersonLinkageCase): string {
  // The labeling round asks the same neutral question for every case:
  // tier-specific phrasing would leak which stratum a blind case came from.
  if (detail.lane === "labeling_round") return "Do these database records describe the same historical person?";
  if (detail.kind === "split") return "Might this entered record contain more than one historical person?";
  const tiers = detail.pairs.map((pair) => pair.deterministic_tier);
  if (tiers.includes("caution_coappearance")) {
    return "These records occur in the same act — does it describe one person entered twice, or different people?";
  }
  if (tiers.includes("caution_gf_conflict")) {
    return "The recorded grandfathers differ — do the sources still describe one person?";
  }
  if (tiers.includes("caution_posthumous_conflict")) {
    return "One record’s heirs appear before the other record’s later living activity — what do the acts show?";
  }
  return "Do these database records describe the same historical person?";
}

function PersonMatrix({
  persons,
  pair,
  splitFlaggedPersonIds,
}: {
  persons: PersonLinkagePerson[];
  pair: PersonLinkageCase["pairs"][number] | null;
  splitFlaggedPersonIds: number[];
}) {
  const nameReview = (
    pair?.evidence_json as {
      name_review?: Record<string, {
        left: string | null;
        right: string | null;
        exact: boolean;
        edit_distance: number | null;
      }>;
    } | undefined
  )?.name_review;
  const nameExceptions = nameReview
    ? Object.entries(nameReview).filter(([, evidence]) => !evidence.exact)
    : [];
  return (
    <section className="pl-records" aria-labelledby="pl-name-heading">
      <div className="pl-section-head">
        <div>
          <p className="eyebrow">Entered records</p>
          <h2 id="pl-name-heading">Compare the records</h2>
        </div>
      </div>
      <div className="pl-matrix-scroll">
        <table className="pl-name-matrix">
          <thead>
            <tr>
              <th>Field</th>
              {persons.map((person) => (
                <th key={person.person_id}>
                  <span className="pl-record-id">Person #{person.person_id}</span>
                  <strong className="pl-record-name">{person.display_name}</strong>
                  <span className="pl-record-meta">
                    {person.first_year == null ? "No dated living appearance" : `${person.first_year}–${person.last_year}`}
                    {" · "}{person.n_appearances} appearance{person.n_appearances === 1 ? "" : "s"}
                  </span>
                  <span className="pl-record-role">
                    {person.role_profile.gp} GP · {person.role_profile.lp} LP
                  </span>
                  <span className="pl-record-actions">
                    <Link className="pl-record-button" to={`/database/person/${person.person_id}`}>
                      View database record
                    </Link>
                    {splitFlaggedPersonIds.includes(person.person_id) ? (
                      <span className="pl-split-status">Flagged for split review</span>
                    ) : null}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {FIELD_LABELS.map(([key, label]) => {
              const status = fieldStatus(persons, key);
              return (
                <tr className={`is-${status}`} key={key}>
                  <th>
                    {label}
                    {status === "different" || status === "missing" ? (
                      <span className="pl-row-signal">{fieldStatusLabel(status)}</span>
                    ) : null}
                  </th>
                  {persons.map((person) => (
                    <td className={person.fields[key] == null || person.fields[key] === "" ? "is-missing" : ""} key={person.person_id}>
                      {displayField(key, person.fields[key])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {nameExceptions.length ? (
        <div className="pl-spelling-evidence" aria-label="Recorded-name comparison">
          {nameExceptions.map(([field, evidence]) => (
            <span className={evidence.exact ? "is-exact" : "is-variant"} key={field}>
              <strong>{field.replace("_", " ")}</strong>
              {evidence.exact
                ? "exact"
                : evidence.edit_distance == null
                  ? "not recorded on both"
                  : `${evidence.edit_distance} edit${evidence.edit_distance === 1 ? "" : "s"}`}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function GroupPairMatrix({
  detail,
  scopeKey,
  onSelectScope,
}: {
  detail: PersonLinkageCase;
  scopeKey: string;
  onSelectScope: (key: string) => void;
}) {
  if (detail.persons.length < 3 || !detail.group) return null;
  const matrix = (
    detail.group as {
      pair_matrix_json?: Array<{
        person_id_l: number;
        person_id_r: number;
        is_projection_edge: boolean;
        deterministic_tier: string | null;
      }>;
    }
  ).pair_matrix_json ?? [];
  if (!matrix.length) return null;
  const names = new Map(
    detail.persons.map((person) => [person.person_id, person.display_name]),
  );
  return (
    <section className="pl-section" aria-labelledby="pl-pair-matrix-heading">
      <p className="eyebrow">Whole-group check</p>
      <h2 id="pl-pair-matrix-heading">How the records are connected</h2>
      <p className="muted">
        A group can be held together by only some pair suggestions. Choose whether the next decision covers every
        record, or only one pair.
      </p>
      <ul className="pl-pair-matrix">
        <li>
          <button
            className={scopeKey === "all" ? "pl-scope is-active" : "pl-scope"}
            onClick={() => onSelectScope("all")}
            type="button"
          >
            <span>All {detail.persons.length} records</span>
            <strong>Decision covers the whole group</strong>
          </button>
        </li>
        {matrix.map((row) => {
          const key = `${row.person_id_l}:${row.person_id_r}`;
          return (
            <li key={key}>
              <button
                className={scopeKey === key ? "pl-scope is-active" : "pl-scope"}
                onClick={() => onSelectScope(key)}
                type="button"
              >
                <span>
                  #{row.person_id_l} {names.get(row.person_id_l)}
                  {" ↔ "}
                  #{row.person_id_r} {names.get(row.person_id_r)}
                </span>
                <strong>
                  {row.deterministic_tier === "distinct_strong"
                    ? "Rule says different"
                    : row.is_projection_edge
                      ? "Suggested connection"
                      : "No direct connection"}
                </strong>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SharedOverlapPanel({ detail }: { detail: PersonLinkageCase }) {
  const context = detail.business_context;
  if (!context) return null;
  const personById = new Map(
    detail.persons.map((person) => [person.person_id, person]),
  );

  const normalizeToken = (value: string) =>
    value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9']/g, "");

  const highlightedFirm = (firm: string, words: string[]) => (
    <>
      {firm.split(/(\s+)/).map((token, index) => (
        words.includes(normalizeToken(token))
          ? <mark key={`${token}-${index}`}>{token}</mark>
          : token
      ))}
    </>
  );

  return (
    <section className="pl-section" aria-labelledby="pl-overlap-heading">
      <p className="eyebrow">Business context</p>
      <h2 id="pl-overlap-heading">Shared connections</h2>
      <p className="pl-context-intro">
        These records occur in some of the same business settings. That is context—not proof that they describe one
        person.
      </p>

      <section className="pl-shared-section">
        <h3>Same full firm name</h3>
        {context.exact_firms.length ? context.exact_firms.map((firm) => (
          <article className="pl-shared-firm" key={firm.normalized_name}>
            <strong>{firm.display_name}</strong>
            <span className="pl-context-strength">Exact normalized firm name</span>
            <div className="pl-connection-sides">
              {detail.persons.map((person) => (
                <div key={person.person_id}>
                  <h4>#{person.person_id} · {person.display_name}</h4>
                  {(firm.appearances_by_person[String(person.person_id)] ?? []).map((appearance) => (
                    <Link
                      key={`${person.person_id}-${appearance.contract_id}`}
                      to={`/database/contract/${appearance.contract_id}`}
                    >
                      {appearance.registration_date || "Undated"} · contract {appearance.contract_id}
                    </Link>
                  ))}
                  {(firm.appearances_by_person[String(person.person_id)] ?? []).length === 0 ? (
                    <span className="pl-connection-empty">No appearance in this firm</span>
                  ) : null}
                </div>
              ))}
            </div>
          </article>
        )) : <p className="pl-context-empty">No exact shared firm name is recorded.</p>}
      </section>

      <section className="pl-shared-section">
        <h3>Shared business contacts</h3>
        {context.shared_partners.length ? (
          <div className="pl-shared-partners">
            {context.shared_partners.map((partner) => (
              <article key={partner.person_id}>
                <header>
                  <Link to={`/database/person/${partner.person_id}`}>
                    {partner.display_name} · person #{partner.person_id}
                  </Link>
                  <span>Shared contact</span>
                </header>
                <div className="pl-connection-sides">
                  {detail.persons.map((person) => (
                    <div key={person.person_id}>
                      <h4>With #{person.person_id} · {person.display_name}</h4>
                      {(partner.connections_by_person[String(person.person_id)] ?? []).map((connection) => (
                        <Link
                          key={`${person.person_id}-${connection.contract_id}`}
                          to={`/database/contract/${connection.contract_id}`}
                        >
                          {connection.registration_date || "Undated"} · contract {connection.contract_id}
                          {connection.firm_name ? ` · ${connection.firm_name}` : ""}
                        </Link>
                      ))}
                      {(partner.connections_by_person[String(person.person_id)] ?? []).length === 0 ? (
                        <span className="pl-connection-empty">No direct co-appearance recorded</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        ) : <p className="pl-context-empty">No shared entered business contact is recorded.</p>}
      </section>

      {!context.exact_firms.length && context.shared_firm_words.length ? (
        <section className="pl-shared-section">
          <h3>Partial firm-name overlap</h3>
          <p className="pl-context-explanation">
            Only the highlighted words recur. These are not necessarily the same firm.
          </p>
          <div className="pl-connection-sides">
            {context.firm_word_evidence.map((side) => {
              const person = personById.get(side.person_id);
              return (
                <div key={side.person_id}>
                  <h4>#{side.person_id} · {person?.display_name ?? `Person ${side.person_id}`}</h4>
                  {side.firms.map((firm) => (
                    <Link key={`${side.person_id}-${firm.contract_id}`} to={`/database/contract/${firm.contract_id}`}>
                      <span>{highlightedFirm(firm.firm_name, firm.matched_words)}</span>
                      <small>{firm.registration_date || "Undated"} · contract {firm.contract_id}</small>
                    </Link>
                  ))}
                </div>
              );
            })}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function ContextProfiles({ persons }: { persons: PersonLinkagePerson[] }) {
  const fields: Array<[keyof PersonLinkagePerson["context_profile"], string]> = [
    ["professions", "Profession"],
    ["residences", "Residence"],
    ["economic_activities", "Economic activity"],
    ["titles", "Titles"],
    ["father_mother_titles", "Father / mother title"],
    ["grandfather_titles", "Grandfather title"],
    ["husband_titles", "Husband title"],
    ["origins", "Place of origin"],
  ];
  return (
    <section className="pl-context-profiles">
      <div className="pl-matrix-scroll">
        <table className="pl-name-matrix">
          <thead>
            <tr>
              <th>Context</th>
              {persons.map((person) => <th key={person.person_id}>Person #{person.person_id}</th>)}
            </tr>
          </thead>
          <tbody>
            {fields.map(([field, label]) => (
              <tr key={field}>
                <th>{label}</th>
                {persons.map((person) => (
                  <td key={person.person_id}>
                    {person.context_profile[field].length
                      ? person.context_profile[field].map((value, index) => (
                        <span
                          className={/\bquondam\b/i.test(value) ? "pl-quondam-value" : undefined}
                          key={value}
                        >
                          {index ? " · " : ""}{value}
                        </span>
                      ))
                      : <span className="muted">Not recorded</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="pl-quondam-guard">
        A ‘quondam’ title dates the named relative’s death — the father’s or husband’s, not this
        person’s — and its absence means nothing, since scribes often omitted it.
      </p>
    </section>
  );
}

function AppearanceTables({ persons }: { persons: PersonLinkagePerson[] }) {
  return (
    <section className="pl-section" aria-labelledby="pl-appearance-heading">
      <p className="eyebrow">Database context</p>
      <h2 id="pl-appearance-heading">Contract appearances</h2>
      <div className="pl-appearance-grid">
        {persons.map((person) => (
          <div className="pl-appearance-card" key={person.person_id}>
            <h3>#{person.person_id} · {person.display_name}</h3>
            <p className="muted">{person.n_appearances} entered appearance{person.n_appearances === 1 ? "" : "s"}</p>
            {person.appearances.length ? (
              <div className="pl-table-scroll">
                <table className="pl-contract-table">
                  <thead><tr><th>Date</th><th>Firm</th><th>Role</th></tr></thead>
                  <tbody>
                    {person.appearances.map((appearance) => (
                      <tr key={appearance.investor_id}>
                        <td><Link to={`/database/contract/${appearance.contract_id}`}>{appearance.registration_date || "—"}</Link></td>
                        <td>{appearance.firm_name || "—"}</td>
                        <td>{appearance.roles || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="muted">This entered record is not attached to an investor appearance.</p>}
          </div>
        ))}
      </div>
    </section>
  );
}

type EvidenceDrawerKind = "contracts" | "business" | "sources" | "history";
type PendingDecision = "same_as" | "distinct" | "defer" | "flag_split" | "bulk_rule_ack";
type PersonCitation = {
  source_entry_id: string;
  source_entry_key: string | null;
  register_id: string | null;
  date: string | null;
  folio: string | null;
  label: string | null;
  source_quote: string;
};

function EvidenceDrawer({
  kind,
  detail,
  relatedSources,
  reviewer,
  busy,
  onClose,
  onOpenSource,
  onUndo,
  onReopen,
}: {
  kind: EvidenceDrawerKind;
  detail: PersonLinkageCase;
  relatedSources: Array<Record<string, string | number>>;
  reviewer: string;
  busy: boolean;
  onClose: () => void;
  onOpenSource: (sourceId: string) => void;
  onUndo: (eventId: string) => void;
  onReopen: () => void;
}) {
  useEscapeLayer(true, onClose);
  const titles: Record<EvidenceDrawerKind, string> = {
    contracts: "Contract appearances",
    business: "Business context",
    sources: "Source records",
    history: "Review history",
  };
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside
        className="word-drawer pl-evidence-drawer"
        aria-label={titles[kind]}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="word-drawer-head">
          <div>
            <p className="eyebrow">Identity evidence</p>
            <h3>{titles[kind]}</h3>
          </div>
          <button className="drawer-close" onClick={onClose} type="button" aria-label="Close evidence drawer">×</button>
        </header>
        <div className="pl-evidence-drawer-body">
          {kind === "contracts" ? <AppearanceTables persons={detail.persons} /> : null}
          {kind === "business" ? (
            <>
              <SharedOverlapPanel detail={detail} />
              <details className="pl-context-details" open>
                <summary>
                  <span className="pl-context-details-label">
                    <span>Other recorded context</span>
                    <small>Profession, residence, activity, title and origin · not scored</small>
                  </span>
                  <span className="pl-context-details-chevron" aria-hidden="true">▾</span>
                </summary>
                <ContextProfiles persons={detail.persons} />
              </details>
            </>
          ) : null}
          {kind === "sources" ? (
            <>
              {detail.shared_contracts.map((contract) => (
                <article className="pl-source-card" key={contract.contract_id}>
                  <h3>
                    <Link to={`/database/contract/${contract.contract_id}`}>Contract {contract.contract_id}</Link>
                    {" · "}{contract.registration_date || "undated"}
                  </h3>
                  <p>{contract.document || "No database narrative is recorded."}</p>
                  <div className="pl-source-links">
                    {contract.word_sources.map((source) => (
                      <button
                        type="button"
                        className="pill-button"
                        key={source.source_entry_id}
                        onClick={() => onOpenSource(source.source_entry_id)}
                      >
                        Read Word summary
                      </button>
                    ))}
                  </div>
                </article>
              ))}
              {relatedSources.length ? (
                <section className="pl-drawer-source-list">
                  <p className="muted">
                    Word summaries from contracts where one of these records appears. Context, not a direct
                    person-to-document link.
                  </p>
                  {detail.persons.map((person) => {
                    const contracts = new Map(
                      person.appearances.map((appearance) => [appearance.contract_id, appearance]),
                    );
                    const sources = relatedSources.filter((source) =>
                      contracts.has(Number(source.via_contract_id))
                    );
                    if (!sources.length) return null;
                    return (
                      <div className="pl-source-person" key={person.person_id}>
                        <h4>#{person.person_id} · {person.display_name}</h4>
                        {sources.map((source) => {
                          const appearance = contracts.get(Number(source.via_contract_id));
                          return (
                            <button
                              type="button"
                              key={`${person.person_id}-${String(source.source_entry_id)}`}
                              onClick={() => onOpenSource(String(source.source_entry_id))}
                            >
                              <strong>
                                {String(source.register_id ?? "Word summary")}
                                {source.entry_registration_date_raw ? ` · ${source.entry_registration_date_raw}` : ""}
                              </strong>
                              <span>
                                Contract {source.via_contract_id}
                                {appearance?.firm_name ? ` · ${appearance.firm_name}` : ""}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </section>
              ) : null}
              {!detail.shared_contracts.length && !relatedSources.length ? (
                <p className="muted">No linked Word summaries are available for this case.</p>
              ) : null}
            </>
          ) : null}
          {kind === "history" ? (
            <>
              <ul className="pl-history">
                {detail.history.map((event) => {
                  const activeIds = new Set((detail.active_links ?? []).map((link) => link.link_id));
                  const decisionIsActive = ["same_as", "distinct"].includes(event.action)
                    && event.link_ids.some((linkId) => activeIds.has(linkId));
                  return (
                  <li key={event.event_id}>
                    <strong>{event.action.replace("_", " ")}</strong>
                    <span>
                      {event.person_ids.map((personId) => `#${personId}`).join(" · ")}
                      {" · "}{event.created_by} · {event.created_at.slice(0, 16).replace("T", " ")}
                    </span>
                    {event.rationale ? <q>{event.rationale}</q> : null}
                    {event.evidence_snapshot?.citations?.length ? (
                      <span>{event.evidence_snapshot.citations.length} attached source citation{
                        event.evidence_snapshot.citations.length === 1 ? "" : "s"
                      }</span>
                    ) : null}
                    {decisionIsActive ? (
                      <button
                        className="pl-record-button"
                        disabled={busy || !reviewer.trim()}
                        onClick={() => onUndo(event.event_id)}
                        type="button"
                      >
                        Undo entire decision
                      </button>
                    ) : null}
                  </li>
                  );
                })}
              </ul>
              {detail.history.length && ["defer", "flag_split", "revoke"].includes(detail.history[0].action) ? (
                <button
                  type="button"
                  className="pl-record-button"
                  disabled={busy || !reviewer.trim()}
                  onClick={onReopen}
                >
                  Reopen this case
                </button>
              ) : null}
            </>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

export default function People() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const [lane, setLane] = useState<PersonLinkageLane>("likely_duplicates");
  const [summary, setSummary] = useState<PersonLinkageSummary | null>(null);
  const [cases, setCases] = useState<PersonLinkagePreview[]>([]);
  const [total, setTotal] = useState(0);
  const [reservedForLabeling, setReservedForLabeling] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(caseId ?? "");
  const [detail, setDetail] = useState<PersonLinkageCase | null>(null);
  const [query, setQuery] = useState("");
  const [priorityBand, setPriorityBand] = useState("All");
  // Stranded-entry facet (other_matches only): pairs where exactly one side
  // never appears in a contract. Ghost-tier groups are NOT here — the batch
  // rules already route them to Likely duplicates, and the help copy says so.
  const [stranded, setStranded] = useState(false);
  const [strandedAll, setStrandedAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem(REVIEWER_KEY) ?? "");
  const [rationale, setRationale] = useState("");
  const [scopeKey, setScopeKey] = useState("");
  const [pendingDecision, setPendingDecision] = useState<PendingDecision | null>(null);
  const [splitPersonId, setSplitPersonId] = useState<number | null>(null);
  const [evidenceDrawer, setEvidenceDrawer] = useState<EvidenceDrawerKind | null>(null);
  const [sourceEntryId, setSourceEntryId] = useState<string | null>(null);
  const [selectedCitations, setSelectedCitations] = useState<PersonCitation[]>([]);
  const [mobileQueueOpen, setMobileQueueOpen] = useState(false);
  const [lastDecisionEventId, setLastDecisionEventId] = useState<string | null>(null);
  const beginDetailLoad = useLatest();
  const reviewerLocked = reviewer.includes("@");

  const changeLane = (nextLane: PersonLinkageLane) => {
    beginDetailLoad(); // invalidate any in-flight case from the previous lane
    setLane(nextLane);
    setPriorityBand(nextLane === "other_matches" ? "priority_1" : "All");
    setStranded(false);
    setStrandedAll(false);
    setOffset(0);
    setSelected("");
    setDetail(null);
    setEvidenceDrawer(null);
    setMobileQueueOpen(false);
    setMessage("");
    setLastDecisionEventId(null);
    navigate("/people");
  };

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const pageSize = lane === "rule_exclusions" ? 25 : 100;
      const [nextSummary, nextCases] = await Promise.all([
        loadPersonLinkageSummary(),
        loadPersonLinkageCases({
          lane,
          status: lane === "decided" ? "All" : "open",
          priorityBand,
          q: query,
          offset,
          limit: pageSize,
          stranded: lane === "other_matches" && stranded,
          strandedScope: lane === "other_matches" && stranded && strandedAll ? "all" : undefined,
        }),
      ]);
      setSummary(nextSummary);
      setCases(nextCases.cases);
      setTotal(nextCases.total);
      setReservedForLabeling(nextCases.reserved_for_labeling ?? 0);
      setError("");
      if (!selected || (!caseId && !nextCases.cases.some((item) => item.case_id === selected))) {
        const first = nextCases.cases[0]?.case_id ?? "";
        setSelected(first);
        if (first) navigate(`/people/${encodeURIComponent(first)}`, { replace: true });
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId, lane, navigate, offset, priorityBand, query, selected, stranded, strandedAll]);

  useEffect(() => { void reload(); }, [lane, priorityBand, offset, stranded, strandedAll]); // query submits explicitly
  useEffect(() => {
    if (caseId) setSelected(caseId);
  }, [caseId]);
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    const fresh = beginDetailLoad();
    setDetail(null);
    loadPersonLinkageCase(selected)
      .then((value) => {
        if (!fresh()) return;
        setDetail(value);
        setError("");
        setScopeKey("");
        setPendingDecision(null);
        setSplitPersonId(null);
        setSelectedCitations([]);
        const targetLane = value.status === "open" ? value.lane : "decided";
        if (caseId && selectedIndex < 0 && targetLane !== lane) {
          setLane(targetLane);
          setPriorityBand(targetLane === "other_matches" ? "All" : priorityBand);
          // A deep link must land on the unfiltered lane — a lingering stranded
          // facet from an earlier visit could silently hide the linked case.
          setStranded(false);
          setStrandedAll(false);
        }
      })
      .catch((err: Error) => { if (fresh()) setError(err.message); });
  // selectedIndex is intentionally read only to detect a deep link outside the lane.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [beginDetailLoad, caseId, selected]);

  const selectedIndex = cases.findIndex((item) => item.case_id === selected);
  const nextCase = cases[selectedIndex + 1]?.case_id ?? cases[0]?.case_id ?? "";
  const selectCase = useCallback((id: string) => {
    setDetail(null);
    setSelected(id);
    setRationale("");
    setScopeKey("");
    setPendingDecision(null);
    setSplitPersonId(null);
    setEvidenceDrawer(null);
    setSelectedCitations([]);
    setMobileQueueOpen(false);
    navigate(`/people/${encodeURIComponent(id)}`);
  }, [navigate]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "ArrowRight" || event.key === "j") {
        if (nextCase) selectCase(nextCase);
      } else if (event.key === "ArrowLeft" || event.key === "k") {
        const previous = cases[selectedIndex - 1]?.case_id ?? cases[cases.length - 1]?.case_id ?? "";
        if (previous) selectCase(previous);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cases, nextCase, selectCase, selectedIndex]);

  const scopedIds = useMemo(() => {
    if (!detail) return [];
    if (detail.persons.length < 3) return detail.person_ids;
    if (!scopeKey) return [];
    if (scopeKey === "all") return detail.person_ids;
    const [left, right] = scopeKey.split(":").map(Number);
    if (!left || !right) return detail.person_ids;
    return [left, right];
  }, [detail, scopeKey]);

  const act = async (
    action: "same_as" | "distinct" | "defer" | "reopen" | "flag_split" | "bulk_rule_ack",
    ids = scopedIds,
  ) => {
    if (!detail || detail.case_id !== selected) return;
    if (!reviewer.trim()) {
      setError("Enter your initials before recording a review action.");
      return;
    }
    if (action === "defer" && !rationale.trim()) {
      setError("Add a short note explaining what evidence is still needed.");
      return;
    }
    setBusy(true);
    try {
      localStorage.setItem(REVIEWER_KEY, reviewer.trim());
      const secondarySplit = action === "flag_split" && detail.kind !== "split";
      const decisionCaseId = action === "flag_split" && ids.length === 1 ? `split:${ids[0]}` : detail.case_id;
      const sameCase = decisionCaseId === detail.case_id;
      const saved = await decidePersonLinkage(decisionCaseId, {
        reviewer: reviewer.trim(),
        action,
        person_ids: ids,
        reason_code: action === "defer" ? "needs_more_evidence" : "",
        rationale: rationale.trim(),
        expected_status: sameCase ? detail.status : "open",
        expected_event_id: sameCase ? detail.latest_event_id ?? null : null,
        citations: selectedCitations,
        review_mode: detail.lane === "labeling_round" ? "blind_labeling" : "standard",
      });
      setLastDecisionEventId(
        ["same_as", "distinct"].includes(action) ? saved.event_id : null,
      );
      setMessage(
        action === "same_as" ? "Linked as the same person. Both entered records remain unchanged."
          : action === "distinct" ? "Recorded as different people. No entered record changed."
            : action === "flag_split" ? (
              secondarySplit
                ? `Flagged person #${ids[0]} for split review. Please still decide this identity case.`
                : "Flagged for a future appearance-by-appearance split review."
            )
              : action === "reopen" ? "Case reopened for review."
              : action === "bulk_rule_ack"
                ? "Recorded that this rule version was sampled. Individual cases were not marked distinct."
              : "Deferred until more evidence is available.",
      );
      setRationale("");
      setPendingDecision(null);
      setSplitPersonId(null);
      setSelectedCitations([]);
      await reload();
      if (secondarySplit) {
        setDetail(await loadPersonLinkageCase(detail.case_id));
      } else if (action !== "bulk_rule_ack" && nextCase && nextCase !== detail.case_id) {
        selectCase(nextCase);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const chooseDecision = (action: PendingDecision, personId?: number) => {
    setError("");
    setMessage("");
    setRationale("");
    setPendingDecision(action);
    setSplitPersonId(personId ?? (detail?.person_ids.length === 1 ? detail.person_ids[0] : null));
  };

  const confirmDecision = () => {
    if (!pendingDecision) return;
    if (["same_as", "distinct"].includes(pendingDecision) && scopedIds.length < 2) {
      setError("Choose which records this decision covers.");
      return;
    }
    if (pendingDecision === "same_as" && lane === "rule_exclusions" && !rationale.trim()) {
      setError("Explain why the source evidence overrides the career-conflict rule.");
      return;
    }
    if (pendingDecision === "flag_split" && splitPersonId === null) {
      setError("Choose which entered record may contain several people.");
      return;
    }
    const ids = pendingDecision === "flag_split" ? [splitPersonId!] : scopedIds;
    void act(pendingDecision, ids);
  };

  const undoDecision = async (eventId: string) => {
    if (!reviewer.trim()) return;
    setBusy(true);
    try {
      await revokePersonDecision(eventId, { reviewer: reviewer.trim(), reason: rationale.trim() });
      setMessage("The entire identity decision was undone. Its audit history was kept.");
      setLastDecisionEventId(null);
      await reload();
      if (selected) setDetail(await loadPersonLinkageCase(selected));
      setSummary(await loadPersonLinkageSummary());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const primaryPair = useMemo(() => {
    if (!detail) return null;
    if (detail.persons.length < 3) return detail.pairs[0] ?? null;
    if (!scopeKey || scopeKey === "all") return null;
    const [left, right] = scopeKey.split(":").map(Number).sort((a, b) => a - b);
    return detail.pairs.find(
      (pair) => pair.person_id_l === left && pair.person_id_r === right,
    ) ?? null;
  }, [detail, scopeKey]);
  const recall = detail?.run?.recall == null ? null : Number(detail.run.recall);
  const relatedSources = useMemo(() => {
    if (!detail) return [];
    const seen = new Set<string>();
    return detail.pairs
      .flatMap((pair) => pair.source_pointers_json)
      .filter((source) => {
        const id = String(source.source_entry_id ?? "");
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      })
      .slice(0, 24);
  }, [detail]);
  const appearanceCount = detail?.persons.reduce((sum, person) => sum + person.n_appearances, 0) ?? 0;
  const scopedAppearanceCount = detail?.persons
    .filter((person) => scopedIds.includes(person.person_id))
    .reduce((sum, person) => sum + person.n_appearances, 0) ?? 0;
  const sharedContextCount = detail?.pairs.reduce(
    (sum, pair) => sum + pair.shared_firms_json.length + pair.shared_firm_words_json.length
      + pair.shared_partner_ids_json.length,
    0,
  ) ?? 0;
  const sourceCount = relatedSources.length
    + (detail?.shared_contracts.reduce((sum, contract) => sum + contract.word_sources.length, 0) ?? 0);
  const needsGroupScope = Boolean(detail && detail.persons.length >= 3 && !scopeKey);
  const scopeSummary = detail && scopedIds.length
    ? scopedIds.map((personId) => {
      const person = detail.persons.find((item) => item.person_id === personId);
      return `#${personId}${person ? ` ${person.display_name}` : ""}`;
    }).join(" · ")
    : "";
  const unavailable = summary && !summary.available;

  return (
    <div className="db-browser pl-browser">
      <aside className={mobileQueueOpen ? "db-rail pl-rail is-mobile-queue-open" : "db-rail pl-rail"}>
        <div className="db-rail-head">
          <p className="eyebrow">Person identity</p>
          <h1>Review people</h1>
          <p>Decide when separate records describe the same historical person.</p>
          <div className="pl-mobile-people-toolbar">
            <select
              aria-label="Person review lane"
              value={lane}
              onChange={(event) => changeLane(event.target.value as PersonLinkageLane)}
            >
              {LANES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label} ({summary?.lanes?.[item.id] ?? "…"})
                </option>
              ))}
            </select>
            <button
              aria-expanded={mobileQueueOpen}
              onClick={() => setMobileQueueOpen((open) => !open)}
              type="button"
            >
              {mobileQueueOpen ? "Close queue" : `Queue (${total})`}
            </button>
          </div>
          <div className="pl-lanes">
            {LANES.map((item) => (
              <button
                className={lane === item.id ? "pl-lane is-active" : "pl-lane"}
                key={item.id}
                onClick={() => changeLane(item.id)}
                type="button"
                aria-pressed={lane === item.id}
              >
                <span>{item.label}</span>
                <strong>{summary?.lanes?.[item.id] ?? 0}</strong>
                <small>{item.note}</small>
              </button>
            ))}
          </div>
          <form
            className="pl-filter"
            onSubmit={(event) => {
              event.preventDefault();
              if (offset) setOffset(0);
              else void reload();
            }}
          >
            <input
              aria-label="Search person identity cases"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search names or ids…"
            />
            {lane === "other_matches" ? (
              <select value={priorityBand} onChange={(event) => { setOffset(0); setPriorityBand(event.target.value); }}>
                <option value="All">All review priorities</option>
                <option value="priority_1">Priority 1 · top 2%</option>
                <option value="priority_2">Priority 2 · next 8%</option>
                <option value="priority_3">Priority 3 · next 20%</option>
                <option value="priority_4">Priority 4 · remaining candidates</option>
              </select>
            ) : null}
            {lane === "other_matches" ? (
              <div className="pl-stranded">
                <button
                  type="button"
                  className={stranded ? "pl-stranded-chip is-active" : "pl-stranded-chip"}
                  aria-pressed={stranded}
                  onClick={() => {
                    setOffset(0);
                    setStranded((on) => !on);
                    // Leaving the facet also drops its widened scope, so the
                    // next activation starts back at the review-tier default.
                    if (stranded) setStrandedAll(false);
                  }}
                >
                  Stranded entries
                </button>
                <small className="pl-stranded-help">
                  One side never appears in a contract. Ghost-tier groups stay in Likely duplicates.
                </small>
                {stranded ? (
                  <label className="pl-stranded-scope">
                    <input
                      type="checkbox"
                      checked={strandedAll}
                      onChange={(event) => { setOffset(0); setStrandedAll(event.target.checked); }}
                    />
                    Include low-evidence pairs
                  </label>
                ) : null}
              </div>
            ) : null}
          </form>
          <p className="pl-queue-count">
            {loading
              ? "Loading…"
              : total
                ? `${offset + 1}–${offset + cases.length} of ${total}`
                : lane === "labeling_round" && summary?.labeling_packet_available === false
                  ? "The labeling packet is not deployed on this server."
                  : lane === "labeling_round" && summary?.labeling_packet_stale
                    ? "The labeling packet was built against an older suggestion cache — regenerate it before labeling."
                    : reservedForLabeling > 0
                      ? reservedForLabeling === 1
                        ? "The only case in this lane is part of the open labeling round. Decide it there, or finish the round to review it here."
                        : `All ${reservedForLabeling} cases in this lane are part of the open labeling round. Decide them there, or finish the round to review them here.`
                      : "No open cases"}
          </p>
          {!loading && total > 0 && reservedForLabeling > 0 ? (
            <p className="pl-queue-note">
              {reservedForLabeling === 1
                ? "1 more case in this lane is in the open labeling round."
                : `${reservedForLabeling} more cases in this lane are in the open labeling round.`}
            </p>
          ) : null}
          {lane === "rule_exclusions" && detail ? (
            <button
              className="pl-rule-sample-action"
              onClick={() => chooseDecision("bulk_rule_ack")}
              type="button"
            >
              Record inspected rule sample
            </button>
          ) : null}
        </div>
        <ul className="pl-case-list">
          {cases.map((item) => {
            const names = [...new Set(item.names.filter(Boolean))];
            const queueTag =
              lane === "labeling_round" ? "Labeling case"
                : item.lane === "likely_duplicates" ? "Possible duplicate"
                : item.lane === "high_concordance" ? "High concordance"
                : item.lane === "read_source" ? "Source needed"
                  : item.lane === "possible_splits" ? "Career needs review"
                    : item.lane === "rule_exclusions" ? "Career conflict"
                      : item.status === "open"
                        ? (item.priority_band ?? "Review candidate").replace("_", " ")
                        : item.status.replace("_", " ");
            return (
              <li key={item.case_id}>
                <button
                  className={item.case_id === selected ? "pl-case is-active" : "pl-case"}
                  onClick={() => selectCase(item.case_id)}
                  type="button"
                  aria-current={item.case_id === selected ? "true" : undefined}
                  title={lane === "labeling_round" ? undefined : item.reasons?.[0] || undefined}
                >
                  <span className="pl-case-tag">{queueTag}</span>
                  <strong>{names.join(" ↔ ") || "Unnamed records"}</strong>
                  <small>
                    {item.person_ids.map((id) => `#${id}`).join(" · ")}
                    {item.career.combined_span_years != null
                      ? ` · ${item.career.combined_span_years}-year span`
                      : ""}
                  </small>
                </button>
              </li>
            );
          })}
        </ul>
        {total > cases.length ? (
          <div className="pl-pagination">
            <button
              type="button"
              aria-label="Previous page"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - cases.length))}
            >
              ←
            </button>
            <span>{Math.floor(offset / Math.max(1, cases.length)) + 1}</span>
            <button type="button" disabled={offset + cases.length >= total} onClick={() => setOffset(offset + cases.length)}>
              <span className="pl-sr-only">Next page</span>→
            </button>
          </div>
        ) : null}
      </aside>

      <section className="workspace pl-workspace">
        {unavailable ? (
          <div className="db-detail-empty">
            <h2>Person suggestions have not been built</h2>
            <p>Build the reviewed model manifest and person cache before opening this worklist.</p>
            <code>uv run --extra linkage python workflows/person_linkage.py</code>
          </div>
        ) : !detail ? (
          <div className="db-detail-empty">
            {!loading && !total && lane === "labeling_round" && summary?.labeling_packet_available === false ? (
              <>
                <h2>Labeling round unavailable</h2>
                <p>The labeling packet is not deployed on this server.</p>
              </>
            ) : !loading && !total && lane === "labeling_round" && summary?.labeling_packet_stale ? (
              <>
                <h2>Labeling round unavailable</h2>
                <p>The labeling packet was built against an older suggestion cache — regenerate it before labeling.</p>
              </>
            ) : (
              <h2>{loading ? "Loading people…" : "Choose a person-identity case"}</h2>
            )}
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        ) : (
          <>
            <header className="case-bar pl-case-bar">
              <div>
                <p className="eyebrow">{detail.kind === "split" ? "Possible combined identity" : "Identity review"}</p>
                <p className="case-bar-question">{laneQuestion(detail)}</p>
                <p className="case-bar-context">
                  {detail.persons.length === 1
                    ? "This entered record remains unchanged."
                    : detail.persons.length === 2
                      ? "Both entered records remain unchanged."
                      : `All ${detail.persons.length} entered records remain unchanged.`}
                  {" "}Decisions are logged and reversible.
                </p>
              </div>
              <span className="pl-case-position">{selectedIndex >= 0 ? `${selectedIndex + 1} / ${cases.length}` : ""}</span>
            </header>
            <div className="pl-review-body">
              {detail.stale?.stale ? (
                <div className="notice warning" role="alert">
                  The database or saved model is newer than this suggestion cache. You may read it, but rebuild before relying on its score.
                </div>
              ) : null}
              {detail.needs_recheck ? (
                <div className="notice warning" role="alert">
                  Evidence changed after the last decision. The original decision and evidence remain in the audit
                  history; review this case again before relying on it.
                </div>
              ) : null}
              {error ? <div className="notice error" role="alert">{error}</div> : null}
              {message ? (
                <div className="notice success pl-decision-toast" role="status">
                  <span>{message}</span>
                  {lastDecisionEventId ? (
                    <button type="button" onClick={() => void undoDecision(lastDecisionEventId)}>
                      Undo last decision
                    </button>
                  ) : null}
                </div>
              ) : null}

              <PersonMatrix
                persons={detail.persons}
                pair={primaryPair}
                splitFlaggedPersonIds={detail.split_flagged_person_ids ?? []}
              />
              <GroupPairMatrix detail={detail} scopeKey={scopeKey} onSelectScope={setScopeKey} />
              <section className="pl-primary-visual" aria-labelledby="pl-career-heading">
                <div className="pl-primary-visual-head">
                  <div>
                    <p className="eyebrow">Time</p>
                    <h2 id="pl-career-heading">Dated contract appearances</h2>
                  </div>
                </div>
                <CareerSpanRibbon persons={detail.persons} />
              </section>

              {primaryPair && detail.lane !== "labeling_round" ? (
                <ModelWaterfall
                  probability={primaryPair.match_probability ?? null}
                  rows={primaryPair.waterfall_contributions_json ?? []}
                  recall={recall}
                  modelHash={String(detail.run?.model_sha256 ?? "")}
                  trainedAt={String(detail.run?.model_training_timestamp ?? "")}
                  runAt={String(detail.run?.run_timestamp ?? "")}
                  reviewRank={primaryPair.review_rank ?? null}
                  reviewPercentile={primaryPair.review_percentile ?? null}
                  priorityBand={primaryPair.priority_band ?? null}
                  networkDiagnostics={primaryPair.network_diagnostics_json ?? {}}
                  firmTokenDiagnostics={primaryPair.firm_token_diagnostics_json ?? {}}
                />
              ) : detail.lane === "labeling_round" ? (
                <section className="pl-blind-labeling-note">
                  <strong>Model output is hidden for this labeling round.</strong>
                  <span>Your decision should be based on the records and source evidence. Model ordering appears after review.</span>
                </section>
              ) : detail.persons.length >= 3 ? (
                <section className="pl-blind-labeling-note">
                  <strong>
                    {scopeKey && scopeKey !== "all"
                      ? "No direct model edge was scored for this pair."
                      : "Model contributions are pair-specific."}
                  </strong>
                  <span>
                    {scopeKey && scopeKey !== "all"
                      ? "Review the factual fields and sources; absence of a model edge is not evidence that the people differ."
                      : "Choose one pair above to inspect its model evidence. Whole-group decisions are not summarized by a single edge score."}
                  </span>
                </section>
              ) : null}

              <nav className="pl-evidence-nav" aria-label="More evidence">
                <button type="button" onClick={() => setEvidenceDrawer("contracts")}>
                  <span className="pl-evidence-icon" aria-hidden="true">▤</span>
                  <span><strong>Contracts</strong><small>{appearanceCount} appearance{appearanceCount === 1 ? "" : "s"}</small></span>
                  <span aria-hidden="true">→</span>
                </button>
                <button type="button" onClick={() => setEvidenceDrawer("business")}>
                  <span className="pl-evidence-icon" aria-hidden="true">◎</span>
                  <span>
                    <strong>Business context</strong>
                    <small>
                      {sharedContextCount
                        ? `${sharedContextCount} shared signal${sharedContextCount === 1 ? "" : "s"}`
                        : "No shared signals"}
                    </small>
                  </span>
                  <span aria-hidden="true">→</span>
                </button>
                <button
                  className={detail.lane === "read_source" ? "is-required" : undefined}
                  type="button"
                  onClick={() => setEvidenceDrawer("sources")}
                >
                  <span className="pl-evidence-icon" aria-hidden="true">¶</span>
                  <span>
                    <strong>Source records</strong>
                    <small>
                      {detail.lane === "read_source"
                        ? "Required for this case"
                        : `${sourceCount || "No"} summar${sourceCount === 1 ? "y" : "ies"}`}
                    </small>
                  </span>
                  <span aria-hidden="true">→</span>
                </button>
                {detail.history.length || detail.active_links?.length ? (
                  <button type="button" onClick={() => setEvidenceDrawer("history")}>
                    <span className="pl-evidence-icon" aria-hidden="true">↺</span>
                    <span><strong>Review history</strong><small>{detail.history.length} event{detail.history.length === 1 ? "" : "s"}</small></span>
                    <span aria-hidden="true">→</span>
                  </button>
                ) : null}
              </nav>

              <p className="pl-impact-note">
                <strong>{scopedIds.length} entered record{scopedIds.length === 1 ? "" : "s"}</strong>
                {scopedIds.length > 1 ? " → 1 reviewed identity if linked" : ""}
                {" · "}{scopedAppearanceCount} appearance{scopedAppearanceCount === 1 ? " remains" : "s remain"} unchanged
              </p>
            </div>

            <section className="pl-decision-bar" aria-label="Record identity decision">
              {pendingDecision ? (
                <div className="pl-decision-confirm">
                  <div className="pl-decision-confirm-head">
                    <div>
                      <span>Confirm decision</span>
                      <strong>
                        {pendingDecision === "same_as" ? (
                          lane === "rule_exclusions"
                            ? `Override rule: link ${scopedIds.length} records`
                            : `Link ${scopedIds.length} records as one reviewed identity`
                        ) : pendingDecision === "distinct" ? `Keep ${scopedIds.length} records different`
                          : pendingDecision === "defer" ? "Not enough evidence"
                            : pendingDecision === "flag_split" ? "Possible combined identity"
                              : "Record inspected rule sample"}
                      </strong>
                      {scopeSummary && ["same_as", "distinct"].includes(pendingDecision) ? (
                        <small className="pl-decision-scope">{scopeSummary}</small>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="pl-decision-cancel"
                      onClick={() => setPendingDecision(null)}
                    >
                      Cancel
                    </button>
                  </div>
                  {pendingDecision === "flag_split" && detail.persons.length > 1 ? (
                    <div className="pl-split-choices" role="group" aria-label="Record that may contain several people">
                      {detail.persons.map((person) => (
                        <button
                          className={splitPersonId === person.person_id ? "is-selected" : ""}
                          key={person.person_id}
                          onClick={() => setSplitPersonId(person.person_id)}
                          type="button"
                        >
                          #{person.person_id} · {person.display_name}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {selectedCitations.length ? (
                    <div className="pl-selected-citations" aria-label="Attached source evidence">
                      <span>Attached evidence</span>
                      {selectedCitations.map((citation) => (
                        <button
                          key={citation.source_entry_id}
                          onClick={() => setSelectedCitations((current) =>
                            current.filter((item) => item.source_entry_id !== citation.source_entry_id)
                          )}
                          title="Remove this citation"
                          type="button"
                        >
                          {citation.label || citation.date || citation.source_entry_id} ×
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {error ? <p className="pl-decision-error" role="alert">{error}</p> : null}
                  <div className="pl-decision-fields">
                    {!reviewerLocked ? (
                      <input
                        className="reviewer-input"
                        value={reviewer}
                        onChange={(event) => setReviewer(event.target.value)}
                        placeholder="Your initials"
                        aria-label="Reviewer"
                      />
                    ) : (
                      <span className="pl-reviewer-identity">{reviewer}</span>
                    )}
                    <input
                      className="note-input"
                      value={rationale}
                      onChange={(event) => setRationale(event.target.value)}
                      placeholder={
                        pendingDecision === "defer"
                          ? "What evidence is still needed? (required)"
                          : pendingDecision === "same_as" && lane === "rule_exclusions"
                            ? "Why does the source override the career rule? (required)"
                            : "Rationale or source note (optional)"
                      }
                      aria-label="Decision rationale"
                    />
                    <button
                      className="pl-confirm-button"
                      disabled={busy || !reviewer.trim()}
                      onClick={confirmDecision}
                      type="button"
                    >
                      {busy ? "Saving…" : pendingDecision === "flag_split" && detail.kind !== "split"
                        ? "Flag record"
                        : pendingDecision === "flag_split"
                          ? "Flag and continue"
                          : pendingDecision === "bulk_rule_ack"
                            ? "Record sample"
                            : pendingDecision === "defer"
                              ? "Defer and continue"
                              : "Confirm and continue"}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="pl-decision-question">
                    <span>Your decision</span>
                    <strong>
                      {detail.person_ids.length > 1
                        ? "Do these records describe the same historical person?"
                        : "Does this entered record contain more than one historical person?"}
                    </strong>
                    <small>Original rows remain unchanged · every decision can be undone</small>
                  </div>
                  <div className="pl-decision-action-area">
                    {needsGroupScope ? (
                      <p className="pl-scope-required">Choose a whole-group or pair scope above before deciding same/different.</p>
                    ) : null}
                    <div className={detail.person_ids.length > 1 ? "pl-decision-options" : "pl-decision-options is-single-record"}>
                      {detail.person_ids.length > 1 ? (
                        <>
                          <button
                            className={lane === "rule_exclusions" ? "is-same is-override" : "is-same"}
                            disabled={needsGroupScope}
                            onClick={() => chooseDecision("same_as")}
                            type="button"
                          >
                            <span aria-hidden="true">✓</span>
                            {lane === "rule_exclusions" ? "Same person (override)" : "Same person"}
                          </button>
                          <button className="is-distinct" disabled={needsGroupScope} onClick={() => chooseDecision("distinct")} type="button">
                            <span aria-hidden="true">≠</span>Different people
                          </button>
                        </>
                      ) : (
                        <button className="is-split" onClick={() => chooseDecision("flag_split")} type="button">
                          <span aria-hidden="true">⑂</span>Confirm possible split
                        </button>
                      )}
                      <button className="is-uncertain" onClick={() => chooseDecision("defer")} type="button">
                        <span aria-hidden="true">?</span>Not enough evidence
                      </button>
                    </div>
                    {detail.person_ids.length > 1 ? (
                      <button
                        className="pl-split-secondary"
                        onClick={() => chooseDecision("flag_split")}
                        type="button"
                      >
                        Flag one record as possibly containing multiple people…
                      </button>
                    ) : null}
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </section>
      {evidenceDrawer && detail ? (
        <EvidenceDrawer
          kind={evidenceDrawer}
          detail={detail}
          relatedSources={relatedSources}
          reviewer={reviewer}
          busy={busy}
          onClose={() => setEvidenceDrawer(null)}
          onOpenSource={setSourceEntryId}
          onUndo={(eventId) => void undoDecision(eventId)}
          onReopen={() => void act("reopen")}
        />
      ) : null}
      {sourceEntryId ? (
        <WordSourceDrawer
          sourceEntryId={sourceEntryId}
          onClose={() => setSourceEntryId(null)}
          evidenceSelected={selectedCitations.some((item) => item.source_entry_id === sourceEntryId)}
          onUseEvidence={(entry) => {
            setSelectedCitations((current) => {
              if (current.some((item) => item.source_entry_id === entry.source_entry_id)) {
                return current.filter((item) => item.source_entry_id !== entry.source_entry_id);
              }
              const sourceText = entry.rich?.clean_text || entry.text || "";
              return [
                ...current,
                {
                  source_entry_id: entry.source_entry_id,
                  source_entry_key: entry.source_entry_key,
                  register_id: entry.register_id,
                  date: entry.date,
                  folio: entry.folio,
                  label: entry.label,
                  source_quote: sourceText.slice(0, 500),
                },
              ];
            });
          }}
        />
      ) : null}
    </div>
  );
}
