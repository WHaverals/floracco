export type ReviewSummary = {
  qa_packet_path: string;
  decisions_path: string;
  total_cases: number;
  reviewed_cases: number;
  buckets: string[];
  registers: string[];
};

export type CasePreview = {
  review_id: string;
  source_entry_id: string;
  source_entry_key: string;
  register_id: string;
  recommended_review_bucket: string;
  word_registration_date: string;
  word_folio_range: string;
  suggested_db_row_ids: string;
  is_reviewed: boolean;
};

export type CaseListResponse = {
  total: number;
  cases: CasePreview[];
};

export type LinkMetric = {
  narrative_similarity_ratio: number | null;
  text_containment_ratio: number | null;
  match_strength: number | null;
  longest_shared_phrase_words: number | null;
  score: number | null;
  relationship_type: string | null;
  link_role: "primary" | "alternative" | null;
  link_ordinal: number | null;
  /** Pipeline-owned Word-label ↔ DB-type verdict (qa_packet_schema.md v4). */
  event_type_relation: "exact" | "interpretive" | "mismatch" | "unknown" | null;
};

export type ReviewCase = {
  row: Record<string, string | number | boolean | null>;
  suggested_db_row_ids: string[];
  db_rows: Record<string, string | number | null>[];
  image_paths: string[];
  image_candidates: WordEntryImage[];
  evidence_items: EvidenceItem[];
  highlight_values: HighlightValue[];
  act_components: ActComponent[];
  link_metrics?: Record<string, LinkMetric>;
  word_entry_rich?: WordEntryRich | null;
  decision?: Record<string, string> | null;
};

export type ActComponentMappingConfidence = "exact" | "heuristic" | "unmapped";

export type ActComponent = {
  raw_label: string | null;
  label_guess: string | null;
  label_display: string;
  event_number: number | null;
  referenced_event_number: number | null;
  suggested_db_row_id: string | null;
  link_component_label: string | null;
  mapping_confidence: ActComponentMappingConfidence;
  link_score: number | null;
};

export type RevisionChangeKind = "insertion" | "deletion" | "move_from" | "move_to";

export type RevisionChange = {
  tag: string;
  kind: RevisionChangeKind;
  id: string | null;
  author: string | null;
  date: string | null;
};

export type RevisionToken =
  | { type: "text"; text: string; changes: RevisionChange[]; comment_ids: string[] }
  | { type: "break" }
  | { type: "tab" }
  | { type: "comment_ref"; id: string | null }
  | { type: "note_ref"; id: string | null; kind: "footnote" | "endnote" };

export type RevisionSummary = {
  insertions: number;
  deletions: number;
  moves: number;
  comments: number;
  notes: number;
};

export type EntryComment = {
  id: string;
  author?: string | null;
  date?: string | null;
  initials?: string | null;
  text?: string | null;
};

export type EntryNote = {
  id: string;
  kind: "footnote" | "endnote";
  text?: string | null;
};

export type WordEntryRich = {
  has_revisions: boolean;
  summary: RevisionSummary;
  tokens: RevisionToken[];
  comments: EntryComment[];
  notes: EntryNote[];
  clean_text: string;
};

export type EvidenceStatus = "match" | "strong" | "partial" | "weak" | "neutral" | "conflict" | "review";

export type EvidenceItem = {
  kind: string;
  label: string;
  status: EvidenceStatus;
  detail: string;
  metric: number | null;
  highlight_values: string[];
};

export type HighlightValue = {
  value: string;
  status: EvidenceStatus;
  label: string;
};

export type DbBrowseTable = "contract" | "sub_contract" | "person";

/** A "Needs review" data-quality flag (computed live by the backend). */
export type DbFlagFix = { kind: string; field: string | null; investor_id?: string };
export type DbFlag = {
  key: string;
  group: string;
  table: DbBrowseTable;
  pk: string;
  title: string;
  severity: "high" | "medium";
  explanation: string;
  fix: DbFlagFix;
};
export type DbFlagGroup = {
  group: string;
  label: string;
  severity: "high" | "medium";
  explanation: string;
  items: DbFlag[];
};
export type DbFlagsResponse = { total: number; groups: DbFlagGroup[] };

