"""Command line entry point.

Subcommands: seed (generate the labeled dataset), run (execute one document),
analyze, eval, analytics, validate-dataset, demo. Exposed as the `forensics`
console script and as `python -m forensics.cli`.
"""

import json
import sys
from pathlib import Path

import typer

from forensics.config import get_settings, validate_startup
from forensics.models import Document
from forensics.pipeline import Pipeline
from forensics.storage import Storage
from forensics.tracing import trace_context

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
    """Test OpenRouter connectivity and print model info."""
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


@app.command()
def seed() -> None:
    """Generate the 50-document dataset with ground-truth labels."""
    _setup()
    typer.secho("Dataset generation not yet implemented (Phase 1)", fg=typer.colors.YELLOW)
    typer.echo("This will create 50 documents in dataset/documents/ and labels.json")
    raise typer.Exit(code=1)


@app.command()
def run(doc_path: Path) -> None:
    """Run the pipeline on a single document."""
    _setup()
    if not doc_path.exists():
        typer.secho(f"Document not found: {doc_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    doc = Document(content=doc_path.read_text())
    pipeline = Pipeline()

    with trace_context(document_id=doc_path.stem) as ctx:
        result = pipeline.run(doc)
        storage = Storage()
        json_path = storage.save_trace(ctx.trace)

    typer.secho(f"Pipeline completed: {doc_path.stem}", fg=typer.colors.GREEN)
    typer.echo(f"Trace saved to: {json_path}")
    typer.echo(f"Trace ID: {ctx.trace.trace_id}")
    typer.echo(f"Intake: {result.intake.char_count} chars")
    typer.echo(f"Extraction: {len(result.extraction.entities)} entities")
    cls = result.classification.document_type.value
    conf = result.classification.confidence
    typer.echo(f"Classification: {cls} ({conf:.2f})")
    typer.echo(f"Summary: {result.summarization.summary[:80]}...")


@app.command(name="validate-dataset")
def validate_dataset() -> None:
    """Validate the dataset against labels.json."""
    _setup()
    labels_path = Path("dataset/labels.json")
    if not labels_path.exists():
        typer.secho("labels.json not found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    with open(labels_path) as f:
        labels = json.load(f)

    errors = []
    warnings = []

    for doc_info in labels.get("documents", []):
        doc_id = doc_info["doc_id"]
        doc_path = Path(f"dataset/documents/{doc_id}.txt")

        if not doc_path.exists():
            errors.append(f"Missing document file: {doc_path}")
            continue

        # Verify document is readable
        try:
            content = doc_path.read_text()
            if not content.strip():
                warnings.append(f"Empty document: {doc_path}")
        except Exception as e:
            errors.append(f"Cannot read {doc_path}: {e}")

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
    labels_path = Path("dataset/labels.json")
    if not labels_path.exists():
        typer.secho("labels.json not found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    with open(labels_path) as f:
        labels = json.load(f)

    storage = Storage()
    pipeline = Pipeline()

    results = []
    for doc_info in labels.get("documents", []):
        doc_id = doc_info["doc_id"]
        doc_path = Path(f"dataset/documents/{doc_id}.txt")

        if not doc_path.exists():
            typer.secho(f"Skipping missing: {doc_id}", fg=typer.colors.YELLOW)
            continue

        doc = Document(content=doc_path.read_text())
        with trace_context(document_id=doc_id) as ctx:
            result = pipeline.run(doc)
            storage.save_trace(ctx.trace)

        expected_type = doc_info.get("doc_type", "unknown")
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
            }
        )

        status = "✓" if match else "✗"
        color = typer.colors.GREEN if match else typer.colors.RED
        msg = f"  {status} {doc_id}: expected={expected_type}, actual={actual_type}"
        typer.secho(msg, fg=color)

    # Summary
    total = len(results)
    correct = sum(1 for r in results if r["match"])
    seeded_failures = sum(1 for r in results if r["should_fail"])

    typer.echo()
    summary_color = typer.colors.GREEN if correct == total else typer.colors.YELLOW
    typer.secho(f"SUMMARY: {correct}/{total} classifications correct", fg=summary_color)
    typer.echo(f"Seeded failures in dataset: {seeded_failures}")
    typer.echo(f"Traces saved: {total}")


@app.command()
def eval() -> None:
    """Re-run the accumulated eval dataset."""
    _setup()
    typer.secho("Eval engine not yet implemented (Phase 5)", fg=typer.colors.YELLOW)
    raise typer.Exit(code=1)


@app.command()
def analytics() -> None:
    """Show analytics dashboard."""
    _setup()
    typer.secho("Analytics not yet implemented (Phase 5)", fg=typer.colors.YELLOW)
    raise typer.Exit(code=1)


@app.command()
def validate_config() -> None:
    """Validate configuration without running anything."""
    _setup()
    typer.secho("Configuration valid", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
