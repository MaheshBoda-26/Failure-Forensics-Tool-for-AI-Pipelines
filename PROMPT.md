# Master Prompt: Plan the "Failure Forensics Tool for AI Pipelines"

> **How to use:** Fill in the `<builder_context>` block (Section 3), then paste everything from `=== BEGIN PROMPT ===` to `=== END PROMPT ===` into Claude Opus 5.5. If the reply is cut off by length limits, reply with `continue` and it will resume at the marked section.

=== BEGIN PROMPT ===

<role>
You are a staff-level AI engineer with 10+ years of experience building and operating production LLM systems at large AI labs and big-tech companies. You have built tracing, evaluation, and observability platforms for multi-step LLM pipelines (in the spirit of LangSmith, Braintrust, Arize Phoenix, and Langfuse), and you have personally debugged hundreds of "the final answer is wrong but nobody knows which step broke" incidents. You are also a pragmatic mentor: you are planning a project for a college student who must ship it in about 14 days, so you cut scope ruthlessly and never plan work that cannot be verified.
</role>

<mission>
Produce a single, complete, implementation-ready planning document for the project described below. The document must contain:

1. A Product Requirements Document (PRD)
2. A Technical Requirements Document (TRD)
3. A system architecture (with diagrams)
4. A phased, day-by-day build plan with verifiable acceptance criteria for every task

The reader will hand your plan to a coding agent (Claude Code) and execute it task by task, so every task must be specific, ordered, and testable. Do not write the application code. Include code only where it defines a contract: Pydantic model skeletons, SQL DDL, REST endpoint signatures, function signatures, prompt templates, and config files.
</mission>

<working_principles>
Follow these rules throughout. They are non-negotiable.

1. **Think before planning.** State your assumptions explicitly in a dedicated section. Where the brief is ambiguous, list the plausible interpretations, pick one, say why, and flag it. Do not silently choose. Do not stop to ask me questions; make a defensible choice, record it, and list anything that genuinely needs my input at the end.
2. **Simplicity first.** Plan the minimum system that fully delivers the brief. No speculative features, no abstractions used only once, no configurability nobody asked for. If a simpler design meets the requirement, use it and say so. Apply this test to every component: "Would a senior engineer call this overcomplicated for a 14-day solo or small-team college project?"
3. **Goal-driven tasks.** Every task you write must end with a concrete verification: a command to run, a test that must pass, a measurable number, or an observable behavior. Never write "make it work", "implement X", or "set up Y" without stating how we will know it is done.
4. **Surgical scope.** Every item in the plan must trace to a line in the project brief or to a requirement you explicitly justify as necessary for correctness (for example, prompt versioning is needed for regression tracking). Mark anything beyond the brief as `[ADDED]` with a one-line justification, and keep `[ADDED]` items few.
5. **Honesty over polish.** Where the brief's claims are weak or unmeasurable (see Section 4), say so and design a way to measure them. Do not invent benchmark numbers. Do not invent library APIs or model names: configure model names through environment variables, and mark any library API or behavior you are not certain about as `VERIFY BEFORE USE`.
6. **Production habits at student scale.** Typed interfaces, deterministic tests, secrets out of git, cost caps, and reproducibility are in scope. Kubernetes, message queues, auth systems, and multi-tenant concerns are out of scope. State this explicitly in the non-goals.
</working_principles>

<project_brief>
## Project: Failure Forensics Tool for AI Pipelines

**What it is.** An observability layer for multi-step AI pipelines. It traces every intermediate step, identifies exactly where a failure originates when the final output is bad, and feeds human-confirmed failures back into a growing evaluation dataset. Essentially a mini LangSmith / Braintrust.

**Why it matters.** When a multi-step AI pipeline produces garbage, most teams cannot tell which step broke. Being able to articulate why observability matters for AI systems is a senior-level signal. The project is a portfolio and interview piece.

**Original tech stack (from the brief)**

