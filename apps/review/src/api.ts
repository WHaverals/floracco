import type {
  CaseListResponse,
  ContractPersonsResponse,
  CorrectionCreatePayload,
  CorrectionProposal,
  DbBrowseTable,
  DbFacets,
  DbRecord,
  DbSearchResponse,
  DecisionPayload,
  ReviewCase,
  ReviewSummary,
  WordEntryDetail,
} from "./types";

function apiError(message: string, status: number): Error & { status?: number } {
  const error = new Error(message) as Error & { status?: number };
  error.status = status;
  return error;
}

/* Every failure reaches the reviewer as a plain sentence. A message the server
 * wrote for the reviewer (a JSON `detail` string: a validation refusal, a
 * maintenance notice, a "changed while you were editing" guard) passes through
 * unchanged; network failures, unreadable replies and bare server errors do not.
 * The technical detail goes to the browser console for diagnosis. */
async function send(path: string, options?: RequestInit): Promise<Response> {
  // Every write is a POST. A write that got no proper answer may or may not have
  // landed, so its message says so instead of promising that nothing was saved.
  // The Analysis queries are POSTs too but only read.
  const writing = (options?.method ?? "GET").toUpperCase() !== "GET" && !path.startsWith("/api/analysis/");
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
      ...options,
    });
  } catch (cause) {
    // "Failed to fetch" / "Load failed" / "NetworkError…": no answer at all.
    console.warn("API request did not reach the server:", path, cause);
    throw apiError(
      writing
        ? "The server could not be reached, so the change may not have been saved. Reload the page to check before trying again."
        : "The server could not be reached. Check the connection and try again.",
      0,
    );
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let message = writing
      ? "The server did not answer properly, so the change may not have been saved. Reload the page to check before trying again."
      : "The server did not answer properly. Please try again.";
    try {
      const body = JSON.parse(text);
      if (body && typeof body.detail === "string" && body.detail.trim()) {
        message = body.detail; // the server's own wording
      } else if (body && Array.isArray(body.detail)) {
        // Field validation: refused before anything was written.
        message = "The server did not accept what was entered, and nothing was saved. Check the fields and try again.";
      }
    } catch {
      // not JSON (a proxy or host error page, a bare "Internal Server Error", an empty body): keep the default
    }
    if (/database is locked/i.test(message)) {
      message = "The server is busy. Please try again.";
    }
    console.warn("API request failed:", path, response.status, text.slice(0, 300));
    throw apiError(message, response.status);
  }
  return response;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await send(path, options);
  try {
    return (await response.json()) as T;
  } catch (cause) {
    // A success reply that is not JSON, typically a sign-in page served instead of the answer.
    console.warn("API reply was not JSON:", path, cause);
    throw apiError(
      "The server sent a reply this page could not read. Reload the page; you may need to sign in again.",
      response.status,
    );
  }
}

/** The signed-in reviewer (from Cloudflare Access), or unauthenticated in local dev. */
export function loadMe(): Promise<{ authenticated: boolean; email: string }> {
  return request("/api/me");
}

export function loadSummary(): Promise<ReviewSummary> {
  return request<ReviewSummary>("/api/summary");
}

export function loadCases(params: URLSearchParams): Promise<CaseListResponse> {
  return request<CaseListResponse>(`/api/cases?${params.toString()}`);
}

export function loadCase(reviewId: string): Promise<ReviewCase> {
  return request<ReviewCase>(`/api/cases/${encodeURIComponent(reviewId)}`);
}

export function saveDecision(decision: DecisionPayload): Promise<{ ok: boolean; review_id: string }> {
  return request<{ ok: boolean; review_id: string }>("/api/decisions", {
    method: "POST",
    body: JSON.stringify(decision),
  });
}

export function searchDb(
  table: DbBrowseTable,
  q: string,
  opts: {
    includeHidden?: boolean;
    sort?: string;
    offset?: number;
    register?: string;
    yearFrom?: number | null;
    yearTo?: number | null;
    subType?: string;
    gender?: string;
  } = {},
): Promise<DbSearchResponse> {
  const params = new URLSearchParams({ table, q });
  if (opts.includeHidden) params.set("include_hidden", "true");
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.register) params.set("register", opts.register);
  if (opts.yearFrom != null) params.set("year_from", String(opts.yearFrom));
  if (opts.yearTo != null) params.set("year_to", String(opts.yearTo));
  if (opts.subType) params.set("sub_type", opts.subType);
  if (opts.gender) params.set("gender", opts.gender);
  return request<DbSearchResponse>(`/api/db/search?${params.toString()}`);
}

