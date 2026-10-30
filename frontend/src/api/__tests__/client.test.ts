import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, setApiKey, clearApiKey } from "../client";

const originalFetch = global.fetch;

beforeEach(() => {
  global.fetch = vi.fn();
  clearApiKey();
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.resetAllMocks();
  clearApiKey();
});

describe("api client", () => {
  it("attaches Authorization header when key is set", async () => {
    setApiKey("ffk_test_key_123");

    (global.fetch as any).mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ trace_id: "t1" }),
      headers: new Headers({ "content-type": "application/json" }),
    });

    await api.getTraces();

    expect(global.fetch).toHaveBeenCalledWith("/traces?limit=100", expect.objectContaining({
      headers: expect.any(Headers),
    }));
    const callHeaders = (global.fetch as any).mock.calls[0][1]?.headers as Headers;
    expect(callHeaders.get("Authorization")).toBe("Bearer ffk_test_key_123");
  });

  it("omits Authorization header when no key is set", async () => {
    clearApiKey();

    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    await api.getTraces();

    const headers = (global.fetch as any).mock.calls[0][1]?.headers;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("throws on 401 with helpful message", async () => {
    setApiKey("ffk_bad_key");

    (global.fetch as any).mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => "Unauthorized",
    });

    await expect(api.getTraces()).rejects.toThrow("Unauthorized: missing or invalid API key");
  });

  it("throws on other non-ok status with body", async () => {
    setApiKey("ffk_test");

    (global.fetch as any).mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "Internal server error",
    });

    await expect(api.getTraces()).rejects.toThrow("API error 500: Internal server error");
  });

  it("throws on non-ok status without body", async () => {
    setApiKey("ffk_test");

    (global.fetch as any).mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      text: async () => "",
    });

    await expect(api.getTraces()).rejects.toThrow("API error 404: Not Found");
  });

  it("getTrace encodes traceId in URL", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ trace_id: "t1" }) });

    await api.getTrace("trace with spaces");

    expect(global.fetch).toHaveBeenCalledWith(
      "/traces/trace%20with%20spaces",
      expect.any(Object)
    );
  });

  it("confirmDiagnosis sends override fields", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ diagnosis_id: "d1" }) });

    await api.confirmDiagnosis("diag_123", true, "extraction_hallucination", "human review", "extraction");

    const [, options] = (global.fetch as any).mock.calls[0];
    const body = JSON.parse(options.body);
    expect(body).toEqual({
      confirmed: true,
      override_category: "extraction_hallucination",
      override_reason: "human review",
      override_step: "extraction",
    });
  });

  it("runDiagnosis uses POST", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ diagnosis_id: "d1" }) });

    await api.runDiagnosis("trace_456");

    expect(global.fetch).toHaveBeenCalledWith(
      "/traces/trace_456/diagnose",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("flagTrace includes note", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ flag_id: "f1" }) });

    await api.flagTrace("trace_789", "extraction failed");

    const [, options] = (global.fetch as any).mock.calls[0];
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      user_id: "web_inspector",
      note: "extraction failed",
    });
  });

  it("triggerEvalRun uses POST", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ run_id: "run_1" }) });

    await api.triggerEvalRun();

    expect(global.fetch).toHaveBeenCalledWith(
      "/eval/run",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("runDemoPipeline uses POST", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ status: "ok", traces_saved: 15 }) });

    await api.runDemoPipeline();

    expect(global.fetch).toHaveBeenCalledWith(
      "/pipeline/run-demo",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("runPipeline posts single document payload", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ status: "completed", trace_id: "t_custom_1" }),
    });

    const res = await api.runPipeline({
      doc_id: "invoice_demo_1",
      doc_text: "INVOICE #101 Total: $500.00",
      expected_type: "invoice",
    });

    expect(res.trace_id).toBe("t_custom_1");
    const [url, options] = (global.fetch as any).mock.calls[0];
    expect(url).toBe("/pipeline/run");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      doc_id: "invoice_demo_1",
      doc_text: "INVOICE #101 Total: $500.00",
      expected_type: "invoice",
    });
  });

  it("runFaultBenchmark posts to /analytics/benchmark", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({
        baselines_scored: 10,
        step_accuracy: 1.0,
        category_accuracy: 1.0,
        results: [],
      }),
    });

    const res = await api.runFaultBenchmark();
    expect(res.step_accuracy).toBe(1.0);
    expect(global.fetch).toHaveBeenCalledWith(
      "/analytics/benchmark",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("getRuntimeStatus fetches /runtime", async () => {
    setApiKey("ffk_test");
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        dry_run: false,
        pipeline_model: "minimaxai/minimax-m3.1-flash",
        pipeline_provider: "nvidia_nim",
      }),
    });

    const res = await api.getRuntimeStatus();
    expect(res.dry_run).toBe(false);
    expect(res.pipeline_model).toBe("minimaxai/minimax-m3.1-flash");
    expect(global.fetch).toHaveBeenCalledWith("/runtime", expect.any(Object));
  });
});
