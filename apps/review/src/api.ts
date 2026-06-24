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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
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

export function addPlace(
  contractId: string,
  body: { place: string; address: string; reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(`/api/db/contract/${encodeURIComponent(contractId)}/place/add`, {
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

export function searchGlobal(q: string, expand = ""): Promise<import("./types").SearchResponse> {
  const params = new URLSearchParams({ q, expand });
  return request(`/api/search?${params.toString()}`);
}

// --- Adding investors ---------------------------------------------------------

export function searchPersonsRich(q: string): Promise<{ results: import("./types").PersonHit[] }> {
  const params = new URLSearchParams({ q });
  return request(`/api/db/person-search?${params.toString()}`);
}

export function sameSurname(lastName: string): Promise<{ results: import("./types").PersonHit[] }> {
  const params = new URLSearchParams({ last_name: lastName });
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
