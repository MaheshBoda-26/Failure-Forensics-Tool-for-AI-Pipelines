import React from "react";
import "../landing.css";

/**
 * The marketing page. It is the front door at `#/`; every action leads into the
 * workspace at `#/overview`, so the page never claims a feature the app lacks.
 *
 * Numbers on this page are the product's own: the stage names, the failing
 * check and the limits all come from the analyzer and the honesty matrix.
 */
const STAGES = [
  { name: "Intake", check: "Normalize the document", state: "passed" },
  { name: "Extraction", check: "Entities must appear verbatim", state: "passed" },
  { name: "Classification", check: "Label must match the document", state: "failed" },
  { name: "Summarization", check: "Summary stays inside the facts", state: "tainted" },
] as const;

const USES = [
  {
    title: "Debugging a bad answer",
    line: "Open the trace and read the check that failed.",
  },
  {
    title: "Gating a release",
    line: "Golden suites compare a baseline against the current build.",
  },
  {
    title: "Auditing a run",
    line: "Prompt, response, tokens and timings, stored per span.",
  },
  {
    title: "Swapping a model",
    line: "Run new documents through a model and name the regression.",
  },
];

const LIMITS = [
  {
    capability: "Root cause",
    guarantee: "The earliest failing check in the chain",
    limit: "Ambiguous when independent stages fail at once",
  },
  {
    capability: "Cascade",
    guarantee: "Downstream spans marked tainted",
    limit: "No counterfactual re-runs",
  },
  {
    capability: "Telemetry",
    guarantee: "Prompts, responses, tokens and timings",
    limit: "Needs instrumentation around model calls",
  },
  {
    capability: "Eval gate",
    guarantee: "Golden suites on fixed seeds",
    limit: "SQLite storage only in this version",
  },
];

const NEEDS = [
  { label: "Runtime", value: "Python 3.12" },
  { label: "Interface", value: "Node 22" },
  { label: "Storage", value: "SQLite" },
  { label: "Model", value: "NVIDIA NIM key" },
];

