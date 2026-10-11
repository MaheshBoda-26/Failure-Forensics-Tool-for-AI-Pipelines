const API_BASE = "";
async function handleResponse(res) {
    if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`API error ${res.status}: ${errorText || res.statusText}`);
    }
    return res.json();
}
export const api = {
    async getTraces(limit = 100) {
        const res = await fetch(`${API_BASE}/traces?limit=${limit}`);
        return handleResponse(res);
    },
    async getTrace(traceId) {
        const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}`);
        return handleResponse(res);
    },
    async getDiagnosis(traceId) {
        const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/diagnosis`);
        return handleResponse(res);
    },
    async runDiagnosis(traceId) {
        const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/diagnose`, {
            method: "POST",
        });
        return handleResponse(res);
    },
    async confirmDiagnosis(diagnosisId, confirmed, overrideCategory, overrideReason) {
        const res = await fetch(`${API_BASE}/diagnoses/${encodeURIComponent(diagnosisId)}/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                confirmed,
                override_category: overrideCategory || null,
                override_reason: overrideReason || null,
            }),
        });
        return handleResponse(res);
    },
    async flagTrace(traceId, note) {
        const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}/flag`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: "web_inspector", note }),
        });
        return handleResponse(res);
    },
    async getAnalytics() {
        const res = await fetch(`${API_BASE}/analytics`);
        return handleResponse(res);
    },
    async getEvalRuns() {
        const res = await fetch(`${API_BASE}/eval/runs`);
        return handleResponse(res);
    },
    async triggerEvalRun() {
        const res = await fetch(`${API_BASE}/eval/run`, {
            method: "POST",
        });
        return handleResponse(res);
    },
    async runDemoPipeline() {
        const res = await fetch(`${API_BASE}/pipeline/run-demo`, {
            method: "POST",
        });
        return handleResponse(res);
    },
};
