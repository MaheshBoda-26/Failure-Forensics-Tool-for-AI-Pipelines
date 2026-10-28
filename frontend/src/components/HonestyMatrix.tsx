import React from "react";

const ROWS = [
  {
    capability: "Root cause",
    guarantee: "Earliest failing check in the chain",
    limit: "Ambiguous when independent stages fail",
  },
  {
    capability: "Cascade",
    guarantee: "Downstream spans marked tainted",
    limit: "No counterfactual re-runs",
  },
  {
    capability: "Telemetry",
    guarantee: "Prompts, responses, tokens, timings",
    limit: "Needs instrumentation around model calls",
  },
  {
    capability: "Eval gate",
    guarantee: "Golden suites compare baseline and release",
    limit: "SQLite storage only in v0",
  },
];

export const HonestyMatrix: React.FC = () => (
  <div className="panel table-wrap">
    <table className="table honesty">
      <caption className="sr-only">Guarantees and limits</caption>
      <thead>
        <tr>
          <th scope="col">Capability</th>
          <th scope="col">Guarantee</th>
          <th scope="col">Limit</th>
        </tr>
      </thead>
      <tbody>
        {ROWS.map((row) => (
          <tr key={row.capability}>
            <td data-label="Capability">{row.capability}</td>
            <td data-label="Guarantee">{row.guarantee}</td>
            <td data-label="Limit">{row.limit}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);
