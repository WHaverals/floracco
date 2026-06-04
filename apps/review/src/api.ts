import type {
  CandidateListResponse,
  CaseListResponse,
  ContractPersonsResponse,
  CorrectionCreatePayload,
  CorrectionListResponse,
  CorrectionProposal,
  Dashboard,
  DbBrowseTable,
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

export function loadSummary(): Promise<ReviewSummary> {
  return request<ReviewSummary>("/api/summary");
}

export function loadDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/api/dashboard");
}

export function exportUrl(name: "decisions" | "proposals" | "candidates"): string {
  return `/api/export/${name}`;
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

export function searchDb(table: DbBrowseTable, q: string): Promise<DbSearchResponse> {
  const params = new URLSearchParams({ table, q });
  return request<DbSearchResponse>(`/api/db/search?${params.toString()}`);
}

export function loadDbRecord(table: DbBrowseTable, id: string): Promise<DbRecord> {
  return request<DbRecord>(`/api/db/record/${table}/${encodeURIComponent(id)}`);
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

export function loadCorrections(params: URLSearchParams): Promise<CorrectionListResponse> {
  return request<CorrectionListResponse>(`/api/corrections?${params.toString()}`);
}

export function loadCorrection(proposalId: string): Promise<CorrectionProposal> {
  return request<CorrectionProposal>(`/api/corrections/${encodeURIComponent(proposalId)}`);
}

export function createCorrection(
  payload: CorrectionCreatePayload,
): Promise<{ ok: boolean; proposal: CorrectionProposal }> {
  return request("/api/corrections", { method: "POST", body: JSON.stringify(payload) });
}

export function loadCandidates(params: URLSearchParams): Promise<CandidateListResponse> {
  return request<CandidateListResponse>(`/api/correction-candidates?${params.toString()}`);
}

export function dismissCandidate(
  candidateKey: string,
  body: { reviewer: string; reason: string },
): Promise<{ ok: boolean }> {
  return request(`/api/correction-candidates/${encodeURIComponent(candidateKey)}/dismiss`, {
    method: "POST",
    body: JSON.stringify(body),
  });
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
