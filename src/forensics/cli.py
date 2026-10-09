"""Command line entry point.

Subcommands: seed (generate the labeled dataset), run (execute one document),
analyze, eval, analytics. Exposed as the `forensics` console script and as
`python -m forensics.cli`.
"""

import sys
from pathlib import Path

import typer

from forensics.config import get_settings, validate_startup

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
    typer.secho("Pipeline execution not yet implemented (Phase 1)", fg=typer.colors.YELLOW)
    raise typer.Exit(code=1)


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