export function loadDbFacets(table: DbBrowseTable): Promise<DbFacets> {
  return request<DbFacets>(`/api/db/facets?table=${encodeURIComponent(table)}`);
}

export function loadAnalysisLibrary(): Promise<{ queries: import("./types").AnalysisQuery[] }> {
  return request("/api/analysis/library");
}

export function runAnalysisLibrary(sql: string): Promise<import("./types").AnalysisResult> {
  return request("/api/analysis/run", { method: "POST", body: JSON.stringify({ sql }) });
}

export type AnalysisBuildSpec = {
  subject: string;
  measure: string;
  filters: { field: string; value?: string }[];
  group_by: string;
};

export function runAnalysisBuild(
  spec: AnalysisBuildSpec,
): Promise<import("./types").AnalysisResult & { sql: string; chart: import("./types").AnalysisChart }> {
  return request("/api/analysis/build", { method: "POST", body: JSON.stringify(spec) });
}

/** The CSV download of a library or console query (`sql`) or a builder question
 * (`spec`): every row, written by the server before it sends the file. A refusal
 * (too many rows, too slow) arrives as a sentence; a download that breaks off
 * saves nothing. */
export async function downloadAnalysisCsv(body: { sql: string } | { spec: AnalysisBuildSpec }): Promise<Blob> {
  const response = await send("/api/analysis/export", { method: "POST", body: JSON.stringify(body) });
  if (!(response.headers.get("Content-Type") ?? "").startsWith("text/csv")) {
    throw apiError("The server sent a reply this page could not read. Reload the page; you may need to sign in again.", response.status);
  }
  try {
    return await response.blob();
  } catch (cause) {
    console.warn("CSV download broke off:", cause);
    // The file is complete before its first byte is sent, so a break here is the network.
    throw apiError("The download broke off before it was complete, so no file was saved. Try again.", 0);
  }
}

export function loadReference(
  kind: import("./types").ReferenceKind,
  opts: { q?: string; sort?: string; offset?: number; orphansOnly?: boolean } = {},
): Promise<import("./types").ReferenceListResponse> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.orphansOnly) params.set("orphans_only", "true");
  const qs = params.toString();
  return request(`/api/db/reference/${kind}${qs ? `?${qs}` : ""}`);
}

export function loadReferenceDuplicates(
  kind: import("./types").ReferenceKind,
): Promise<import("./types").ReferenceDuplicatesResponse> {
  return request(`/api/db/reference/${kind}/duplicates`);
}

export function createReferenceLink(
  kind: import("./types").ReferenceKind,
  body: { reviewer: string; rel: "same_as" | "variant_of" | "distinct"; from_id: number; to_id: number; reason?: string },
): Promise<{ ok: boolean; link_id: string }> {
  return request(`/api/db/reference/${kind}/link`, { method: "POST", body: JSON.stringify(body) });
}