export type DbSearchResult = {
  id: string;
  row_id: string;
  title: string;
  meta: string;
};

export type DbSearchResponse = {
  table: DbBrowseTable;
  total: number;
  shown: number;
  offset: number;
  results: DbSearchResult[];
};

export type DbFacets = {
  registers: { folder: string; label: string; count: number }[];
  year_histogram: { decade: number; count: number }[];
  year_min: number | null;
  year_max: number | null;
  sub_types: { value: string; count: number }[];
};

export type DbFieldCorrection = {
  proposal_id: string;
  status: CorrectionStatus;
  change_type: CorrectionChangeType;
  proposed_value: string | null;
  applied_at: string | null;
  applied_by: string | null;
  reviewed_by: string | null;
};

export type DbFieldInputType = "text" | "date" | "number" | "enum" | "textarea" | "bool";

/** An FK field that re-points to a lookup row (title/place/currency/activity)
 * via the relink endpoint — reuse an existing phrase, create one verbatim, or
 * clear. The phrase itself is never edited in place. */
export type DbRelink = {
  table: string;
  pk: string;
  field: string;
  kind: "economic_activity" | "place" | "currency" | "title";
  current: string;
};

/** A curated Word↔DB registration-date disagreement, surfaced on the date field as
 * *evidence* for the reviewer to verify against the manuscript — never an assertion or an
 * auto-fix. `tier` ranks confidence; only tracked_change / clear are surfaced (one_day is
 * held). Dates are calendar-normalized (stile fiorentino resolved); `word_raw` is the
 * verbatim transcription. Image paths feed the existing /api/images endpoint. */
export type WordDateCheck = {
  db_row_id: string;
  table: "contract" | "sub_contract";
  field: "registration_date";
  tier: "tracked_change" | "clear" | "one_day";
  surfaced: boolean;
  db_value: string;
  db_display: string | null;
  word_iso: string;
  word_display: string | null;
  word_raw: string;
  gap_days: number;
  revision: { removed: string[]; added: string[]; author: string | null } | null;
  source_entry_id: string | null;
  field_overlap_count: number;
  folio: string | null;
  page_side: "recto" | "verso" | null;
  images: { primary: string | null; prev: string | null; next: string | null };
};

export type DbField = {
  label: string;
  value: string;
  column: string | null;
  editable: boolean;
  input_type?: DbFieldInputType;
  options?: string[] | null;
  current?: string;
  correction?: DbFieldCorrection;
  relink?: DbRelink;
  /** Present only on registration_date when a curated Word↔DB conflict exists (step 3 renders it). */
  word_check?: WordDateCheck | null;
};

export type DbSectionRow = {
  id: string;
  cells: string[];
};

/** One editable cell of a child row (investor/investment) in the Partners block.
 * Carries its own db_row_id because each partner addresses a different SQLite row. */
export type DbEditableCell = {
  db_row_id: string;
  column: string;
  value: string;
  editable: boolean;
  current: string;
  input_type: DbFieldInputType;
  options?: string[] | null;
  correction?: DbFieldCorrection;
};

export type DbPartnerCash = {
  display: string;
  non_cash: string;
  /** Joint = this stake is co-held, recorded either as one shared investment
   * (then `joint_count` > 1, shown as "joint · N") or as parallel investments
   * marked by the per-investor flag (then `joint_count` is 1, shown as "joint").
   * Where shown, the cash is the *shared* figure, not per-person. */
  joint: boolean;
  /** Live count of investors on the shared tranche; 1 when joint comes from the
   * stored flag rather than a shared investment (so the UI omits "· N"). */
  joint_count: number;
  field: DbEditableCell | null;
};

/** One field inside the per-partner detail panel: either an editable cell, or a
 * read-only FK display value (`locked` — title/place, until relink ships). */
