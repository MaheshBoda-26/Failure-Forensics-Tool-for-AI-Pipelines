import { Trace, Diagnosis, AnalyticsSnapshot, EvalRun, EvalRunSummary, Flag, FailureCategory, EvalResult } from "./types";

const API_BASE = "";

// localStorage key *name*, not a credential.
const API_KEY_STORAGE = "forensics-api-key"; // pragma: allowlist secret

function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

export { setApiKey, clearApiKey };

async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const apiKey = getApiKey();
  const headers = new Headers(options.headers);
  if (apiKey) {
    headers.set("Authorization", `Bearer ${apiKey}`);
  }
  return fetch(url, { ...options, headers });
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    throw new Error("Unauthorized: missing or invalid API key. Set your key in Settings and try again.");
  }
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`API error ${res.status}: ${errorText || res.statusText}`);
  }
  return res.json();
}

export const api = {
  async getTraces(limit: number = 100): Promise<Trace[]> {
    const res = await apiFetch(`${API_BASE}/traces?limit=${limit}`);
    return handleResponse<Trace[]>(res);
  },

  async getTrace(traceId: string): Promise<Trace> {
    const res = await apiFetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}`);
    return handleResponse<Trace>(res);
  },

  async getDiagnosis(traceId: string): Promise<Diagnosis> {
    const res = await apiFetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/diagnosis`);
    return handleResponse<Diagnosis>(res);
  },

  async runDiagnosis(traceId: string): Promise<Diagnosis> {
    const res = await apiFetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/diagnose`, {
      method: "POST",
    });
    return handleResponse<Diagnosis>(res);
  },

  async confirmDiagnosis(
    diagnosisId: string,
    confirmed: boolean,
    overrideCategory?: FailureCategory | null,
    overrideReason?: string | null,
    overrideStep?: string | null
  ): Promise<Diagnosis> {
    const res = await apiFetch(`${API_BASE}/diagnoses/${encodeURIComponent(diagnosisId)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmed,
        override_category: overrideCategory || null,
        override_reason: overrideReason || null,
        override_step: overrideStep || null,
      }),
    });
    return handleResponse<Diagnosis>(res);
  },

  async flagTrace(traceId: string, note?: string): Promise<Flag> {
    const res = await apiFetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/flag`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "web_inspector", note }),
    });
    return handleResponse<Flag>(res);
  },

  async getAnalytics(): Promise<AnalyticsSnapshot> {
    const res = await apiFetch(`${API_BASE}/analytics`);
    return handleResponse<AnalyticsSnapshot>(res);
  },

  async getEvalRuns(): Promise<EvalRun[]> {
    const res = await apiFetch(`${API_BASE}/eval/runs`);
    return handleResponse<EvalRun[]>(res);
  },

  async getEvalResults(runId: string): Promise<EvalResult[]> {
    const res = await apiFetch(`${API_BASE}/eval/runs/${encodeURIComponent(runId)}/results`);
    return handleResponse<EvalResult[]>(res);
  },

  async triggerEvalRun(): Promise<EvalRunSummary> {
    const res = await apiFetch(`${API_BASE}/eval/run`, {
      method: "POST",
    });
    return handleResponse<EvalRunSummary>(res);
  },

  async runDemoPipeline(): Promise<{ status: string; traces_saved: number }> {
    const res = await apiFetch(`${API_BASE}/pipeline/run-demo`, {
      method: "POST",
    });
    return handleResponse<{ status: string; traces_saved: number }>(res);
  },
};
