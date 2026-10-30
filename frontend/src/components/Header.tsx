import React, { useEffect, useId, useState } from "react";
import {
  BarChart2,
  FilePlus2,
  Gauge,
  GitBranch,
  KeyRound,
  Menu,
  Play,
  RefreshCw,
  Rows3,
  Upload,
  X,
} from "lucide-react";
import { clearApiKey, setApiKey } from "../api/client";
import type { DocumentType, PipelineRunRequest, RuntimeStatus } from "../api/types";
import { SpineMark, StatusPill } from "./ui";

export type NavTab = "home" | "explorer" | "dag" | "analytics";

interface HeaderProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  backendOnline: boolean;
  runtime?: RuntimeStatus | null;
  onRunDemo: () => Promise<void>;
  isRunningDemo: boolean;
  onRunDocument: (payload: PipelineRunRequest) => Promise<void>;
  isRunningDocument: boolean;
  /** Re-fetch after the key changes, so a 401 state clears without a reload. */
  onApiKeyChange: () => Promise<void>;
  lastUpdated: Date | null;
}

const TABS: Array<{ id: NavTab; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: "home", label: "Overview", icon: Gauge },
  { id: "explorer", label: "Traces", icon: Rows3 },
  { id: "dag", label: "Execution graph", icon: GitBranch },
  { id: "analytics", label: "Analytics", icon: BarChart2 },
];

const SAMPLE_PRESETS: Array<{
  label: string;
  doc_id: string;
  expected_type: DocumentType;
  doc_text: string;
}> = [
  {
    label: "Invoice (clean)",
    doc_id: "invoice_001",
    expected_type: "invoice",
    doc_text:
      "INVOICE #INV-2026-0412\nDate: 2026-10-15\nVendor: Northwind Cloud Systems LLC\nBill To: Apex Manufacturing Corp\n\nLine Items:\n1. Enterprise Pipeline Audit License — $4,200.00\n2. Dedicated Worker Node Support — $800.00\n\nSubtotal: $5,000.00\nTax (8%): $400.00\nTotal Due: $5,400.00\nPayment Terms: Net 30 days.",
  },
  {
    label: "Invoice (hallucination fault)",
    doc_id: "invoice_003",
    expected_type: "invoice",
    doc_text:
      "INVOICE #INV-2026-0889\nDate: 2026-10-18\nFrom: Meridian Data Services\nTo: Horizon Logistics Ltd\n\nDescription: Quarterly data ingestion & archival tier\nSubtotal: $2,500.00\nTax: $200.00\nTotal Due: $2,700.00\nDue Date: 2026-11-18",
  },
  {
    label: "Contract (misclassification fault)",
    doc_id: "contract_001",
    expected_type: "contract",
    doc_text:
      "MASTER SERVICES AGREEMENT\nEffective Date: 2026-10-01\nBetween: Sterling Legal Partners LLP (\"Provider\") and Vanguard Biotech Inc. (\"Client\").\n\n1. Scope of Services: Provider shall deliver regulatory compliance advisory services.\n2. Term and Termination: This Agreement remains in force until 2027-10-01 unless terminated with 30 days written notice.\n3. Indemnification: Each party agrees to indemnify and hold harmless the other party from third-party claims.",
  },
  {
    label: "Report (context loss fault)",
    doc_id: "report_003",
    expected_type: "report",
    doc_text:
      "Q3 INCIDENT & RELIABILITY REPORT\nDate: 2026-10-20\nPrepared by: Site Reliability Engineering, Helios AI\n\nExecutive Summary & Findings:\nAcross 42,000 production pipeline runs in Q3, mean latency dropped by 18% to 640ms while schema validation catches improved to 99.4%.\n\nMethodology:\nEvery span was evaluated by seven deterministic checks before release.\n\nRecommendations:\n1. Pin cross-family judge models.\n2. Enforce per-tenant token bucket limits.",
  },
];

