"""Command line entry point.

Subcommands: seed (generate the labeled dataset), run (execute one document),
analyze, eval, analytics, validate-dataset, demo. Exposed as the `forensics`
console script and as `python -m forensics.cli`.
"""

import json
import sys
import threading
from pathlib import Path
from typing import Annotated

import typer

from forensics.analyzer_simple import analyze_trace
from forensics.auth import AuthManager
from forensics.config import get_settings, validate_startup
from forensics.documents import is_valid_doc_id, resolve_document_text
from forensics.llm import QuotaExhaustedError, RateLimitError, using_llm_client
from forensics.models import DEFAULT_TENANT_ID, Document, DocumentType, FailureCategory
from forensics.pipeline import Pipeline
from forensics.storage import get_storage
from forensics.tracing import trace_context

# The demo runs one pipeline per dataset document. A hosted provider's
# per-minute request budget is shared and small on the free tier, so the batch
# is paced and a rate-limited document is retried after the window resets.
_DEMO_RATE_LIMIT_ATTEMPTS = 3
_DEMO_RATE_LIMIT_DELAY_S = 30
_DEMO_INTER_DOCUMENT_DELAY_S = 5.0

app = typer.Typer(
    name="forensics",
    help="Failure forensics tool for multi-step AI pipelines",
    no_args_is_help=True,
)


def _setup() -> None:
    """Run startup validation."""
    try:
        validate_startup()
    except ValueError as e:
        typer.secho(f"Configuration error: {e}", fg=typer.colors.RED, err=True)
        sys.exit(1)


@app.command()
def ping() -> None:
    """Print the resolved configuration and model info.

    Deliberately no network call: this is the check an operator runs when a run
    misbehaved, and it must work when the provider is the thing that is down.
    """
    _setup()
    settings = get_settings()

    typer.echo("Configuration OK")
    for name, model in [
        ("Pipeline", settings.pipeline_model),
        ("Pipeline fallback", settings.pipeline_fallback_model),
        ("Judge", settings.judge_model),
        ("Cheap", settings.cheap_model),
    ]:
        typer.echo(
            f"{name} model: {model.model_id} (provider: {model.provider}, family: {model.family})"
        )
    typer.echo(f"Per-run cap: ${settings.spend_cap_per_run_usd:.2f}")
    typer.echo(f"Global cap: ${settings.spend_cap_global_usd:.2f}")
    typer.echo(f"Cache: {'enabled' if settings.cache_enabled else 'disabled'}")
    typer.echo(f"Dry run: {settings.dry_run}")

    # OTel status
    otel = settings.otel
    typer.echo(f"OTel exporter: {otel.exporter} (endpoint: {otel.endpoint})")
    typer.echo(f"OTel service: {otel.service_name} (sampling: {otel.sampling_rate:.0%})")


@app.command()
def seed() -> None:
    """Load the committed real-model traces into this instance's storage.

    The public demo runs once against a real model and its traces are committed
    under `dataset/live/traces`. Loading them here means a fresh deployment (or a
    fresh local database) shows real traces, latencies and diagnoses instead of
    only the `DRY_RUN` fixture — and it works without an API key or quota.

    Idempotent: traces already indexed in this database are skipped, so it is
    safe to run on every boot.
    """
    _setup()

    from forensics.analyzer_simple import analyze_trace
    from forensics.eval_simple import document_ground_truth
    from forensics.models import Trace

    settings = get_settings()
    fixture_dir = settings.dataset_dir / "live" / "traces"
    fixture_paths = sorted(fixture_dir.glob("*.json")) if fixture_dir.exists() else []
    if not fixture_paths:
        typer.secho(
            f"No live fixtures found in {fixture_dir}; nothing to seed.",
            fg=typer.colors.YELLOW,
        )
        return

    storage = get_storage()
    seeded = skipped = failed = 0

    for path in fixture_paths:
        try:
            trace = Trace.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:
            typer.secho(f"  skipping {path.name}: {exc}", fg=typer.colors.RED, err=True)
            failed += 1
            continue

        if storage.get_trace(trace.trace_id, tenant_id=None) is not None:
            skipped += 1
            continue

        document_text = resolve_document_text(trace)
        if not document_text:
            typer.secho(
                f"  skipping {path.name}: document {trace.document_id!r} not found",
                fg=typer.colors.RED,
                err=True,
            )
            failed += 1
            continue

        storage.save_trace(trace)
        diagnosis = analyze_trace(trace, document_text, document_ground_truth(trace.document_id))
        storage.create_diagnosis(diagnosis)
        seeded += 1
        typer.secho(
            f"  seeded {trace.document_id} ({trace.trace_id}): "
            f"{diagnosis.root_cause_step} / {diagnosis.category.value}",
            fg=typer.colors.GREEN,
        )

    typer.echo(
        f"Live seed: {seeded} loaded, {skipped} already present, {failed} skipped with errors."
    )
    if failed and not seeded:
        raise typer.Exit(code=1)


