import React, { useEffect, useId, useState } from "react";
import {
  BarChart2,
  Gauge,
  GitBranch,
  KeyRound,
  Menu,
  Play,
  RefreshCw,
  Rows3,
  X,
} from "lucide-react";
import { clearApiKey, setApiKey } from "../api/client";
import { SpineMark } from "./ui";

export type NavTab = "home" | "explorer" | "dag" | "analytics";

interface HeaderProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  backendOnline: boolean;
  onRunDemo: () => Promise<void>;
  isRunningDemo: boolean;
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
  onRunDemo,
  isRunningDemo,
  onApiKeyChange,
  lastUpdated,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen && !keyOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        setKeyOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, keyOpen]);

  const go = (tab: NavTab) => {
    setMenuOpen(false);
    onTabChange(tab);
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
          <button className="btn btn--block" onClick={onRunDemo} disabled={isRunningDemo}>
            {isRunningDemo ? (
              <RefreshCw size={14} className="spin" />
            ) : (
              <Play size={13} fill="currentColor" />
            )}
            <span>{isRunningDemo ? "Running demo" : "Run demo"}</span>
          </button>
          <p className="sidebar__meta">Pipeline v0.1</p>
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

            <ApiKeyForm
              onSaved={async () => {
                await onApiKeyChange();
              }}
            />

            <button
              className="btn btn--primary btn--block"
              onClick={onRunDemo}
              disabled={isRunningDemo}
            >
              {isRunningDemo ? (
                <RefreshCw size={14} className="spin" />
              ) : (
                <Play size={13} fill="currentColor" />
              )}
              <span>{isRunningDemo ? "Running demo" : "Run demo"}</span>
            </button>
            <p className="sidebar__meta">Pipeline v0.1</p>
          </div>
        </div>
      ) : null}
    </>
  );
};