const ApiKeyForm: React.FC<{
  onSaved: () => Promise<void>;
  autoFocus?: boolean;
}> = ({ onSaved, autoFocus }) => {
  const inputId = useId();
  const hintId = useId();
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(false);

  return (
    <form
      className="key-form"
      title="Stored in this browser only, sent as an Authorization header. Submit empty to clear."
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = draft.trim();
        // An empty submit clears the stored key, so a wrong key can always be removed.
        if (trimmed) {
          setApiKey(trimmed);
        } else {
          clearApiKey();
        }
        setDraft("");
        setSaved(true);
        window.setTimeout(() => setSaved(false), 2000);
        void onSaved();
      }}
    >
      <label htmlFor={inputId}>API key</label>
      <input
        id={inputId}
        className="input"
        type="password"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="ffk_…"
        aria-describedby={hintId}
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
      />
      <p className="hint" id={hintId}>
        Stored in this browser. Submit empty to clear.
      </p>
      <button className="btn btn--sm" type="submit">
        {saved ? "Saved" : draft.trim() ? "Save key" : "Clear key"}
      </button>
    </form>
  );
};

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  onTabChange,
  backendOnline,
  runtime,
  onRunDemo,
  isRunningDemo,
  onRunDocument,
  isRunningDocument,
  onApiKeyChange,
  lastUpdated,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [docId, setDocId] = useState("custom_doc_001");
  const [expectedType, setExpectedType] = useState<DocumentType | "">("invoice");
  const [docText, setDocText] = useState(SAMPLE_PRESETS[0].doc_text);
  const [runError, setRunError] = useState<string | null>(null);

  const shortModelName = runtime?.pipeline_model
    ? runtime.pipeline_model.split("/").pop() ?? runtime.pipeline_model
    : null;

  const docIdInputId = useId();
  const expectedTypeInputId = useId();
  const docTextInputId = useId();
  const fileInputId = useId();

  useEffect(() => {
    if (!menuOpen && !keyOpen && !runModalOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        setKeyOpen(false);
        setRunModalOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, keyOpen, runModalOpen]);

  const go = (tab: NavTab) => {
    setMenuOpen(false);
    onTabChange(tab);
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const safeStem = file.name
      .replace(/\.[^.]+$/, "")
      .replace(/[^A-Za-z0-9_-]/g, "_")
      .slice(0, 64);
    if (safeStem) {
      setDocId(safeStem);
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setDocText(reader.result);
      }
    };
    reader.readAsText(file);
  };

  const handleSubmitDocument = async (event: React.FormEvent) => {
    event.preventDefault();
    setRunError(null);
    const cleanedId = docId.trim().replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 64);
    const cleanedText = docText.trim();
    if (!cleanedId) {
      setRunError("Enter a valid document ID (letters, numbers, underscores or hyphens).");
      return;
    }
    if (!cleanedText) {
      setRunError("Document text cannot be empty.");
      return;
    }
    try {
      await onRunDocument({
        doc_id: cleanedId,
        doc_text: cleanedText,
        expected_type: expectedType || null,
      });
      setRunModalOpen(false);
      setMenuOpen(false);
    } catch (err: unknown) {
      setRunError((err as Error).message);
    }
  };

  const brand = (
    <button className="brand" onClick={() => go("home")} aria-label="Failure Forensics, overview">
      <span className="brand__mark">
        <SpineMark />
      </span>
      <span className="brand__name">Failure Forensics</span>
    </button>
  );

  const nav = (
    <nav className="nav" aria-label="Main">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            className="nav-item"
            onClick={() => go(tab.id)}
            aria-current={activeTab === tab.id ? "page" : undefined}
          >
            <Icon size={16} />
            <span>{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );

  return (
    <>
      <header className="topbar">
        <div className="topbar__left">
          <button
            className="icon-btn menu-btn"
            onClick={() => setMenuOpen(true)}
            aria-label="Open menu"
            aria-expanded={menuOpen}
          >
            <Menu size={18} />
          </button>
          {brand}
          <span className="scope">Local workspace</span>
          {runtime ? (
            <StatusPill tone={runtime.dry_run ? "warn" : "pass"}>
              {runtime.dry_run
                ? "Offline fixture"
                : `Live NIM · ${shortModelName ?? runtime.pipeline_provider}`}
            </StatusPill>
          ) : null}
        </div>

        <div className="topbar__right">
          {lastUpdated ? (
            <span className="freshness">
              Updated{" "}
              {lastUpdated.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
            </span>
          ) : null}

          <span className={`status${backendOnline ? "" : " is-offline"}`}>
            <span className="status__dot" aria-hidden="true" />
            <span className="status__text">{backendOnline ? "API connected" : "API offline"}</span>
          </span>

          <button
            className="btn btn--ghost btn--sm"
            onClick={() => setRunModalOpen(true)}
            aria-label="Run custom document"
          >
            <FilePlus2 size={14} />
            <span className="hide-sm">Test document</span>
          </button>

          <button
            className="btn btn--ghost btn--sm"
            onClick={() => setKeyOpen((open) => !open)}
            aria-expanded={keyOpen}
            aria-label="API key"
          >
            <KeyRound size={14} />
            <span className="hide-sm">API key</span>
          </button>

          {keyOpen ? (
            <>
              <div
                className="popover-backdrop"
                onClick={() => setKeyOpen(false)}
                aria-hidden="true"
              />
              <div className="popover" role="dialog" aria-label="API key">
                <ApiKeyForm
                  onSaved={async () => {
                    await onApiKeyChange();
                  }}
                  autoFocus
                />
              </div>
            </>
          ) : null}
        </div>
      </header>

      <aside className="sidebar">
        {nav}
        <div className="sidebar__foot">
          <button
            className="btn btn--primary btn--block"
            onClick={() => setRunModalOpen(true)}
            disabled={isRunningDocument}
          >
            <FilePlus2 size={14} />
            <span>{isRunningDocument ? "Running document" : "Test document"}</span>
          </button>
          <button className="btn btn--block" onClick={onRunDemo} disabled={isRunningDemo}>
            {isRunningDemo ? (
              <RefreshCw size={14} className="spin" />
            ) : (
              <Play size={13} fill="currentColor" />
            )}
            <span>{isRunningDemo ? "Running demo" : "Run 15-doc demo"}</span>
          </button>
          <p className="sidebar__meta">
            Pipeline v{runtime?.pipeline_version ?? "0.1"}
            {shortModelName ? ` · ${shortModelName}` : ""}
          </p>
        </div>
      </aside>

      {menuOpen ? (
        <div className="menu-sheet" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="menu-sheet__head">
            {brand}
            <button
              className="icon-btn"
              onClick={() => setMenuOpen(false)}
              aria-label="Close menu"
            >
              <X size={18} />
            </button>
          </div>

          <div className="menu-nav">{nav}</div>

          <div className="menu-sheet__foot">
            <span className={`status${backendOnline ? "" : " is-offline"}`}>
              <span className="status__dot" aria-hidden="true" />
              <span>{backendOnline ? "API connected" : "API offline"}</span>
            </span>

            {runtime ? (
              <div>
                <StatusPill tone={runtime.dry_run ? "warn" : "pass"}>
                  {runtime.dry_run
                    ? "Offline fixture"
                    : `Live NIM · ${shortModelName ?? runtime.pipeline_provider}`}
                </StatusPill>
              </div>
            ) : null}

            <ApiKeyForm
              onSaved={async () => {
                await onApiKeyChange();
              }}
            />

            <button
              className="btn btn--primary btn--block"
              onClick={() => {
                setMenuOpen(false);
                setRunModalOpen(true);
              }}
              disabled={isRunningDocument}
            >
              <FilePlus2 size={14} />
              <span>{isRunningDocument ? "Running document" : "Test document"}</span>
            </button>

            <button
              className="btn btn--block"
              onClick={onRunDemo}
              disabled={isRunningDemo}
            >
              {isRunningDemo ? (
                <RefreshCw size={14} className="spin" />
              ) : (
                <Play size={13} fill="currentColor" />
              )}
              <span>{isRunningDemo ? "Running demo" : "Run 15-doc demo"}</span>
            </button>
            <p className="sidebar__meta">
              Pipeline v{runtime?.pipeline_version ?? "0.1"}
              {shortModelName ? ` · ${shortModelName}` : ""}
            </p>
          </div>
        </div>
      ) : null}

      {runModalOpen ? (
        <>
          <div
            className="modal-backdrop"
            onClick={() => setRunModalOpen(false)}
            aria-hidden="true"
          />
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="run-doc-title"
          >
            <div className="panel__head">
              <div>
                <h2 className="panel__title" id="run-doc-title">
                  Run pipeline on a document
                </h2>
                <p className="panel__hint">
                  Executes Intake → Extraction → Classification → Summarization and runs deterministic forensics checks.
                </p>
              </div>
              <button
                className="icon-btn"
                onClick={() => setRunModalOpen(false)}
                aria-label="Close modal"
              >
                <X size={16} />
              </button>
            </div>

            <form className="panel__body stack" onSubmit={handleSubmitDocument}>
              <div>
                <p className="scores__label">Load preset or file</p>
                <div className="page-actions" style={{ justifyContent: "flex-start" }}>
                  {SAMPLE_PRESETS.map((preset) => (
                    <button
                      key={preset.doc_id}
                      type="button"
                      className="btn btn--sm"
                      onClick={() => {
                        setDocId(preset.doc_id);
                        setExpectedType(preset.expected_type);
                        setDocText(preset.doc_text);
                        setRunError(null);
                      }}
                    >
                      {preset.label}
                    </button>
                  ))}
                  <label className="btn btn--sm" htmlFor={fileInputId}>
                    <Upload size={12} /> Upload .txt
                  </label>
                  <input
                    id={fileInputId}
                    type="file"
                    accept=".txt,text/plain"
                    className="sr-only"
                    onChange={handleFileUpload}
                  />
                </div>
              </div>

              <div className="payload__grid">
                <div className="key-form">
                  <label htmlFor={docIdInputId}>Document ID</label>
                  <input
                    id={docIdInputId}
                    className="input mono"
                    type="text"
                    value={docId}
                    onChange={(event) => setDocId(event.target.value)}
                    placeholder="custom_doc_001"
                    required
                  />
                </div>

                <div className="key-form">
                  <label htmlFor={expectedTypeInputId}>Expected type (ground truth)</label>
                  <select
                    id={expectedTypeInputId}
                    className="input"
                    value={expectedType}
                    onChange={(event) =>
                      setExpectedType(event.target.value as DocumentType | "")
                    }
                  >
                    <option value="">None (unlabelled)</option>
                    <option value="contract">contract</option>
                    <option value="invoice">invoice</option>
                    <option value="report">report</option>
                    <option value="correspondence">correspondence</option>
                    <option value="mixed">mixed</option>
                  </select>
                </div>
              </div>

              <div className="key-form">
                <label htmlFor={docTextInputId}>Document text</label>
                <textarea
                  id={docTextInputId}
                  className="input textarea mono"
                  rows={8}
                  value={docText}
                  onChange={(event) => setDocText(event.target.value)}
                  placeholder="Paste contract, invoice, report, or correspondence text..."
                  required
                />
              </div>

              {runError ? (
                <p className="notice is-error" role="alert" style={{ margin: 0 }}>
                  {runError}
                </p>
              ) : null}

              <div className="panel__actions" style={{ marginTop: 0 }}>
                <button
                  type="submit"
                  className="btn btn--primary"
                  disabled={isRunningDocument}
                >
                  {isRunningDocument ? (
                    <RefreshCw size={14} className="spin" />
                  ) : (
                    <Play size={13} fill="currentColor" />
                  )}
                  <span>{isRunningDocument ? "Running pipeline" : "Run & diagnose"}</span>
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setRunModalOpen(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </>
      ) : null}
    </>
  );
};