export const Landing: React.FC = () => (
  <div className="lp">
    <a className="lp-skip" href="#traced">
      Skip to the trace
    </a>

    <header className="lp-mast">
      <div className="lp-container">
        <p className="lp-wordmark">Failure Forensics</p>
        <nav className="lp-nav" aria-label="Page">
          <a href="#how">How it works</a>
          <a href="#product">The product</a>
          <a href="#uses">Use cases</a>
          <a href="#limits">Limits</a>
        </nav>
      </div>
    </header>

    <main>
      <section className="lp-hero" aria-labelledby="lp-hero-title">
        <div className="lp-container">
          <div className="lp-hero__text">
            <h1 className="lp-display" id="lp-hero-title">
              Which stage broke your pipeline?
            </h1>
            <p className="lp-lead">Deterministic root cause for multi-step LLM pipelines.</p>
            <div className="lp-actions">
              <a className="lp-btn lp-btn--primary" href="#/overview">
                Open the trace explorer
              </a>
              <a className="lp-btn" href="#traced">
                See a failure traced
              </a>
            </div>
          </div>

          <figure className="lp-map">
            <div className="lp-map__head">
              <span className="lp-mono">mixed_001</span>
              <span>4 spans</span>
              <span className="lp-map__verdict">Failure</span>
            </div>
            <ol className="lp-map__list">
              {STAGES.map((stage) => (
                <li className={`lp-map__row is-${stage.state}`} key={stage.name}>
                  <span className="lp-map__mark" aria-hidden="true" />
                  <span className="lp-map__name">{stage.name}</span>
                  <span className="lp-map__state">
                    {stage.state === "failed"
                      ? "Root cause"
                      : stage.state === "tainted"
                        ? "Tainted"
                        : "Passed"}
                  </span>
                </li>
              ))}
            </ol>
            <figcaption className="lp-map__foot">
              <span className="lp-map__label">Evidence at classification</span>
              <span className="lp-map__finding">
                Classification &lsquo;contract&rsquo; does not match ground truth
                &lsquo;mixed&rsquo;
              </span>
              <span className="lp-map__confidence">90% confidence</span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section className="lp-section" id="traced" aria-labelledby="lp-traced-title">
        <div className="lp-container">
          <div className="lp-head">
            <h2 className="lp-title" id="lp-traced-title">
              The final answer says nothing.
            </h2>
            <p className="lp-section__lead">
              A wrong output is a symptom. The tool reports the stage that produced it.
            </p>
          </div>

          <div className="lp-compare">
            <div className="lp-pane lp-pane--plain">
              <h3>Without a trace</h3>
              <dl className="lp-facts">
                <dt>Output</dt>
                <dd>
                  <span className="lp-mono">{`{ "document_type": "contract" }`}</span>
                </dd>
                <dt>Stage</dt>
                <dd>Unknown</dd>
                <dt>Category</dt>
                <dd>Unknown</dd>
                <dt>Evidence</dt>
                <dd>None recorded</dd>
              </dl>
            </div>

            <div className="lp-pane lp-pane--edge">
              <h3>With the trace</h3>
              <dl className="lp-facts">
                <dt>Stage</dt>
                <dd>Classification, 3 of 4</dd>
                <dt>Category</dt>
                <dd>Misclassification</dd>
                <dt>Check</dt>
                <dd>Label grounding</dd>
                <dt>Evidence</dt>
                <dd>
                  Classification &lsquo;contract&rsquo; does not match ground truth
                  &lsquo;mixed&rsquo;
                </dd>
              </dl>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section lp-section--band" id="how" aria-labelledby="lp-how-title">
        <div className="lp-container">
          <div className="lp-head">
            <h2 className="lp-title" id="lp-how-title">
              Four stages, four checks.
            </h2>
            <p className="lp-section__lead">
              Each span is checked where it lands, before the next stage sees it.
            </p>
          </div>

          <ol className="lp-stages">
            {STAGES.map((stage) => (
              <li className="lp-stage" key={stage.name}>
                <h3 className="lp-stage__name">{stage.name}</h3>
                <p className="lp-stage__line">{stage.check}</p>
              </li>
            ))}
          </ol>

          <p className="lp-note">
            The analyzer walks backward from the last span and names the earliest failing
            check. Everything after it is marked tainted, so a cascade never reads as four
            separate bugs.
          </p>
        </div>
      </section>

      <section className="lp-section" id="product" aria-labelledby="lp-product-title">
        <div className="lp-container">
          <div className="lp-head">
            <h2 className="lp-title" id="lp-product-title">
              The trace, opened.
            </h2>
            <p className="lp-section__lead">
              One trace, four spans, every check recorded. This is the app, not a mockup.
            </p>
          </div>

          <figure className="lp-shot lp-shot--lead">
            <img
              src="/shots/trace.jpg"
              width="1440"
              height="900"
              alt="Trace explorer showing the four-stage spine, the diagnosis panel and the failing check at classification"
              loading="lazy"
            />
            <figcaption>
              Trace 2e832ca6a622. Classification is marked as the root cause, summarization as
              tainted, and both failing checks are named.
            </figcaption>
          </figure>

          <div className="lp-shots">
            <figure className="lp-shot">
              <img
                src="/shots/graph.jpg"
                width="1440"
                height="900"
                alt="Execution graph with the failing link drawn between extraction and classification"
                loading="lazy"
              />
              <figcaption>Stage graph, with the first break marked.</figcaption>
            </figure>
            <figure className="lp-shot">
              <img
                src="/shots/analytics.jpg"
                width="1440"
                height="900"
                alt="Analytics view with failures by stage and a stage by category correlation matrix"
                loading="lazy"
              />
              <figcaption>Failures by stage, and where they cluster.</figcaption>
            </figure>
          </div>
        </div>
      </section>

      <section className="lp-section" id="uses" aria-labelledby="lp-uses-title">
        <div className="lp-container lp-uses">
          <div className="lp-uses__head">
            <h2 className="lp-title" id="lp-uses-title">
              Where it earns its keep.
            </h2>
            <p className="lp-section__lead">Four jobs, one database of runs.</p>
          </div>

          <ul className="lp-uses__list">
            {USES.map((use) => (
              <li className="lp-use" key={use.title}>
                <h3>{use.title}</h3>
                <p>{use.line}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="lp-section" id="limits" aria-labelledby="lp-limits-title">
        <div className="lp-container">
          <div className="lp-head">
            <h2 className="lp-title" id="lp-limits-title">
              What it does, and its limits.
            </h2>
            <p className="lp-section__lead">
              A diagnostic tool is only useful if it says where it stops.
            </p>
          </div>

          <table className="lp-table">
            <caption className="lp-sr">Guarantees and limits</caption>
            <thead>
              <tr>
                <th scope="col">Capability</th>
                <th scope="col">Guarantee</th>
                <th scope="col">Limit</th>
              </tr>
            </thead>
            <tbody>
              {LIMITS.map((row) => (
                <tr key={row.capability}>
                  <td data-label="Capability">{row.capability}</td>
                  <td data-label="Guarantee">{row.guarantee}</td>
                  <td data-label="Limit">{row.limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="lp-section lp-section--ink" aria-labelledby="lp-needs-title">
        <div className="lp-container lp-needs">
          <div>
            <h2 className="lp-title" id="lp-needs-title">
              Runs on your machine.
            </h2>
            <p className="lp-section__lead">
              No hosted service. Traces, the database and the model key stay on the box.
            </p>
          </div>

          <div>
            <dl className="lp-needs__list">
              {NEEDS.map((need) => (
                <div className="lp-needs__item" key={need.label}>
                  <dt>{need.label}</dt>
                  <dd>{need.value}</dd>
                </div>
              ))}
            </dl>
            <pre className="lp-cmd">{`make install\nmake run-api`}</pre>
          </div>
        </div>
      </section>

      <section className="lp-section" id="open" aria-labelledby="lp-open-title">
        <div className="lp-container lp-close">
          <div className="lp-close__text">
            <h2 className="lp-title" id="lp-open-title">
              Open it and follow one trace.
            </h2>
            <p className="lp-section__lead">
              The demo dataset is already loaded. Pick a failure and read the stage.
            </p>
            <div className="lp-actions">
              <a className="lp-btn lp-btn--primary" href="#/overview">
                Open the trace explorer
              </a>
            </div>
          </div>

          <figure className="lp-shot lp-close__shot">
            <img
              src="/shots/overview.jpg"
              width="1440"
              height="900"
              alt="Overview with pass rate, the latest failure's four-stage spine and failures by stage"
              loading="lazy"
            />
            <figcaption>The overview, on the demo dataset.</figcaption>
          </figure>
        </div>
      </section>
    </main>

    <footer className="lp-foot">
      <div className="lp-container lp-foot__grid">
        <div>
          <p className="lp-wordmark">Failure Forensics</p>
          <p className="lp-foot__line">Root cause localization for multi-step LLM pipelines.</p>
        </div>
        <nav className="lp-foot__nav" aria-label="Footer">
          <a href="#how">How it works</a>
          <a href="#product">The product</a>
          <a href="#uses">Use cases</a>
          <a href="#limits">Limits</a>
        </nav>
        <p className="lp-foot__colophon">
          FastAPI, React, SQLite. Demo dataset of 15 documents. Screenshots taken from this
          build.
        </p>
      </div>
    </footer>
  </div>
);