export type DbPartnerAttrField = {
  label: string;
  cell?: DbEditableCell;
  value?: string;
  locked?: boolean;
  relink?: DbRelink;
};

/** One place a contract's firm operated in. The place itself is re-pointed by
 * remove + add (its id is part of the composite key); `address` is editable text. */
export type DbPlace = {
  key: string;
  place_id: string;
  place: string;
  address: string;
  removed: boolean;
};

export type DbPlaces = { count: number; rows: DbPlace[]; removed_count?: number };

export type DbPartnerAttrGroup = { label: string; fields: DbPartnerAttrField[] };

/** The investor's full per-appearance record, grouped for the expand panel.
 * `notable` counts the sparse meaningful attrs (drives the row's expand cue). */
export type DbPartnerAttributes = { notable: number; groups: DbPartnerAttrGroup[] };

export type DbPartnerRow = {
  key: string;
  person: { id: string; name: string } | null;
  role: DbEditableCell | null;
  cash: DbPartnerCash;
  profession: DbEditableCell | null;
  residence: string;
  status: string;
  /** Soft-deleted ("removed") partner — only present when the record was loaded
   * with include_hidden; rendered greyed with a Restore action. */
  removed: boolean;
  /** Full attribute set for the expand panel (live rows only; null when removed). */
  attributes: DbPartnerAttributes | null;
};

export type DbPartners = {
  count: number;
  rows: DbPartnerRow[];
  /** Number of soft-deleted partner rows included (0 unless include_hidden). */
  removed_count?: number;
};

export type DbSection = {
  title: string;
  columns: string[];
  rows: DbSectionRow[];
  link_table?: DbBrowseTable | null;
};

export type DbLinkStatus = "confirmed" | "proposed" | "rejected";

export type DbWordSource = {
  source_entry_id: string;
  source_entry_key: string | null;
  register_id: string | null;
  label: string | null;
  date: string | null;
  folio: string | null;
  relationship: string | null;
  strength: number | null;
  status: DbLinkStatus;
  via?: string | null;
  via_row_id?: string | null;
  /** Paleographic-doubt comments on the summary (badge on the collapsed strip). */
  comment_count?: number;
};

// --- Creating DB-native records --------------------------------------------

export type RegisterOption = {
  archive: string;
  series: string;
  folder: string;
  contracts: number;
};

export type NumberCheck = {
  free: boolean;
  existing: { id: string; title: string; date: string | null; folio: string | null; folder: string | null } | null;
};

export type SimilarRow = {
  row_id: string;
  table: "contract" | "sub_contract";
  id: string;
  title: string;
  date: string | null;
  folio: string | null;
  match: string;
};

export type LookupValue = { id: number; value: string; used: number };

export type ContractCreatePayload = {
  reviewer: string;
  source: string;
  archive: string;
  series: string;
  folder: string;
  folio: string;
  registration_date: string;
  register_number: number | null;
  firm_name: string;
  economic_activity: string;
  total: number | null;
  document: string;
};

export type SubContractCreatePayload = {
  reviewer: string;
  source: string;
  main_contract_id: number;
  sub_type: string;
  archive: string;
  series: string;
  folder: string;
  folio: string;
  registration_date: string;
  end_date: string;
  renewal_months: number | null;
  sub_firm_name: string;
  document: string;
};

export type WordEntryImageFolio = {
  folio: string | null;
  page_position: string | null;
  entry_folio_role: string | null;
};

export type WordEntryImage = {
  path: string;
  file: string | null;
  role: string | null;
  needs_review: boolean;
  folios: WordEntryImageFolio[];
};

export type WordEntryDetail = {
  source_entry_id: string;
  source_entry_key: string | null;
  register_id: string | null;
  label: string | null;
  date: string | null;
  folio: string | null;
  has_revisions: boolean;
  text: string;
  /** Tracked-changes token stream + comment/footnote bodies (frozen Word evidence). */
  rich?: WordEntryRich | null;
  images: WordEntryImage[];
};

export type ChangeHistoryEvent = { event: string; at: string; by: string; note: string | null };

