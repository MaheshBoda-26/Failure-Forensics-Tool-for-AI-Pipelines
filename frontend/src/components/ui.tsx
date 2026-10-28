import React from "react";

export type Tone = "pass" | "fail" | "warn" | "neutral";

export const StatusPill: React.FC<{ tone: Tone; children: React.ReactNode }> = ({
  tone,
  children,
}) => (
  <span className={`pill pill--${tone}`}>
    <span className="pill__dot" aria-hidden="true" />
    {children}
  </span>
);

export const Metric: React.FC<{
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  unit?: string;
  negative?: boolean;
}> = ({ label, value, hint, unit, negative }) => (
  <div className="metric">
    <div className="metric__label">{label}</div>
    <div className={`metric__value${negative ? " is-negative" : ""}`}>
      {value}
      {unit ? <span className="metric__unit">{unit}</span> : null}
    </div>
    {hint ? <div className="metric__hint">{hint}</div> : null}
  </div>
);

export const SectionHead: React.FC<{
  title: string;
  hint?: string;
  action?: React.ReactNode;
}> = ({ title, hint, action }) => (
  <div className="section__head">
    <div>
      <h2 className="section__title">{title}</h2>
      {hint ? <p className="section__hint">{hint}</p> : null}
    </div>
    {action}
  </div>
);

export const Empty: React.FC<{
  children: React.ReactNode;
  action?: React.ReactNode;
}> = ({ children, action }) => (
  <div className="empty">
    <p>{children}</p>
    {action}
  </div>
);

/** The product's mark: a four-node spine with the third node broken. */
export const SpineMark: React.FC<{ size?: number }> = ({ size = 24 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <path d="M6 4v16" stroke="var(--copper)" strokeWidth="1.6" strokeLinecap="round" />
    <circle cx="6" cy="6" r="2" fill="var(--surface)" stroke="var(--copper)" strokeWidth="1.8" />
    <circle cx="6" cy="12" r="2.2" fill="var(--cinnabar)" stroke="var(--cinnabar)" strokeWidth="1.8" />
    <circle cx="6" cy="18" r="2" fill="var(--surface)" stroke="var(--copper)" strokeWidth="1.8" />
    <path d="M12 7h7M12 12h4M12 17h7" stroke="var(--faint)" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);