| Component | Tool / Library |
|---|---|
| Language | Python 3.11+ |
| Pipeline framework | Custom chain or LangChain |
| LLM provider | OpenRouter API (OpenAI-compatible `/chat/completions`, one key for many models) |
| Tracing | OpenTelemetry + custom spans |
| Storage | SQLite + JSON trace files |
| Visualization | React frontend or Streamlit (interactive trace explorer) |
| Feedback loop | Simple REST API (humans flag bad outputs) |
| Deployment | Render (backend + persistent disk for SQLite/JSON traces). No Docker, no containers. |

**Original build guide (6 phases, ~14 days)**

*Phase 1: Multi-step pipeline (Days 1-3).*
- 4-step pipeline: (1) Intake: accept raw text, markdown, or simulated PDF text. (2) Extraction: LLM extracts structured entities (names, dates, amounts, key terms). (3) Classification: classify as contract, invoice, report, or correspondence. (4) Summarization: structured summary tailored to document type.
- Each step is a clean, isolated function with typed input and output. Pydantic models for every intermediate structure. "If your steps are spaghetti, your tracing will be meaningless."
- Inject realistic failure modes: a contract with no dates, an invoice with amounts in different currencies, a document ambiguously between two categories.

*Phase 2: Tracing layer (Days 3-6).*
- A `Trace` object per execution with a unique `trace_id`, a list of `Span` objects (one per step), the final output, and a status (success / failure / degraded).
- Instrument each step with a context manager that captures: step name, serialized input, serialized output, LLM prompt sent, LLM raw response, token count, latency, and errors. Use a decorator so instrumenting a new step is one line.
- Confidence scoring: after each LLM call the model also outputs a self-confidence score (1-5), stored in the span. Low-confidence spans are primary suspects in backward tracing.
- Store each complete trace as JSON, and index it in SQLite (trace_id, timestamp, status, final_score).

*Phase 3: Backward trace analyzer (Days 6-9).*
- Root cause analysis: for a failed trace, walk backward through the spans. At each step ask whether the output is a reasonable transformation of the input. Use an LLM-as-judge to score each step's output quality given its input. The first step with a significant quality drop is the root cause.
- Failure taxonomy: **Extraction Hallucination** (entities that do not exist in the source), **Misclassification** (wrong document type), **Propagation Error** (step N was correct but step N+1 misinterpreted its output), **Prompt Failure** (the LLM ignored instructions), **Context Loss** (important information from earlier steps was dropped).
- Evidence chain: for each diagnosed failure, a structured explanation such as "Step 2 (Extraction) hallucinated the entity 'John Smith', which does not appear in the source document. This propagated to Step 4, which included it in the summary", with the specific input/output pairs as evidence.

*Phase 4: Visual trace explorer (Days 9-11).*
- Trace view: each step is a node, color-coded green (healthy), yellow (low confidence), red (identified root cause). Clicking a node shows full span details: input, output, prompt, confidence.
- Diff view for failed traces: side-by-side of what the step received, what it produced, and what it should have produced (from the golden dataset or human correction), highlighting the divergence.
- Flagging interface: a button to mark any trace as "bad output". It triggers backward analysis and shows the diagnosis. The user can confirm or override the diagnosis.

*Phase 5: Feedback-to-eval loop (Days 11-13).*
- Auto-generate eval cases from confirmed flags: original input, failing step, bad output, human-corrected output, failure category. Append to a growing eval dataset.
- Regression tracking: periodically re-run the accumulated eval dataset against the current pipeline. Track whether known failures are still failing or fixed. Show a trend line of "known issues resolved" over time.
- Failure analytics dashboard: most common failure types, which step fails most often, failure rate over time, average time to root cause.

*Phase 6: Portfolio polish (Days 13-14).*
- Demo: process 50 documents with at least 8-10 failures of different types. Record a Loom showing: a bad output, opening the trace explorer, diagnosing the root cause, flagging it, and the eval dataset growing.
- Architecture doc with a diagram of the pipeline plus tracing layer, the backward analysis flow, and the feedback loop. Framing: "I built an observability system that reduces mean time to root cause for AI pipeline failures from hours of manual debugging to seconds of automated diagnosis."
</project_brief>