export type ChangeHistoryItem = {
  request_id: string;
  op: string; // update | relink | create | delete | restore
  field: string | null;
  before_value: unknown;
  after_value: unknown;
  status: string; // applied | reverted | conflict | proposed | ...
  reason: string | null;
  created_by: string;
  created_at: string;
  events: ChangeHistoryEvent[];
};

export type DbRecord = {
  table: DbBrowseTable;
  id: string;
  row_id: string;
  title: string;
  subtitle: string;
  fields: DbField[];
  /** Contracts only: people + role + capital, merged from investor/investment. */
  partners?: DbPartners | null;
  /** Contracts only: the place(s) the firm operated in (editable). */
  places?: DbPlaces | null;
  sections: DbSection[];
  document: string | null;
  /** Latest correction touching the narrative (document) field, if any. */
  document_correction?: DbFieldCorrection | null;
  /** Manuscript page candidates found by (register folder, folio) — works for
   * any record, including ones with no Word entry. Provisional map. */
  manuscript_images?: WordEntryImage[];
  word_sources: DbWordSource[];
  word_sources_note?: string | null;
  is_deleted?: boolean;
  dependents?: Record<string, number>;
  change_history?: ChangeHistoryItem[];
};

export type CorrectionStatus =
  | "draft"
  | "proposed"
  | "approved"
  | "rejected"
  | "applied"
  | "reverted";

export type CorrectionChangeType = "correct" | "fill_missing" | "flag_uncertain";

export type CorrectionSource = {
  source_entry_id: string | null;
  source_entry_key: string | null;
  source_quote: string | null;
  register_id: string | null;
  folio: string | null;
  link_review_id: string | null;
};

export type CorrectionProposal = {
  proposal_id: string;
  created_at: string;
  created_by: string;
  origin: "manual" | "agent_suggested";
  db_table: DbBrowseTable;
  db_row_id: string;
  primary_key: Record<string, string>;
  field: string;
  field_label: string;
  change_type: CorrectionChangeType;
  current_value: string;
  proposed_value: string;
  rationale: string;
  source: CorrectionSource;
  evidence_fingerprint: string;
  status: CorrectionStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  applied_at: string | null;
  applied_by: string | null;
  applied_run_id: string | null;
  // present only from GET /api/corrections/{id}
  db_value_now?: string | null;
  is_stale?: boolean;
};

export type CorrectionListResponse = {
  total: number;
  statuses: CorrectionStatus[];
  tables: DbBrowseTable[];
  proposals: CorrectionProposal[];
};

export type CorrectionCandidateFamily = "word_db_conflict" | "db_intrinsic";
export type CorrectionCandidateStrength = "high" | "medium" | "low";

export type CandidateExistingProposal = {
  proposal_id: string;
  status: CorrectionStatus;
  proposed_value: string | null;
};

// A tracked change in the Word source that touches the same field as the conflict.
// Word is evidence, not truth: this strengthens a date/folio conflict, it never
// creates a candidate on its own.
export type CandidateRevisionEvidence = {
  insertions: string[];
  deletions: string[];
  author: string | null;
  date: string | null;
};

export type CorrectionCandidate = {
  candidate_key: string;
  db_row_id: string;
  db_table: DbBrowseTable;
  primary_key: Record<string, string>;
  field: string | null;
  field_label: string | null;
  editable: boolean;
  input_type: DbFieldInputType | null;
  options: string[] | null;
  family: CorrectionCandidateFamily;
  reason_code: string;
  title: string;
  explanation: string;
  strength: CorrectionCandidateStrength;
  priority_score: number;
  db_value: string;
  word_value: string | null;
  // adjudicated reading pre-filled into a draft (dates only); null = show, don't pre-fill
  suggested_value: string | null;
  // present only when a tracked change edited this field's source text
  revision_evidence: CandidateRevisionEvidence | null;
  source_entry_id: string | null;
  source_entry_key: string | null;
  register_id: string | null;
  source_folio: string | null;
  evidence_snippet: string;
  generated_at: string;
  builder_version: number;
  // server-annotated
  link_confirmed: boolean;
  dismissed: boolean;
  dismissed_reason: string | null;
  existing_proposal: CandidateExistingProposal | null;
  rank_score: number;
};