export function revokeReferenceLink(
  linkId: string,
  body: { reviewer: string },
): Promise<{ ok: boolean }> {
  return request(`/api/db/reference/link/${encodeURIComponent(linkId)}/revoke`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function loadPersonLinkageSummary(): Promise<import("./types").PersonLinkageSummary> {
  return request("/api/person-linkage/summary");
}

export function loadPersonLinkageCases(
  opts: import("./types").PersonLinkageCasesRequest,
): Promise<import("./types").PersonLinkageCasesResponse> {
  const params = new URLSearchParams({ lane: opts.lane });
  if (opts.status) params.set("status", opts.status);
  if (opts.priorityBand) params.set("priority_band", opts.priorityBand);
  if (opts.q) params.set("q", opts.q);
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.stranded) {
    params.set("stranded", "1");
    // Scope only travels with the facet — alone it would mean nothing server-side.
    if (opts.strandedScope) params.set("stranded_scope", opts.strandedScope);
  }
  if (opts.facet) params.set("facet", opts.facet);
  return request(`/api/person-linkage/cases?${params.toString()}`);
}

export function loadPersonLinkageCase(caseId: string): Promise<import("./types").PersonLinkageCase> {
  return request(`/api/person-linkage/cases/${encodeURIComponent(caseId)}`);
}

export function decidePersonLinkage(
  caseId: string,
  body: {
    reviewer: string;
    action: "same_as" | "distinct" | "defer" | "reopen" | "flag_split" | "bulk_rule_ack";
    person_ids: number[];
    reason_code?: string;
    rationale?: string;
    citations?: Array<Record<string, unknown>>;
    expected_status: import("./types").PersonLinkagePreview["status"];
    expected_event_id: string | null;
    review_mode?: "standard" | "blind_labeling";
    /** With flag_split only: which acts are whose (see PersonSeamInput). */
    seam?: import("./types").PersonSeamInput;
  },
): Promise<{ ok: boolean; event_id: string; link_ids: string[] }> {
  return request(`/api/person-linkage/cases/${encodeURIComponent(caseId)}/decision`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokePersonLink(
  linkId: string,
  body: { reviewer: string; reason?: string },
): Promise<{ ok: boolean; event_id: string }> {
  return request(`/api/person-linkage/links/${encodeURIComponent(linkId)}/revoke`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokePersonDecision(
  eventId: string,
  body: { reviewer: string; reason?: string },
): Promise<{ ok: boolean; event_id: string; revoked_link_ids: string[] }> {
  return request(`/api/person-linkage/events/${encodeURIComponent(eventId)}/revoke`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function loadPlaceMap(): Promise<import("./types").PlaceMapResponse> {
  return request("/api/db/reference/place/map");
}

export function loadReferenceRecords(
  kind: import("./types").ReferenceKind,
  termId: number,
  offset = 0,
): Promise<import("./types").ReferenceRecordsResponse> {
  const qs = offset ? `?offset=${offset}` : "";
  return request(`/api/db/reference/${kind}/${termId}/records${qs}`);
}

export function loadDbRecord(
  table: DbBrowseTable,
  id: string,
  includeHidden = false,
): Promise<DbRecord> {
  const q = includeHidden ? "?include_hidden=1" : "";
  return request<DbRecord>(`/api/db/record/${table}/${encodeURIComponent(id)}${q}`);
}

export function removePartner(
  contractId: string,
  investorId: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean; left_unattached: boolean }> {
  return request(
    `/api/db/contract/${encodeURIComponent(contractId)}/partner/${encodeURIComponent(investorId)}/remove`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function loadFlags(): Promise<import("./types").DbFlagsResponse> {
  return request("/api/db/flags");
}

export function dismissFlag(
  key: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(`/api/db/flags/${encodeURIComponent(key)}/dismiss`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Undo a dismissal: the item returns to Needs review. */
export function restoreFlag(key: string, body: { reviewer: string }): Promise<{ ok: boolean }> {
  return request(`/api/db/flags/${encodeURIComponent(key)}/restore`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function addPlace(
  contractId: string,
  body: { place: string; address: string; reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(`/api/db/contract/${encodeURIComponent(contractId)}/place/add`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function refuteJewishAttribution(
  investorId: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(`/api/db/investor/${encodeURIComponent(investorId)}/refute-jewish-attribution`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function setPlaceRemoved(
  contractId: string,
  placeId: string,
  removed: boolean,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  const verb = removed ? "remove" : "restore";
  return request(`/api/db/contract/${encodeURIComponent(contractId)}/place/${encodeURIComponent(placeId)}/${verb}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function editPlaceAddress(
  contractId: string,
  placeId: string,
  body: { address: string; reviewer: string; reason: string },
): Promise<{ ok: boolean; value: string }> {
  return request(`/api/db/contract/${encodeURIComponent(contractId)}/place/${encodeURIComponent(placeId)}/address`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function relinkField(
  table: string,
  recordId: string,
  body: { field: string; value: string; reviewer: string; reason: string },
): Promise<{ ok: boolean; value: string }> {
  return request(`/api/db/relink/${encodeURIComponent(table)}/${encodeURIComponent(recordId)}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function restorePartner(
  contractId: string,
  investorId: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(
    `/api/db/contract/${encodeURIComponent(contractId)}/partner/${encodeURIComponent(investorId)}/restore`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

/** Remove a stake nobody is attached to (audited soft-delete of the investment row). */
export function removeStake(
  contractId: string,
  investmentId: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(
    `/api/db/contract/${encodeURIComponent(contractId)}/stake/${encodeURIComponent(investmentId)}/remove`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function restoreStake(
  contractId: string,
  investmentId: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(
    `/api/db/contract/${encodeURIComponent(contractId)}/stake/${encodeURIComponent(investmentId)}/restore`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function hideRecord(
  table: DbBrowseTable,
  id: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean; is_deleted: boolean }> {
  return request(`/api/db/record/${table}/${encodeURIComponent(id)}/hide`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function restoreRecord(
  table: DbBrowseTable,
  id: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean; is_deleted: boolean }> {
  return request(`/api/db/record/${table}/${encodeURIComponent(id)}/restore`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function loadWordEntry(sourceEntryId: string): Promise<WordEntryDetail> {
  return request<WordEntryDetail>(`/api/word-entry/${encodeURIComponent(sourceEntryId)}`);
}

export function loadContractPersons(contractId: string): Promise<ContractPersonsResponse> {
  return request<ContractPersonsResponse>(
    `/api/db/contract-persons/${encodeURIComponent(contractId)}`,
  );
}

export function searchPersons(q: string): Promise<DbSearchResponse> {
  return request<DbSearchResponse>(`/api/db/search?table=person&q=${encodeURIComponent(q)}`);
}

export function createCorrection(
  payload: CorrectionCreatePayload,
): Promise<{ ok: boolean; proposal: CorrectionProposal }> {
  return request("/api/corrections", { method: "POST", body: JSON.stringify(payload) });
}

export function transitionCorrection(
  proposalId: string,
  action: "approve" | "reject" | "apply" | "revert",
  body: { reviewer: string; note?: string },
): Promise<{ ok: boolean; proposal: CorrectionProposal }> {
  return request(`/api/corrections/${encodeURIComponent(proposalId)}/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function imageUrl(path: string): string {
  return `/api/images?path=${encodeURIComponent(path)}`;
}

// --- Creating DB-native records ---------------------------------------------

export function loadRegisters(): Promise<{ registers: import("./types").RegisterOption[] }> {
  return request("/api/db/registers");
}

export function checkNumber(n: number): Promise<import("./types").NumberCheck> {
  return request(`/api/db/check-number/${n}`);
}

export function findSimilar(
  folder: string,
  folio: string,
  date: string,
): Promise<{ rows: import("./types").SimilarRow[] }> {
  const params = new URLSearchParams({ folder, folio, date });
  return request(`/api/db/similar?${params.toString()}`);
}

export function lookupValues(
  kind: string,
  q: string,
): Promise<{ values: import("./types").LookupValue[]; exact: import("./types").LookupValue | null }> {
  const params = new URLSearchParams({ q });
  return request(`/api/db/lookup/${encodeURIComponent(kind)}?${params.toString()}`);
}

export function createDbRecord(
  table: "contract" | "sub_contract",
  payload: import("./types").ContractCreatePayload | import("./types").SubContractCreatePayload,
): Promise<{ ok: boolean; id: string; row_id: string }> {
  return request(`/api/db/create/${table}`, { method: "POST", body: JSON.stringify(payload) });
}

export function searchGlobal(q: string, expand = "", limit = 0): Promise<import("./types").SearchResponse> {
  const params = new URLSearchParams({ q, expand });
  if (expand && limit) params.set("limit", String(limit));
  return request(`/api/search?${params.toString()}`);
}

// --- Adding investors ---------------------------------------------------------

export function searchPersonsRich(q: string, limit = 12): Promise<{ results: import("./types").PersonHit[] }> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request(`/api/db/person-search?${params.toString()}`);
}

/** Persons sharing a surname; those with the typed first name come first. `total`
 * is the uncapped count (absent from an older server, hence optional). */
export function sameSurname(
  lastName: string,
  firstName = "",
): Promise<{ results: import("./types").PersonHit[]; total?: number }> {
  const params = new URLSearchParams({ last_name: lastName, first_name: firstName });
  return request(`/api/db/same-surname?${params.toString()}`);
}

export function loadContractInvestments(
  contractId: string,
): Promise<{ investments: import("./types").ContractInvestment[] }> {
  return request(`/api/db/contract-investments/${encodeURIComponent(contractId)}`);
}

export function createInvestor(
  payload: import("./types").InvestorCreatePayload,
): Promise<{
  ok: boolean;
  investor_id: string;
  person_id: string;
  person_created: boolean;
  investment_id: string;
  joined_existing: boolean;
}> {
  return request("/api/db/create/investor", { method: "POST", body: JSON.stringify(payload) });
}