@app.command()
def run(doc_path: Path) -> None:
    """Run the pipeline on a single document."""
    _setup()
    if not doc_path.exists():
        typer.secho(f"Document not found: {doc_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    import asyncio

    doc = Document(content=doc_path.read_text())

    # The dataset's ground-truth type lets `check_label_grounding` detect a
    # valid-but-wrong label (misclassification) instead of staying silent.
    expected_type: str | None = None
    labels_path = get_settings().dataset_dir / "labels.json"
    if labels_path.exists():
        with open(labels_path) as f:
            labels = json.load(f)
        for doc_info in labels.get("documents", []):
            if doc_info.get("doc_id") == doc_path.stem:
                # `None` when the label omits the type: the old `"unknown"`
                # default made `check_label_grounding` fail every classification
                # against a type that cannot exist.
                expected_type = doc_info.get("doc_type")
                break

    async def _run() -> None:
        async with using_llm_client() as client:
            pipeline = Pipeline(client=client)
            with trace_context(document_id=doc_path.stem) as ctx:
                result = await pipeline.run(doc, document_id=doc_path.stem)
                storage = get_storage()
                assert ctx.trace is not None
                # Diagnose the unredacted trace, then persist (save_trace redacts
                # in place when PII_REDACTION_ENABLED is on).
                diagnosis = analyze_trace(ctx.trace, doc.content, expected_type)
                json_path = storage.save_trace(ctx.trace)

            # Persist the diagnosis so analytics/eval/UI have a root cause.
            storage.create_diagnosis(diagnosis)

            typer.secho(f"Pipeline completed: {doc_path.stem}", fg=typer.colors.GREEN)
            typer.echo(f"Trace saved to: {json_path}")
            typer.echo(f"Trace ID: {ctx.trace.trace_id}")
            typer.echo(f"Intake: {result.intake.char_count} chars")
            typer.echo(f"Extraction: {len(result.extraction.entities)} entities")
            cls = result.classification.document_type.value
            conf = result.classification.confidence
            typer.echo(f"Classification: {cls} ({conf:.2f})")
            typer.echo(f"Summary: {result.summarization.summary[:80]}...")

    asyncio.run(_run())


@app.command(name="validate-dataset")
def validate_dataset() -> None:
    """Validate the dataset against labels.json."""
    _setup()
    from forensics.analyzer_simple import reachable_failure_pairs

    labels_path = get_settings().dataset_dir / "labels.json"
    if not labels_path.exists():
        typer.secho("labels.json not found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    with open(labels_path) as f:
        labels = json.load(f)

    errors: list[str] = []
    warnings: list[str] = []

    valid_types = {d.value for d in DocumentType}
    valid_steps = {"intake", "extraction", "classification", "summarization"}
    valid_categories = {c.value for c in FailureCategory}
    reachable = reachable_failure_pairs()

    for doc_info in labels.get("documents", []):
        doc_id = doc_info["doc_id"]
        doc_path = get_settings().dataset_dir / "documents" / f"{doc_id}.txt"

        if not doc_path.exists():
            errors.append(f"Missing document file: {doc_path}")
            continue

        try:
            content = doc_path.read_text()
            if not content.strip():
                warnings.append(f"Empty document: {doc_path}")
        except Exception as e:
            errors.append(f"Cannot read {doc_path}: {e}")
            continue

        # The label vocabulary must be expressible by the pipeline, or the
        # document is unclassifiable by construction.
        doc_type = doc_info.get("doc_type")
        if doc_type not in valid_types:
            errors.append(
                f"{doc_id}: doc_type '{doc_type}' is not a DocumentType "
                f"(valid: {sorted(valid_types)})"
            )

        if doc_info.get("should_fail"):
            step = doc_info.get("failure_step")
            category = doc_info.get("failure_category")

            if step not in valid_steps:
                errors.append(f"{doc_id}: failure_step '{step}' is not a pipeline step")
            if category not in valid_categories:
                errors.append(f"{doc_id}: failure_category '{category}' is not a FailureCategory")
            elif (step, category) not in reachable:
                errors.append(
                    f"{doc_id}: seeded failure ({step}, {category}) is unreachable by the "
                    f"analyzer; ground truth must be reproducible"
                )

    if errors:
        typer.secho("VALIDATION FAILED", fg=typer.colors.RED, bold=True)
        for err in errors:
            typer.secho(f"  ERROR: {err}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if warnings:
        typer.secho("VALIDATION PASSED WITH WARNINGS", fg=typer.colors.YELLOW, bold=True)
        for warn in warnings:
            typer.secho(f"  WARNING: {warn}", fg=typer.colors.YELLOW)
    else:
        typer.secho("VALIDATION PASSED", fg=typer.colors.GREEN, bold=True)

    typer.echo(f"Documents checked: {len(labels.get('documents', []))}")
    typer.echo(f"Errors: {len(errors)}, Warnings: {len(warnings)}")


@app.command()
def demo() -> None:
    """Run the pipeline on all 15 documents and print summary."""
    _setup()
    dataset_dir = get_settings().dataset_dir
    labels_path = dataset_dir / "labels.json"
    if not labels_path.exists():
        typer.secho("labels.json not found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    import asyncio

    with open(labels_path) as f:
        labels = json.load(f)

    storage = get_storage()

    async def _run_demo() -> None:
        async with using_llm_client() as client:
            pipeline = Pipeline(client=client)
            results = []
            for doc_info in labels.get("documents", []):
                doc_id = doc_info["doc_id"]
                doc_path = dataset_dir / "documents" / f"{doc_id}.txt"

                if not doc_path.exists():
                    typer.secho(f"Skipping missing: {doc_id}", fg=typer.colors.YELLOW)
                    continue

                doc = Document(content=doc_path.read_text())
                # A hosted model is rate limited, and a batch of 15 documents is
                # enough requests to hit it. Aborting the whole demo on the first
                # 429 threw away every document already finished; retrying the
                # document after the limit resets costs nothing extra because the
                # steps that already succeeded are cached.
                for attempt in range(1, _DEMO_RATE_LIMIT_ATTEMPTS + 1):
                    try:
                        with trace_context(document_id=doc_id) as ctx:
                            result = await pipeline.run(doc, document_id=doc_id)
                        break
                    except (QuotaExhaustedError, RateLimitError):
                        if attempt == _DEMO_RATE_LIMIT_ATTEMPTS:
                            raise
                        wait = _DEMO_RATE_LIMIT_DELAY_S * attempt
                        typer.secho(
                            f"  rate limited on {doc_id}; retrying in {wait}s "
                            f"(attempt {attempt}/{_DEMO_RATE_LIMIT_ATTEMPTS})",
                            fg=typer.colors.YELLOW,
                        )
                        await asyncio.sleep(wait)

                assert ctx.trace is not None
                # Analyze and persist the diagnosis so analytics, the eval
                # loop and the UI have a real root cause per trace.
                # Previously the demo saved traces but never analyzed them,
                # so analytics and eval had nothing to read.
                expected_type = doc_info.get("doc_type")
                # The dataset's ground-truth type lets the analyzer detect a
                # valid-but-wrong label (misclassification).
                #
                # Analyze before `save_trace` redacts the trace in place (PII
                # redaction), or every extracted entity grounds against a
                # span that no longer contains it.
                diagnosis = analyze_trace(ctx.trace, doc.content, expected_type)
                storage.save_trace(ctx.trace)
                storage.create_diagnosis(diagnosis)

                actual_type = result.classification.document_type.value
                match = expected_type == actual_type

                results.append(
                    {
                        "doc_id": doc_id,
                        "expected": expected_type,
                        "actual": actual_type,
                        "match": match,
                        "should_fail": doc_info.get("should_fail", False),
                        "failure_step": doc_info.get("failure_step"),
                        "failure_category": doc_info.get("failure_category"),
                        "root_cause_step": diagnosis.root_cause_step,
                        "root_cause_category": diagnosis.category.value,
                    }
                )

                status = "✓" if match else "✗"
                color = typer.colors.GREEN if match else typer.colors.RED
                msg = f"  {status} {doc_id}: expected={expected_type}, actual={actual_type}"
                typer.secho(msg, fg=color)

                # Pace the batch: the provider's per-minute budget is shared,
                # and a 15-document run at full speed reliably trips it.
                await asyncio.sleep(_DEMO_INTER_DOCUMENT_DELAY_S)

        # Summary
        total = len(results)
        correct = sum(1 for r in results if r["match"])
        seeded_failures = sum(1 for r in results if r["should_fail"])

        typer.echo()
        summary_color = typer.colors.GREEN if correct == total else typer.colors.YELLOW
        typer.secho(f"SUMMARY: {correct}/{total} classifications correct", fg=summary_color)
        typer.echo(f"Seeded failures in dataset: {seeded_failures}")
        typer.echo(f"Traces saved: {total}")

        # Show how many diagnoses found a real root cause and match ground truth
        diagnosed = [r for r in results if r["root_cause_step"] not in ("", "none")]
        typer.echo(f"Traces with a diagnosed root cause: {len(diagnosed)}")

        # The five `should_fail` labels describe the DRY_RUN fixture, which
        # replays them by construction. A real model is not given those faults,
        # so scoring it against them reports its *behaviour*, not the analyzer's
        # accuracy; `benchmark-analyzer` is the tool that measures the analyzer.
        if get_settings().dry_run:
            seeded = [r for r in results if r["should_fail"]]
            step_hits = sum(1 for r in seeded if r["root_cause_step"] == r["failure_step"])
            cat_hits = sum(1 for r in seeded if r["root_cause_category"] == r["failure_category"])
            if seeded:
                typer.echo(
                    f"Seeded-failure root-cause step accuracy: {step_hits}/{len(seeded)}, "
                    f"category accuracy: {cat_hits}/{len(seeded)}"
                )
        else:
            typer.echo(
                "Live run: the `should_fail` labels describe the DRY_RUN fixture, "
                "not faults injected into the model. Run `benchmark-analyzer` for "
                "analyzer step/category accuracy."
            )

    asyncio.run(_run_demo())


@app.command(name="eval", help="Re-run the accumulated eval dataset.")
def eval_command() -> None:
    """Trigger a full eval run against the current pipeline."""
    _setup()
    import asyncio

    from forensics.eval_simple import print_eval_results, run_eval

    run = asyncio.run(run_eval())
    if run:
        print_eval_results(run.run_id)


@app.command(name="eval-run")
def eval_run() -> None:
    """Alias for `eval`."""
    eval_command()


@app.command(name="eval-cases")
def create_eval_cases() -> None:
    """Create eval cases from all confirmed flags."""
    _setup()
    from forensics.eval_simple import create_eval_cases_from_confirmed_flags

    created = create_eval_cases_from_confirmed_flags()
    typer.secho(f"Created {created} eval cases from confirmed flags", fg=typer.colors.GREEN)


@app.command()
def analytics() -> None:
    """Show analytics dashboard."""
    _setup()
    from forensics.api import get_analytics

    analytics = get_analytics()

    # Pretty print analytics
    typer.secho("\n📊 Analytics Dashboard", fg=typer.colors.BLUE, bold=True)
    typer.secho(f"  Total Traces: {analytics.total_traces}", fg=typer.colors.CYAN)

    rate_color = typer.colors.RED if analytics.failure_rate > 0 else typer.colors.GREEN
    typer.secho(f"  Failure Rate: {analytics.failure_rate:.1%}", fg=rate_color)

    # `0.0` is a real measurement (a DRY_RUN trace with zero-latency spans), not
    # a missing one; truthiness printed "N/A" for it.
    if analytics.avg_time_to_root_cause_seconds is not None:
        typer.secho(
            f"  Avg Time to Root Cause: {analytics.avg_time_to_root_cause_seconds:.2f}s",
            fg=typer.colors.CYAN,
        )
    else:
        typer.secho("  Avg Time to Root Cause: N/A", fg=typer.colors.CYAN)

    if analytics.failures_by_category:
        typer.secho("\n  Failures by Category:", fg=typer.colors.BLUE, bold=True)
        for cat, count in analytics.failures_by_category.items():
            typer.secho(f"  {cat.value}: {count}", fg=typer.colors.RED)

    if analytics.failures_by_step:
        typer.secho("\n  Failures by Step:", fg=typer.colors.BLUE, bold=True)
        for step, count in analytics.failures_by_step.items():
            typer.secho(f"  {step}: {count}", fg=typer.colors.RED)

    if analytics.failure_rate_over_time:
        typer.secho("\n  Failure Rate Over Time:", fg=typer.colors.BLUE, bold=True)
        # Entries are {"timestamp": "YYYY-MM-DD", "failure_rate": float} —
        # matching the aggregation in `api.get_analytics`. Reading `rate`,
        # `failures` and `total` from them raised KeyError on real data.
        for entry in analytics.failure_rate_over_time[-10:]:  # Last 10 days
            rate_str = f"{entry['failure_rate']:.1%}"
            typer.secho(f"  {entry['timestamp']}: {rate_str}", fg=typer.colors.YELLOW)


@app.command(name="benchmark-analyzer")
def benchmark_analyzer() -> None:
    """Run fault injection scorecard against a baseline trace."""
    _setup()

    from forensics.eval_simple import document_ground_truth
    from forensics.faults import score_all_faults
    from forensics.storage import get_storage

    # Find a clean trace to use as baseline
    storage = get_storage()
    traces = storage.list_traces(limit=20)
    if not traces:
        typer.secho("No traces found. Run `demo` first.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    # Prefer a clean baseline whose classification is a single document type:
    # `check_propagation` exempts `mixed` (a mixed document legitimately uses
    # several shapes), so a mixed baseline makes the propagation fault
    # unreachable and scores the analyzer down for a verdict it cannot produce.
    def _classified_type(trace: object) -> str | None:
        for span in getattr(trace, "spans", []):
            if span.step_name != "classification":
                continue
            try:
                data = json.loads(span.output_json) if span.output_json else {}
            except json.JSONDecodeError:
                return None
            value = data.get("document_type")
            return str(value) if value is not None else None
        return None

    clean = [
        trace
        for trace in traces
        if (diag := storage.get_diagnosis_for_trace(trace.trace_id)) is None
        or diag.root_cause_step in ("", "none")
    ]
    baseline_trace = next(
        (t for t in clean if _classified_type(t) not in (None, "", "mixed")),
        clean[0] if clean else None,
    )

    if baseline_trace is None:
        baseline_trace = traces[0]

    # `resolve_document_text` covers the dataset *and* documents uploaded
    # through the API; looking only in `dataset/documents` failed with
    # "Document not found" whenever the first clean trace came from an upload.
    doc_text = resolve_document_text(baseline_trace)
    if not doc_text:
        typer.secho(
            f"Document not found for baseline trace {baseline_trace.trace_id} "
            f"({baseline_trace.document_id})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    expected_type = document_ground_truth(baseline_trace.document_id)

    typer.secho(
        "\n📊 Benchmark Analyzer — Fault Injection Scorecard", fg=typer.colors.BLUE, bold=True
    )
    typer.secho(
        f"Baseline trace: {baseline_trace.trace_id} ({baseline_trace.document_id})",
        fg=typer.colors.CYAN,
    )
    typer.secho(f"Expected type: {expected_type or 'unknown'}", fg=typer.colors.CYAN)

    results = score_all_faults(baseline_trace, doc_text, expected_type)

    # Print header
    typer.echo()
    typer.secho(
        f"{'Fault':<25} {'Step':<18} {'Expected':<25} {'Actual Step':<18} "
        f"{'Actual Cat':<25} {'Step':<6} {'Cat':<6} {'Conf':<6}",
        fg=typer.colors.BLUE,
        bold=True,
    )
    typer.secho("─" * 140, fg=typer.colors.BLUE)

    for r in results["results"]:
        step_ok = "✓" if r["step_correct"] else "✗"
        cat_ok = "✓" if r["category_correct"] else "✗"
        step_color = typer.colors.GREEN if r["step_correct"] else typer.colors.RED
        cat_color = typer.colors.GREEN if r["category_correct"] else typer.colors.RED

        typer.secho(
            f"{r['fault']:<25} {r['expected_step']:<18} {r['expected_category']:<25} ", nl=False
        )
        typer.secho(f"{r['actual_step']:<18} ", nl=False)
        typer.secho(f"{r['actual_category']:<25} ", nl=False)
        typer.secho(f"{step_ok:<6} ", fg=step_color, nl=False)
        typer.secho(f"{cat_ok:<6} ", fg=cat_color, nl=False)
        typer.secho(f"{r['confidence']:.2f}")

    typer.echo()
    typer.secho("─" * 140, fg=typer.colors.BLUE)
    typer.secho(
        f"Step-Localization Accuracy: {results['step_accuracy']:.1%}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.secho(
        f"Category Accuracy:           {results['category_accuracy']:.1%}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.secho(f"Total Faults Tested:         {results['total']}", fg=typer.colors.CYAN)


@app.command()
def validate_config() -> None:
    """Validate configuration without running anything."""
    _setup()
    typer.secho("Configuration valid", fg=typer.colors.GREEN)


# ─── auth (spec 04) ─────────────────────────────────────────────────────────

auth_app = typer.Typer(help="Manage tenants and API keys (spec 04)", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


def _auth_manager() -> AuthManager:
    """Build an AuthManager for CLI use.

    Deliberately does *not* call `_setup()`: minting a key must not require a
    valid `NVIDIA_API_KEY` or a reachable model config, and an operator who is
    locking the API down may well be running this on a machine with no model
    credentials.

    Always ensures the `default` tenant exists, so `--tenant default` works for a
    deployment that wants to put an API key in front of the data it already has.
    """
    settings = get_settings()
    manager = AuthManager(
        get_storage(),
        prefix=settings.auth_api_key_prefix,
        rounds=settings.auth_bcrypt_rounds,
    )
    manager.ensure_default_tenant()
    return manager


@auth_app.command("create-tenant")
def auth_create_tenant(
    name: str = typer.Option(..., "--name", help="Human-readable tenant name"),
) -> None:
    """Create a tenant. Prints the new tenant_id on stdout."""
    manager = _auth_manager()
    try:
        tenant = manager.create_tenant(name)
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    typer.secho(f"Created tenant '{tenant.name}'", fg=typer.colors.GREEN, err=True)
    typer.echo(tenant.tenant_id)


@auth_app.command("list-tenants")
def auth_list_tenants() -> None:
    """List every tenant."""
    tenants = _auth_manager().list_tenants()
    if not tenants:
        typer.secho("No tenants.", fg=typer.colors.YELLOW, err=True)
        return
    typer.secho(f"{'tenant_id':<14} {'name':<24} {'active':<7} created", bold=True)
    for t in tenants:
        typer.echo(
            f"{t.tenant_id:<14} {t.name:<24} {'yes' if t.active else 'no':<7} "
            f"{t.created_at.isoformat()}"
        )


@auth_app.command("create-key")
def auth_create_key(
    tenant: str = typer.Option(..., "--tenant", help="tenant_id that will own the key"),
    name: str | None = typer.Option(None, "--name", help="Label, e.g. 'ci-cd'"),
    ttl: int = typer.Option(0, "--ttl", help="Key TTL in days (0 = no expiry)"),
) -> None:
    """Mint an API key. The plaintext key is printed once and never stored."""
    manager = _auth_manager()
    try:
        api_key, plaintext = manager.create_api_key(
            tenant, name=name, ttl_days=ttl if ttl > 0 else None
        )
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    typer.secho(
        f"Created key {api_key.key_id} ({api_key.prefix}…) for tenant {api_key.tenant_id}",
        fg=typer.colors.GREEN,
        err=True,
    )
    if api_key.expires_at:
        typer.secho(
            f"Key expires at: {api_key.expires_at.isoformat()}", fg=typer.colors.YELLOW, err=True
        )
    typer.secho(
        "Store this now — only the bcrypt hash is kept, so it cannot be shown again.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    typer.echo(plaintext)


@auth_app.command("list-keys")
def auth_list_keys(
    tenant: str = typer.Option(..., "--tenant", help="tenant_id whose keys to list"),
) -> None:
    """List a tenant's keys. Key hashes and plaintext are never printed."""
    keys = _auth_manager().list_keys(tenant)
    if not keys:
        typer.secho(f"No keys for tenant {tenant}.", fg=typer.colors.YELLOW, err=True)
        return
    typer.secho(f"{'key_id':<14} {'prefix':<14} {'name':<16} {'status':<9} last_used", bold=True)
    for k in keys:
        status = "revoked" if k.revoked_at else "active"
        last_used = k.last_used_at.isoformat() if k.last_used_at else "never"
        typer.echo(f"{k.key_id:<14} {k.prefix:<14} {k.name or '-':<16} {status:<9} {last_used}")


@auth_app.command("revoke-key")
def auth_revoke_key(
    key_id: str = typer.Option(..., "--key-id", help="key_id to revoke"),
) -> None:
    """Revoke a key. It stops validating immediately; the row is kept for audit."""
    if not _auth_manager().revoke_key(key_id):
        typer.secho(
            f"No active key with id {key_id} (unknown or already revoked).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho(f"Revoked key {key_id}", fg=typer.colors.GREEN, err=True)


# ─── scheduler (spec 06) ────────────────────────────────────────────────────


def _wait_for_shutdown_signal() -> None:
    """Block the main thread until SIGINT/SIGTERM.

    `signal.pause()` is Unix-only; a never-set `Event.wait()` blocks the same way
    on every platform and still raises `KeyboardInterrupt` on Ctrl-C.
    """
    threading.Event().wait()


@app.command(name="scheduler-start")
def scheduler_start() -> None:
    """Start the eval scheduler and block (for systemd / a container's PID 1)."""
    from forensics.scheduler import get_scheduler

    # Same startup gate as `serve`/`worker`: an invalid webhook, cron string or
    # PII pattern must fail here, not hours later when the first alert fires.
    _setup()
    scheduler = get_scheduler(get_storage())
    scheduler.start()
    if not scheduler.settings.eval_schedule:
        typer.secho(
            "EVAL_SCHEDULE is empty, so nothing is scheduled. "
            "Set it (e.g. '0 2 * * *') and try again.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.secho(
        f"Scheduler started. Next eval: {scheduler.next_run_time()}",
        fg=typer.colors.GREEN,
        err=True,
    )
    try:
        _wait_for_shutdown_signal()
    except KeyboardInterrupt:
        scheduler.stop()


@app.command(name="scheduler-status")
def scheduler_status() -> None:
    """Show the eval schedule, the next run, and recent runs."""
    from forensics.scheduler import get_scheduler

    scheduler = get_scheduler(get_storage())
    status = scheduler.describe()

    typer.secho("\n⏰ Eval scheduler", fg=typer.colors.BLUE, bold=True)
    if not status["schedule"]:
        typer.secho("  Schedule: (none — EVAL_SCHEDULE is empty)", fg=typer.colors.YELLOW)
    else:
        typer.secho(f"  Schedule: {status['schedule']} ({status['timezone']})")
    typer.secho(f"  Running in this process: {'yes' if status['running'] else 'no'}")
    typer.secho(f"  Next eval: {status['next_eval'] or 'N/A'}")
    typer.secho(f"  Job store: {status['job_store']}")
    typer.secho(
        "  Alerts: "
        + (
            f"webhook configured, regression threshold "
            f"{status['alert_regression_threshold']:.0%}, "
            f"on_fixed={status['alert_on_fixed']}"
            if status["alert_webhook_configured"]
            else "no ALERT_WEBHOOK_URL configured"
        )
    )

    runs = scheduler.storage.list_eval_runs(limit=5)
    if runs:
        typer.secho("\n  Recent runs:", fg=typer.colors.BLUE, bold=True)
        for run in runs:
            accuracy = f"{run.passed_count / run.total_cases:.0%}" if run.total_cases else "n/a"
            typer.echo(
                f"  {run.run_id:<14} {run.started_at.isoformat()} "
                f"cases={run.total_cases:<4} passed={run.passed_count:<4} "
                f"failed={run.failed_count:<4} accuracy={accuracy}"
            )
    else:
        typer.secho("\n  No eval runs yet.", fg=typer.colors.YELLOW)


# ─── worker + queue (spec 07) ───────────────────────────────────────────────

worker_app = typer.Typer(help="Pipeline worker (spec 07)", no_args_is_help=True)
app.add_typer(worker_app, name="worker")
queue_app = typer.Typer(help="Enqueue and inspect pipeline jobs (spec 07)", no_args_is_help=True)
app.add_typer(queue_app, name="queue")


@worker_app.command("start")
def worker_start() -> None:
    """Run a worker in the foreground until SIGTERM/SIGINT.

    `_setup()` runs first: a worker that cannot call a model has nothing to do,
    so failing on a bad configuration here is better than failing per job.
    """
    import asyncio

    from forensics.queue import get_queue
    from forensics.worker import PipelineWorker

    _setup()
    settings = get_settings()
    typer.secho(
        f"Worker starting (concurrency={settings.queue_worker_concurrency}, "
        f"poll={settings.queue_poll_interval}s). Stop with Ctrl-C.",
        fg=typer.colors.GREEN,
        err=True,
    )
    worker = PipelineWorker(get_storage(), get_queue())
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        worker.stop()


@queue_app.command("enqueue")
def queue_enqueue(
    # `Annotated` for this parameter alone because it is a `Path`: ruff's B008
    # exemption for Typer only covers immutable annotations (str/int/bool/...),
    # and `exists=True` is worth keeping for validation and the generated --help.
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    doc_id: str = typer.Option(None, "--doc-id", help="Document id (defaults to the file stem)"),
    expected: str = typer.Option(None, "--expected", help="Expected document type (ground truth)"),
    priority: int = typer.Option(0, "--priority", help="Higher runs first"),
) -> None:
    """Queue a document for asynchronous processing."""
    from forensics.queue import get_queue

    expected_type: str | None = None
    if expected is not None:
        valid = [member.value for member in DocumentType]
        if expected not in valid:
            typer.secho(
                f"Unknown --expected {expected!r}. Valid values: {', '.join(valid)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        expected_type = expected

    resolved_doc_id = doc_id or file.stem
    # A doc id becomes a filename component (`documents/<id>.txt`) and has to
    # pass the same allowlist the API applies. An invalid id (a filename with a
    # space or a dot) used to be queued successfully and only failed later, in
    # the worker, as an opaque `Document(...)` validation error.
    if not is_valid_doc_id(resolved_doc_id):
        typer.secho(
            f"Invalid document id {resolved_doc_id!r}: use letters, digits, '-' and '_' "
            f"(max 64 characters), or pass --doc-id.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    job = get_queue().enqueue(
        DEFAULT_TENANT_ID,
        {
            "doc_id": resolved_doc_id,
            "doc_text": file.read_text(),
            "expected_type": expected_type,
        },
        priority=priority,
    )
    typer.secho(f"Queued job {job.job_id} for {resolved_doc_id}", fg=typer.colors.GREEN, err=True)
    typer.echo(job.job_id)


@queue_app.command("status")
def queue_status(job_id: str = typer.Option(..., "--job-id", help="Job to inspect")) -> None:
    """Show one job's status, result, and error."""
    from forensics.queue import get_queue

    job = get_queue().get_job(job_id)
    if job is None:
        typer.secho(f"No job with id {job_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    color = {
        "completed": typer.colors.GREEN,
        "failed": typer.colors.RED,
    }.get(job.status.value, typer.colors.YELLOW)
    typer.secho(f"\nJob {job.job_id}", fg=typer.colors.BLUE, bold=True)
    typer.secho(f"  Status: {job.status.value}", fg=color, bold=True)
    typer.echo(f"  Tenant: {job.tenant_id}")
    typer.echo(f"  Priority: {job.priority}  Attempts: {job.attempts}/{job.max_attempts}")
    typer.echo(f"  Created: {job.created_at}")
    typer.echo(f"  Started: {job.started_at or '-'}  Completed: {job.completed_at or '-'}")
    typer.echo(f"  Worker: {job.worker_id or '-'}")
    if job.error:
        typer.secho(f"  Error: {job.error}", fg=typer.colors.RED)
    if job.result:
        typer.echo(f"  Result: {json.dumps(job.result)}")


@queue_app.command("list")
def queue_list(
    status: str = typer.Option(None, "--status", help="Filter: pending|running|completed|failed"),
    tenant: str = typer.Option(None, "--tenant", help="Filter by tenant id"),
    limit: int = typer.Option(50, "--limit", help="Maximum rows"),
) -> None:
    """List queued jobs, newest first."""
    from forensics.queue import JobStatus, get_queue

    job_status: JobStatus | None = None
    if status is not None:
        try:
            job_status = JobStatus(status)
        except ValueError:
            valid = ", ".join(member.value for member in JobStatus)
            typer.secho(
                f"Unknown --status {status!r}. Valid values: {valid}", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=1) from None

    jobs = get_queue().list_jobs(tenant_id=tenant, status=job_status, limit=limit)
    if not jobs:
        typer.secho("No jobs match.", fg=typer.colors.YELLOW, err=True)
        return

    typer.secho(f"{'job_id':<18} {'status':<10} {'attempts':<9} {'doc_id':<20} created", bold=True)
    for job in jobs:
        doc_id = job.payload.get("doc_id", "-")
        typer.echo(
            f"{job.job_id:<18} {job.status.value:<10} "
            f"{f'{job.attempts}/{job.max_attempts}':<9} {doc_id!s:<20} {job.created_at}"
        )
    counts = get_queue().counts()
    typer.secho(
        "\n  Totals: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())),
        fg=typer.colors.BLUE,
    )


@queue_app.command("reaper")
def queue_reaper(
    stall_timeout: int = typer.Option(
        300, "--stall-timeout", help="Seconds before a running job is considered stalled"
    ),
) -> None:
    """Reclaim stalled jobs (running past their claim expiration).

    Useful for manual recovery after a worker crash, or to test the reaper logic.
    """
    _setup()
    from forensics.queue import get_queue

    reclaimed = get_queue().reclaim_stalled_jobs(stall_timeout=stall_timeout)
    typer.secho(f"Reclaimed {reclaimed} stalled jobs", fg=typer.colors.GREEN)


def main() -> None:
    """Console-script entry point declared in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