export type CandidateListResponse = {
  total: number;
  total_all: number;
  dismissed_count: number;
  handled_count: number;
  families: CorrectionCandidateFamily[];
  reasons: string[];
  tables: DbBrowseTable[];
  registers: string[];
  strengths: CorrectionCandidateStrength[];
  candidates: CorrectionCandidate[];
  generated_at: string | null;
};

export type CorrectionCreatePayload = {
  reviewer: string;
  db_row_id: string;
  field: string;
  change_type: CorrectionChangeType;
  proposed_value: string;
  rationale: string;
  origin: "manual" | "agent_suggested";
  source_entry_id: string;
  source_entry_key: string;
  source_quote: string;
  source_register_id: string;
  source_folio: string;
  link_review_id: string;
};

// Person picker — resolve a name mention to an existing person_id. Names are highly
// ambiguous in this corpus, so the picker offers the people already on the contract
// (with disambiguating context) plus an explicit cross-database search step.
export type ContractPerson = {
  person_id: string;
  row_id: string;
  display_name: string;
  detail: string | null;
  appears_on_contracts: number;
  first_name: string;
  last_name: string;
};

export type ContractPersonsResponse = {
  contract_id: string;
  contract_title: string;
  contract_date: string;
  persons: ContractPerson[];
};

export type DecisionPayload = {
  reviewer: string;
  source_entry_key: string;
  source_entry_id: string;
  suggested_db_row_id: string;
  packet_section: string;
  register_id: string;
  recommended_review_bucket: string;
  main_judgment: string;
  image_judgment: string;
  field_correction_needed: string;
  next_action: string;
  review_note: string;
  image_candidate_paths: string;
  selected_db_row_ids: string[];
  rejected_db_row_ids: string[];
  /** Alternatives the reviewer neither selected nor rejected (no decision status). */
  unassessed_db_row_ids: string[];
  suggested_relationship_type: string;
};

// --- Global search (FTS over the database) ----------------------------------

export type SearchResult = {
  kind: "contract" | "sub_contract" | "person";
  ref: string;
  title: string;
  meta: string;
  /** Matched terms wrapped in « » (rendered as <mark>). */
  snippet: string;
};

export type SearchGroup = {
  kind: "contract" | "sub_contract" | "person";
  label: string;
  total: number;
  results: SearchResult[];
};

export type IdJump = { kind: "contract" | "sub_contract" | "person"; ref: string; title: string; meta: string };

export type SearchResponse = {
  total: number;
  groups: SearchGroup[];
  term_counts: { term: string; count: number }[] | null;
  id_jumps: IdJump[];
};

// --- Adding investors ---------------------------------------------------------

export type PersonHit = {
  person_id: string;
  display_name: string;
  father_mother: string;
  residences: string;
  appearances: number;
  is_woman?: boolean;
};

export type ContractInvestment = {
  investment_id: string;
  type: string;
  cash: number | null;
  non_cash: string;
  partnership_name: string;
  members: string;
};

export type NewPersonPayload = {
  first_name: string;
  father_mother: string;
  last_name: string;
  is_woman: boolean;
};

export type InvestorCreatePayload = {
  reviewer: string;
  contract_id: number;
  person_id: number | null;
  new_person: NewPersonPayload | null;
  role: string;
  join_investment_id: number | null;
  investment_cash: number | null;
  cash_unspecified: boolean;
  investment_non_cash: string;
  partnership_name: string;
  title: string;
  residence: string;
  origin: string;
  profession: string;
  via_proxy: boolean;
  citizen_florence: boolean;
  is_widow: boolean;
  is_guardian: boolean;
  is_jewish: boolean;
  is_convert: boolean;
  heirs: boolean;
  heirs_of: boolean;
  and_c: boolean;
  note: string;
};
