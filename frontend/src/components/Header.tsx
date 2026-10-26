import React, { useState } from "react";
import { Activity, Play, CheckCircle2, AlertCircle } from "lucide-react";
import { clearApiKey, setApiKey } from "../api/client";

export type NavTab = "home" | "explorer" | "dag" | "analytics";

interface HeaderProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  backendOnline: boolean;
  onRunDemo: () => Promise<void>;
  isRunningDemo: boolean;
  /** Re-fetch after the key changes, so a 401 state clears without a reload. */
  onApiKeyChange: () => Promise<void>;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  onTabChange,
  backendOnline,
  onRunDemo,
  isRunningDemo,
  onApiKeyChange,
}) => {
  const [keyDraft, setKeyDraft] = useState("");
  const [keySaved, setKeySaved] = useState(false);

  return (
    <header
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "16px 32px",
        borderBottom: "1px solid var(--color-rule)",
        backgroundColor: "var(--color-ground)",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "36px" }}>
        {/* Brand */}
        <div
          onClick={() => onTabChange("home")}
          style={{
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "10px",
          }}
        >
          <div
            style={{
              width: "28px",
              height: "28px",
              border: "2px solid var(--color-copper)",
              borderRadius: "var(--radius-1)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontFamily: "var(--font-display)",
              fontSize: "14px",
              color: "var(--color-copper)",
            }}
          >
            FF
          </div>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "20px",
              fontWeight: 800,
              letterSpacing: "var(--tracking-tight)",
              color: "var(--color-ink)",
            }}
          >
            Failure Forensics
          </span>
        </div>

        {/* Navigation Tabs */}
        <nav style={{ display: "flex", gap: "24px", fontFamily: "var(--font-mono)", fontSize: "12px" }}>
          {[
            { id: "home", label: "Overview" },
            { id: "explorer", label: "Trace explorer" },
            { id: "dag", label: "DAG graph and diff" },
            { id: "analytics", label: "Eval and analytics" },
          ].map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => onTabChange(tab.id as NavTab)}
                style={{
                  background: "none",
                  border: "none",
                  padding: "6px 0",
                  cursor: "pointer",
                  color: isActive ? "var(--color-ink)" : "var(--color-ink-3)",
                  fontWeight: isActive ? 600 : 400,
                  borderBottom: isActive ? "2px solid var(--color-copper)" : "2px solid transparent",
                  letterSpacing: "0.04em",
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Right Controls */}
      <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
        {/* Status Indicator */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            color: backendOnline ? "var(--color-copper)" : "var(--color-cinnabar)",
            padding: "4px 8px",
            backgroundColor: backendOnline ? "var(--color-copper-tint)" : "var(--color-cinnabar-tint)",
            borderRadius: "var(--radius-1)",
            border: `1px solid ${backendOnline ? "var(--color-copper)" : "var(--color-cinnabar)"}`,
          }}
        >
          {backendOnline ? (
            <CheckCircle2 size={12} strokeWidth={2.5} />
          ) : (
            <AlertCircle size={12} strokeWidth={2.5} />
          )}
          <span>{backendOnline ? "API connected" : "Offline / local"}</span>
        </div>

        {/* API Key Input */}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = keyDraft.trim();
            // An empty submit clears the stored key. It used to `return` early,
            // so a wrong key could never be removed without devtools.
            if (trimmed) {
              setApiKey(trimmed);
            } else {
              clearApiKey();
            }
            setKeyDraft("");
            setKeySaved(true);
            window.setTimeout(() => setKeySaved(false), 2000);
            // Re-fetch so a newly valid key (or a cleared one) is reflected in
            // the UI immediately instead of after a full page reload.
            void onApiKeyChange();
          }}
          title="Stored in this browser only; sent as an Authorization header. Submit empty to clear."
          style={{ display: "flex", gap: "6px", alignItems: "center" }}
        >
          <input
            type="password"
            value={keyDraft}
            onChange={(event) => setKeyDraft(event.target.value)}
            placeholder={keySaved ? "key saved" : "API key (ffk_…)"}
            aria-label="API key"
            autoComplete="off"
            spellCheck={false}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              padding: "4px 8px",
              width: "150px",
              color: "var(--color-ink)",
              backgroundColor: "var(--color-ground)",
              border: "1px solid var(--color-rule-strong)",
              borderRadius: "var(--radius-1)",
            }}
          />
          <button
            type="submit"
            // Never disabled: submitting with an empty field is how a stored
            // key is cleared.
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              padding: "4px 10px",
              color: "var(--color-copper)",
              backgroundColor: "transparent",
              border: "1px solid var(--color-copper)",
              borderRadius: "var(--radius-1)",
              cursor: "pointer",
            }}
          >
            {keySaved ? "SAVED" : keyDraft.trim() ? "SET KEY" : "CLEAR"}
          </button>
        </form>

        {/* Run Demo Button */}
        <button
          onClick={onRunDemo}
          disabled={isRunningDemo}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontFamily: "var(--font-mono)",
            fontSize: "12px",
            fontWeight: 600,
            color: "var(--color-ground)",
            backgroundColor: isRunningDemo ? "var(--color-ink-3)" : "var(--color-copper)",
            border: `1px solid ${isRunningDemo ? "var(--color-ink-3)" : "var(--color-copper)"}`,
            borderRadius: "var(--radius-1)",
            padding: "7px 14px",
            cursor: isRunningDemo ? "wait" : "pointer",
          }}
        >
          <Play size={12} fill="currentColor" />
          <span>{isRunningDemo ? "Running pipeline..." : "Run demo"}</span>
        </button>
      </div>
    </header>
  );
};
