export type ReviewSummary = {
  qa_packet_path: string;
  decisions_path: string;
  total_cases: number;
  reviewed_cases: number;
  priorities: string[];
  buckets: string[];
  registers: string[];
};

export type CasePreview = {
  review_id: string;
  source_entry_id: string;
  source_entry_key: string;
  register_id: string;
  review_priority: string;
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

export type ReviewCase = {
  row: Record<string, string | number | boolean | null>;
  suggested_db_row_ids: string[];
  db_rows: Record<string, string | number | null>[];
  image_paths: string[];
  evidence_items: EvidenceItem[];
  highlight_values: HighlightValue[];
  word_entry_rich?: WordEntryRich | null;
  decision?: Record<string, string> | null;
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
  results: DbSearchResult[];
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

export type DbFieldInputType = "text" | "date" | "number" | "enum";

export type DbField = {
  label: string;
  value: string;
  column: string | null;
  editable: boolean;
  input_type?: DbFieldInputType;
  options?: string[] | null;
  current?: string;
  correction?: DbFieldCorrection;
};

export type DbSectionRow = {
  id: string;
  cells: string[];
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
};

export type WordEntryImage = {
  path: string;
  file: string | null;
  role: string | null;
  page_position: string | null;
  folio: string | null;
  needs_review: boolean;
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
  images: WordEntryImage[];
};

export type DbRecord = {
  table: DbBrowseTable;
  id: string;
  row_id: string;
  title: string;
  subtitle: string;
  fields: DbField[];
  sections: DbSection[];
  document: string | null;
  word_sources: DbWordSource[];
  word_sources_note?: string | null;
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
  register_id: string;
  review_priority: string;
  recommended_review_bucket: string;
  main_judgment: string;
  image_judgment: string;
  field_correction_needed: string;
  next_action: string;
  review_note: string;
  image_candidate_paths: string;
  selected_db_row_ids: string[];
  rejected_db_row_ids: string[];
  suggested_relationship_type: string;
};