<builder_context>
<!-- FILL THESE IN BEFORE SENDING. If you leave a field blank, Opus will state a default assumption. -->
- Team size and hours per day available: [e.g., solo, 4-6 h/day]
- Comfort level: Python [ ], async/FastAPI [ ], React/TypeScript [ ], OpenTelemetry [ ]
- Total LLM spend budget for the whole project (USD): [e.g., 30] — plan against OpenRouter's per-model pricing, plus its credit-based minimum top-up. State the minimum top-up required to start and flag that OpenRouter bills per-request overhead beyond the published token price.
- Hard deadline / demo date: [e.g., 14 calendar days from start]
- Is the project graded? On what? [e.g., working demo + report + viva]
- Dev machine OS (macOS/Linux/Windows): [ ]
- Where you will host the demo (free-tier limits, available disk, custom domain or not): [ ]
- Any constraint on which models the judge may use (e.g., must be a different provider family than the pipeline model): [e.g., none]
</builder_context>

<provider_constraint_openrouter>
The LLM provider is **OpenRouter**, not the OpenAI API directly. This is a deliberate choice and the plan must design around it rather than pretending it is OpenAI. Address each of these explicitly:

1. **Model selection is configuration, not code.** OpenRouter model identifiers are `provider/model` slugs that change as providers are added and retired. Every model name — pipeline models, judge model, cheap model for pre-checks, summarizer — must come from environment variables with a documented default. Never hardcode a slug. Mark current slugs, context windows, and pricing `VERIFY BEFORE USE` and pin them in a `.env.example` with a comment explaining that slugs are validated at startup (fail fast with a clear message rather than a 404 mid-pipeline).
2. **Provider routing must be pinned for reproducibility.** By default OpenRouter may route a slug to different upstream providers across calls, which silently breaks determinism and replay. Decide and record an ADR: pin an explicit `provider` routing object (with fallback order) per model so a given model name resolves to the same upstream provider for the life of a run, or accept nondeterminism and say so. If you pin, state that a pinned provider can be rate-limited more often and design a fallback for it.
3. **Structured output is not uniformly supported.** Not every OpenRouter model supports OpenAI-style `response_format` with a JSON schema, and support varies by upstream provider. Design one `LLMClient` method for structured calls with a three-tier fallback: (a) native structured output / JSON mode where the selected model supports it, (b) explicit schema-in-prompt plus tolerant parsing, (c) a repair round-trip that feeds the validation error back once. Decide and record how the client discovers a model's capabilities (a pinned allowlist in config, versus querying OpenRouter's model metadata endpoint at startup — `VERIFY BEFORE USE` whether that endpoint is stable and authenticated). Extraction and classification must still get typed Pydantic output in all three tiers; if a model cannot be trusted to conform, it must be rejected at config time rather than failing mid-run.
4. **Reasoning models break naive response parsing.** Some models return a separate reasoning field alongside content. The client must extract content correctly for both shapes, and the plan must state what happens to reasoning content in trace storage (recommend: store it, do not show it by default).
5. **This is a strength for judge independence — use it.** The weakness that a judge is biased toward its own family's outputs becomes cheap to solve here: pick the pipeline models and the judge model from *different provider families* via separate env vars, and state this as the mitigation. Record it as an ADR and make it a config assertion rather than a convention, so a misconfiguration that puts pipeline and judge in the same family fails loudly.
6. **Cost control must be OpenRouter-shaped.** Spend caps, per-model price tables, and a worked cost estimate driven by env-var-configured prices rather than hardcoded numbers. Note that reasoning models bill reasoning tokens, which can dominate cost, and that some models are priced per-request rather than per-token. Prefer non-reasoning models for the pipeline; if a reasoning model is used for the judge, budget for it explicitly.
7. **Caching and replay still belong to us.** OpenRouter does not make the project's caching layer unnecessary: the project-level response cache keyed by a hash of (model, provider routing, prompt version, input) is what makes record/replay and the deterministic test suite possible, and it is also what keeps the demo reproducible. Plan a single cache layer in our code; do not rely on provider-side prompt caching.
8. **Secrets and telemetry.** Only the OpenRouter API key is a secret. Include a note that requests to OpenRouter transmit document text to third-party providers, which reinforces the PII policy: the dataset must contain no real personal data, because every document is forwarded to whichever upstream provider serves the request.
</provider_constraint_openrouter>

<known_weaknesses_you_must_address>
Treat these as design inputs. They are the difference between a toy and a credible system. Your plan must resolve each one explicitly.

1. **Self-reported confidence is a weak signal.** LLMs are poorly calibrated. Plan to collect it, but treat it as a prior and not as evidence. Plan to measure its usefulness on the labeled set (for example, whether low-confidence spans are actually more likely to be root causes) and report the result, even if it is bad.
2. **The LLM judge can be wrong, and can be biased toward its own family's outputs.** Plan to (a) use a judge model that is separate from, and at least as capable as, the pipeline model, (b) use rubric-based scoring with structured outputs, (c) run cheap deterministic checks first wherever ground truth can be computed (for example, entity grounding by normalized substring match against the source), and use the LLM only for what code cannot decide, and (d) validate the analyzer against labeled data.
3. **We can get free ground truth.** Because we deliberately seed failures, we know the true root-cause step and category for those documents. Design the dataset and a **fault-injection harness** (a deterministic way to corrupt a specific step's output, for example drop an entity, swap the label, or truncate context) so the analyzer can be scored objectively: step-localization accuracy, category accuracy, and evidence quality. Report these as headline metrics.
4. **Propagation needs a precise definition.** The brief's taxonomy is ambiguous between "an upstream error carried downstream unchanged" and "step N was correct but step N+1 misread it". Resolve this with a decision table (see Deliverable D5) and a root-cause algorithm that distinguishes **local fidelity** (is this step's output a faithful transformation of its own input?) from **global fidelity** (is this step's output correct relative to the original source document?). A step whose output is locally faithful but globally wrong is a victim of upstream error, not the root cause.
5. **"Hours to seconds" is an unmeasured claim.** Plan a small, honest baseline: time how long it takes to manually find the root cause in N failing traces without the tool versus with it, and report both. Define "time to root cause" precisely (for example, from flag timestamp to diagnosis confirmation).
6. **Reproducibility and flakiness.** LLM outputs vary. Plan for temperature 0 where supported, recorded model and prompt versions on every span, response caching keyed by a hash of (model, prompt, input), and a record-and-replay mode so tests are deterministic and cost nothing.
7. **Regression tracking needs identity.** To say a known failure is "fixed", each trace and eval run must record `pipeline_version` and per-step `prompt_version` (content hash). Define what "fixed" means per step type (for example, exact match for classification, entity-set F1 for extraction, judge score plus deterministic checks for summaries).
8. **Documents are untrusted input.** Plan basic prompt-injection hygiene (clear delimiting of document text in prompts, instruction that document content is data) in both the pipeline and the judge, and a rule that no real PII is used in the dataset.
</known_weaknesses_you_must_address>

<decisions_to_make>
Make each decision, justify it in 2-5 sentences with the trade-offs, and record it as an Architecture Decision Record (ADR). The leaning default is given; override it only with a stated reason.

| # | Decision | Leaning default |
|---|---|---|
| 1 | Custom chain vs LangChain | Custom, thin typed chain. Tracing clarity and testability matter more than framework features. Mention an optional adapter only as a non-goal. |
| 2 | LLM access layer | One small `LLMClient` wrapper over the OpenRouter chat-completions endpoint. This is justified because the tracing layer must capture prompt, raw response, tokens, latency, provider routing, and retries in one place, and because OpenRouter's uneven per-model feature support (structured output, reasoning fields) must be normalized in exactly one place rather than leaking into every step. Use Pydantic-bound structured output with the three-tier fallback from `provider_constraint_openrouter`. Justify the HTTP client choice (raw `httpx`, the official `openai` SDK pointed at a custom base URL, or `openrouter` SDK) and record it as an ADR. |
| 3 | OpenTelemetry usage | Use the OTel SDK for span creation and context propagation, with a custom `SpanProcessor`/exporter that writes our JSON + SQLite. Use OTel GenAI semantic-convention attribute names where they exist (`VERIFY BEFORE USE` the current names). Do not require a collector or backend. |
| 4 | Where payloads live | Full span payloads in per-trace JSON files (git-friendly, inspectable); SQLite holds the index plus relational tables for flags, diagnoses, eval cases, and eval runs. State why this split and what the migration path would be. |
| 5 | Backend framework | FastAPI (the REST feedback API is required anyway; reuse it for the UI). |
| 6 | Frontend | Decide between Streamlit and React (Vite + TypeScript + React Flow). Score both against: build time, quality of the node graph and diff view, effort to add flag/confirm/override interactions, fit with `builder_context`. State a primary choice, and a fallback you can switch to on Day 9 if behind. |
| 7 | Judge model strategy | Separate, stronger judge model from the pipeline model, configured by env var, and — because the provider is OpenRouter — from a **different provider family** so the judge cannot share the pipeline model's self-preference bias. Enforce the family separation as a startup assertion, not a convention. Deterministic checks first, then rubric-based LLM judgment with structured output. Include judge self-consistency (for example, 2-3 samples on borderline scores) only if the budget allows, and justify it. Note that model upgrades can silently change judge behavior, so pin the judge's model id in config and record it on every diagnosis. |
| 8 | Analyzer algorithm | Score every step on local and global fidelity (not just stop at the first drop), then derive the root cause from the causal ordering. Explain why this beats a naive "walk back and stop at the first drop". |
| 9 | Eval scoring per step type | Specify the metric for each of the four steps. |
| 10 | Test strategy | pytest with a record/replay layer for LLM calls, golden trace fixtures, and a small number of live smoke tests gated behind a flag. |
| 11 | Dataset construction | Mix of LLM-synthesized and hand-written documents, with a `labels.json` ground-truth file. Specify count, type mix, and failure-type quotas (see Deliverable D8). |
| 12 | Cost control | Per-run and global spend caps enforced in our own client, cheaper model for the pipeline steps, cheaper still for pre-checks, our own response cache, and a dry-run mode. All prices and model ids come from config; the caps must work across heterogeneous per-model and per-request pricing from OpenRouter. |
</decisions_to_make>

<deliverables>
Produce the following sections, in this order, using the headings below. Be dense and specific; avoid filler and generic advice.

### Part A: Product Requirements (PRD)
- **A1. Problem statement and goals.** Who feels this pain, and what changes for them.
- **A2. Personas.** At least: the pipeline developer who debugs, the reviewer who flags bad outputs, and the interviewer or evaluator who watches the demo.
- **A3. User stories** with IDs (`US-01`...) and acceptance criteria in Given/When/Then form.
- **A4. Scope.** Must-have, should-have, could-have (MoSCoW), each mapped to phases.
- **A5. Non-goals.** Explicit list.
- **A6. Success metrics.** Product-level and analyzer-level, each with a measurement method and a target. Include at minimum: root-cause step-localization accuracy and category accuracy on seeded failures, judge-vs-human agreement on a labeled subset, the confidence-signal usefulness result, regression-fix tracking correctness, and measured time to root cause (tool vs manual baseline).
- **A7. Demo script.** The 3-5 minute Loom storyline, scene by scene, tied to the exact features and data it needs.

### Part B: Architecture
- **B1. System context and component diagram** (Mermaid, with an ASCII fallback). Show: pipeline, tracing layer, exporter, JSON store, SQLite, backward analyzer, REST API, UI, feedback loop, eval runner, analytics.
- **B2. Runtime sequence diagrams** (Mermaid) for: (1) a normal pipeline run, (2) flag then analyze then confirm or override, (3) eval case creation and regression run.
- **B3. Data flow and trust boundaries.** Where untrusted document text enters, where secrets live, what is sent to the external LLM.
- **B4. Repository and folder structure**, with a one-line purpose per file or directory. Keep it flat and small.
- **B5. Architecture Decision Records** for all items in `<decisions_to_make>`.

### Part C: Technical Requirements (TRD)
- **C1. Environment and tooling.** Python version, dependency manager, linting and typing tools, pre-commit, `.env` handling, Makefile or task runner targets. Include a `.env.example` listing every model slug and price with a comment marking each as `VERIFY BEFORE USE`, plus a startup config validation step that fails fast on a bad model id.
- **C2. Domain models.** Pydantic skeletons for: `Document`, `IntakeOutput`, `ExtractionOutput` (entities with value, type, and a source span or offset where possible), `ClassificationOutput`, `SummaryOutput`, `Span`, `Trace`, `StepScore`, `Diagnosis` (root-cause step, category, evidence chain, confidence, analyzer version), `Flag`, `EvalCase`, `EvalRun`, `EvalResult`. Include versioning fields (`pipeline_version`, `prompt_version`, `schema_version`). `Span` and `Diagnosis` must additionally record the model id and pinned provider routing used, since OpenRouter makes the exact upstream model part of a result's identity.
- **C3. Pipeline contracts.** Exact function signatures for the four steps, the `@traced_step` decorator contract, and how one instrumented step is added in one line.
- **C4. LLM client contract.** The single OpenRouter-facing entry point: base URL, auth header, and request/response shapes marked `VERIFY BEFORE USE`. Retries and backoff, timeouts, token accounting (including reasoning tokens and per-request-priced models), structured output handling with the three-tier fallback from `provider_constraint_openrouter`, validation-failure repair, provider routing pin, capability discovery and rejection of unsupported models at startup, caching and replay modes, and spend caps. Specify exactly which failure classes are retried (transport errors, 429, 5xx) versus surfaced immediately (401/403 auth, 404 unknown model, schema-exhaustion), and what a `PipelineError` carries into the span when a step ultimately fails.
- **C5. Tracing design.** Span lifecycle, attributes captured per span, how errors and partial failures are represented, how trace status (success / failure / degraded) is computed, and how the confidence score is requested and stored.
- **C6. Storage design.** Full SQLite DDL (tables, keys, indexes), JSON trace file layout and naming, atomic write strategy, and the consistency story between the file and the index.
- **C7. Analyzer design.** The algorithm in pseudocode; the per-step rubrics; the deterministic checks per step; the judge prompt templates (full text, with injection-hygiene delimiters); the output schema; the decision table for failure categories (including the Propagation definition from `known_weaknesses_you_must_address` item 4); and how the evidence chain is assembled from real input/output pairs.
- **C8. REST API specification.** Endpoint table with method, path, request and response schemas, status codes, and error format, covering at minimum: run pipeline on a document, list and get traces, flag a trace, run or fetch diagnosis, confirm or override diagnosis, list eval cases, trigger an eval run, fetch eval runs and trend data, fetch analytics.
- **C9. Frontend specification.** Screens, component tree, state management, the node-graph color rules (green / yellow / red with exact thresholds), the diff view data contract, and the flag/confirm/override flow.
- **C10. Eval and regression engine.** Case schema, per-step-type scoring and the definition of "fixed", run scheduling (manual trigger plus a simple scheduled option), result storage, and the trend-line computation.
- **C11. Analytics.** The exact queries or aggregations for: failure types, failing step frequency, failure rate over time, average time to root cause.
- **C12. Non-functional requirements.** Latency budget per pipeline run and per diagnosis, cost budget with a worked estimate (tokens times config-driven price assumptions, flagged as assumptions, including the OpenRouter minimum top-up), reliability, determinism (including what pinned provider routing does and does not guarantee), and observability of the tool itself (logging, plus a per-request log of model id, provider, tokens, latency, and cache hit so a run's cost is auditable after the fact).
- **C13. Security, privacy, and safety.** Secrets (the OpenRouter API key is the only one; note that the standard OpenAI SDK pointed at a custom base URL still expects an `OPENAI_API_KEY`-style env var, so decide and document the variable name rather than letting it be confusing), PII policy for the dataset (no real personal data, because every document is forwarded to a third-party upstream provider chosen by OpenRouter routing), prompt-injection hygiene, and dependency pinning. Also cover what to do if the API key is ever committed: rotation procedure and a pre-commit secret scan.
- **C14. Testing strategy.** Test pyramid with concrete examples: unit tests for steps and the decorator, contract tests for schemas, golden-trace tests for the analyzer, fault-injection tests, API tests, one end-to-end smoke test, and the LLM record/replay approach. State coverage expectations realistically.
- **C15. Deployment and packaging.** No Docker. Plan instead: (a) a local dev/run story — dependency manager (`uv` preferred, else `poetry`/`venv`), a `Makefile` or `justfile` with `install` / `dev` / `test` / `run-api` / `run-ui` / `eval` / `seed` targets, and how to run API and UI together without a container runtime; (b) Render deployment — service type, build/start commands, runtime and Python version, environment variables and secrets, and **persistent disk** mounted for the SQLite database file and the JSON trace directory, including the disk mount path and how the app resolves it via config; (c) health checks and graceful handling of Render's spin-down and cold starts; (d) the one-command local run and the one-command deploy; (e) a static-export or read-only demo mode as a fallback if a persistent disk is unavailable on the chosen plan, so the public demo degrades gracefully instead of losing data. State clearly what data survives a redeploy and what a restart with a fresh disk loses.

### Part D: Data and Evaluation Plan
- **D5. Failure taxonomy operational spec.** For each of the five categories plus `Unknown/Other`: definition, the observable signature in a trace, the deterministic detector (if any), the judge rubric item, a worked example, and the confusable neighbors with a rule to separate them.
- **D6. Failure injection plan.** Two mechanisms: (a) naturally difficult documents (no-date contract, mixed-currency invoice, ambiguous doc type, plus others you propose), and (b) synthetic fault injection into a step's output for controlled ground truth. Provide a matrix: failure category by injection method by document type.
- **D7. Analyzer validation plan.** How the labeled set is built, labeling protocol, the metrics (step-localization accuracy, category accuracy, confusion matrix, judge-vs-human agreement, false-positive rate on healthy traces), and what we do if the numbers are poor.
- **D8. The 50-document demo dataset.** Exact composition: counts per document type, counts per failure category (at least 2 seeded documents per category, 8-10 or more total failures), healthy-document count, how documents are created, naming convention, and the `labels.json` schema.

### Part E: Phased Implementation Plan
Use these phases and day ranges. They align with the original brief and add a short Phase 0 and explicit buffer. Total: 14 days.

- **Phase 0: Foundations and decisions (Day 0-1)**
- **Phase 1: Multi-step pipeline and dataset (Days 1-3)**
- **Phase 2: Tracing layer (Days 3-6)**
- **Phase 3: Backward trace analyzer (Days 6-9)**
- **Phase 4: Visual trace explorer (Days 9-11)**
- **Phase 5: Feedback-to-eval loop and analytics (Days 11-13)**
- **Phase 6: Hardening, demo, and documentation (Days 13-14)**

For **every phase**, provide exactly this structure:
1. **Goal** (one sentence) and **Exit criteria** (the checklist that must be true to leave the phase).
2. **Inputs** from previous phases and **Outputs** handed to the next.
3. **Task list**: ordered, with IDs (`P2-T03`). For each task: description, files created or changed, dependencies, estimated hours, and **Verification** (the exact command, test, or observable result). Tasks should be 1-4 hours each.
4. **Tests to write** in this phase.
5. **Risks** specific to the phase with mitigations.
6. **Demo checkpoint**: what can be shown at the end of the phase.
7. **Cut line**: what to drop first in this phase if behind schedule.

Then provide:
- **E-1. Dependency graph** of tasks across phases (Mermaid), and the critical path.
- **E-2. Daily schedule table** (Day, planned tasks, hours, checkpoint).
- **E-3. Global cut list**, ordered: what to cut first, second, third if the schedule slips, and what must never be cut (the minimum viable portfolio project).
- **E-4. Risk register** (risk, likelihood, impact, mitigation, owner trigger).

### Part F: Execution Kit
- **F1. CLAUDE.md for this repo.** Drafted content that a coding agent will read: project overview, architecture summary, commands (install, test, lint, run, eval), coding conventions (typed, small functions, no speculative abstraction, surgical diffs), testing rules (never call the live API in unit tests), and a definition of done.
- **F2. Task prompt template** for handing a single task (for example `P2-T03`) to a coding agent, including how to reference the plan, the acceptance criteria, and the verification step.
- **F3. Architecture doc outline** for the final portfolio write-up, including the honest framing of the "hours to seconds" claim and a "limitations and future work" section.
- **F4. Interview talking points**: the 8 hardest questions an interviewer would ask about this project (for example on judge reliability, calibration, why not LangSmith, and how this scales), each with a strong, honest answer grounded in the design.

### Part G: Open Items
- **G1. Assumptions made** (numbered, each with the alternative).
- **G2. Questions I must answer**, limited to those that truly block a decision, each with the default you assumed.
- **G3. Items marked `VERIFY BEFORE USE`** collected in one list, grouped by source, covering at minimum: OpenRouter API specifics (base URL, auth header name, request/response shapes, whether a model-metadata endpoint exists and is stable, how provider routing is pinned, minimum credit top-up, whether per-request pricing applies to any candidate model, reasoning-field response shape), candidate model slugs and their context windows and pricing, OTel GenAI attribute names, and any library API whose behavior you are assuming.
</deliverables>

<output_rules>
- Write in clear, direct prose and tight tables. Prefer specifics (file names, field names, thresholds, commands) over generalities.
- Use Markdown headings exactly as numbered above so the document can be navigated and referenced by ID.
- Use Mermaid for diagrams, with an ASCII fallback for the main component diagram.
- Every task, requirement, and user story gets a stable ID, and later sections must reference earlier IDs (traceability: user story to requirement to task to test).
- Where you give numbers (thresholds, counts, costs, hours), state the reasoning or mark them as tunable assumptions.
- Do not pad. Do not repeat the brief back to me. Do not include motivational text.
- If the full document would exceed your output limit, stop cleanly at the end of a complete section, write `CONTINUE FROM: <next section ID>`, and resume there when I reply "continue". Never truncate mid-section or compress later parts to fit.
</output_rules>

<final_self_check>
Before you finish, silently verify and then fix any failures:
1. Every phase from the brief is covered, and every task has a concrete verification.
2. All five failure categories have a detector or rubric, a worked example, and at least 2 seeded documents.
3. The Propagation category is defined unambiguously, and the algorithm correctly refuses to blame a step whose output is locally faithful to bad input.
4. The plan measures its own claims: analyzer accuracy, confidence usefulness, judge agreement, and time to root cause versus a manual baseline.
5. Nothing in the plan requires infrastructure beyond a laptop and a single free-tier Render service with a persistent disk. No Docker, no Kubernetes, no message queues, no external database.
6. Every `[ADDED]` item is justified and the total scope fits 14 days at the stated hours, with a cut list if it does not.
7. No invented model names, prices, or library APIs are stated as fact. No OpenRouter slug, price, or API shape appears anywhere in the plan without being marked `VERIFY BEFORE USE` and sourced from config.
8. The provider-independence mitigation is real: the pipeline model and the judge model are configured from different provider families, and misconfiguration fails loudly rather than silently producing a biased judge.
Then output the document.
</final_self_check>

=== END PROMPT ===

---

## Notes for you (not part of the prompt)

- **Follow-up prompts that work well after the plan arrives:** "Expand Phase 3 into per-task implementation specs with file-level detail", "Generate the SQLite DDL and Pydantic models as real code and tests", and "Write the labeled dataset generator spec for the 50 documents."
- **Use plan mode in Claude Code** when you start executing, and feed it one task ID at a time using the F2 template.
- **Keep the plan file in the repo** (for example `docs/PLAN.md`) so the coding agent can reference it by task ID.
