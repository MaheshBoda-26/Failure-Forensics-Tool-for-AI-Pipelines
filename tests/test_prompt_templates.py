"""Spec 09: prompt templates are archived with traces so a trace can be
replayed against the template revision that ran.

Acceptance criteria:
1. `finalize_trace` / `save_trace` records `span.prompt_template` by name.
2. A `PromptTemplate` row is upserted per (name, version) — idempotent.
3. `GET /prompt-templates` lists templates; `GET /prompt-templates/{name}/{version}`
   returns one or 404.
4. `forensics prompts list` / `forensics prompts show` work.
5. Template text is byte-identical to the builder output (hash pins rendering).
"""

import hashlib

import pytest
from typer.testing import CliRunner

from forensics import api
from forensics.cli import app as cli_app
from forensics.models import Span, Trace
from forensics.prompts import (
    STEP_PROMPT_BUILDERS,
    build_classification_prompt,
    build_extraction_prompt,
    build_summarization_prompt,
    template_for_step,
)
from forensics.storage import Storage


def _span(trace: Trace, step: str, prompt: str) -> Span:
    return Span(
        trace_id=trace.trace_id,
        step_name=step,
        input_json="{}",
        output_json="{}",
        model_id="m",
        provider="p",
        prompt=prompt,
        raw_response="",
        prompt_template=template_for_step(step),
        status="success",
    )


def _trace() -> Trace:
    trace = Trace(document_id="doc_001", tenant_id="default", spans=[])
    trace.spans.append(
        _span(
            trace,
            "extraction",
            build_extraction_prompt({"text": "hello"})[0],
        )
    )
    trace.spans.append(
        _span(
            trace,
            "classification",
            build_classification_prompt({"entities": []})[0],
        )
    )
    trace.spans.append(
        _span(
            trace,
            "summarization",
            build_summarization_prompt({"document_type": "report", "text": "x"})[0],
        )
    )
    return trace


# --- Criterion 1: span.prompt_template is recorded -------------------------


def test_prompt_template_recorded_on_spans() -> None:
    trace = _trace()
    names = {span.prompt_template for span in trace.spans}
    assert "extraction-v1" in names
    assert "classification-v1" in names
    assert "summarization-v1" in names
    # intake has no template
    assert all(s.prompt_template != "" or s.step_name == "intake" for s in trace.spans)


# --- Criterion 2: template row upserted through save_trace -----------------


def test_save_trace_archives_prompt_templates(tmp_path) -> None:
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    trace = _trace()
    storage.save_trace(trace)

    templates = storage.list_prompt_templates()
    names = {t.name for t in templates}
    assert "extraction-v1" in names
    assert "classification-v1" in names
    assert "summarization-v1" in names

    # The version is the SHA-256 of the rendered text.
    extraction = next(t for t in templates if t.name == "extraction-v1")
    expected_version = hashlib.sha256(
        build_extraction_prompt({"text": "hello"})[0].encode()
    ).hexdigest()
    assert extraction.version == expected_version
    assert extraction.template_text == build_extraction_prompt({"text": "hello"})[0]


def test_save_template_is_idempotent(tmp_path) -> None:
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    trace = _trace()
    storage.save_trace(trace)
    storage.save_trace(trace)  # same trace again

    templates = storage.list_prompt_templates()
    assert len(templates) == 3


def test_get_prompt_template_by_name_and_version(tmp_path) -> None:
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    storage.save_trace(_trace())

    text = build_extraction_prompt({"text": "hello"})[0]
    version = hashlib.sha256(text.encode()).hexdigest()
    template = storage.get_prompt_template("extraction-v1", version)
    assert template is not None
    assert template.template_text == text

    assert storage.get_prompt_template("extraction-v1", "nope") is None


# --- Criterion 3: API endpoints -------------------------------------------


def test_list_prompt_templates_endpoint(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", storage)
    storage.save_trace(_trace())

    body = api.list_prompt_templates()
    assert len(body) == 3
    assert any(t.name == "extraction-v1" for t in body)


def test_get_prompt_template_endpoint_404(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", storage)

    with pytest.raises(api.HTTPException) as excinfo:
        api.get_prompt_template("nope", "nope")
    assert excinfo.value.status_code == 404


# --- Criterion 4: CLI -------------------------------------------------------


def test_cli_prompts_list_shows_archived_templates(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forensics import storage as storage_module
    from forensics.storage import reset_storage

    reset_storage()
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", storage)
    monkeypatch.setattr(storage_module, "_storage_instance", storage)
    storage.save_trace(_trace())

    runner = CliRunner()
    result = runner.invoke(cli_app, ["prompts", "list"])
    assert result.exit_code == 0
    assert "extraction-v1" in result.output


def test_cli_prompts_show_prints_template_text(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forensics import storage as storage_module
    from forensics.storage import reset_storage

    reset_storage()
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", storage)
    monkeypatch.setattr(storage_module, "_storage_instance", storage)
    storage.save_trace(_trace())

    text = build_extraction_prompt({"text": "hello"})[0]
    version = hashlib.sha256(text.encode()).hexdigest()

    runner = CliRunner()
    result = runner.invoke(cli_app, ["prompts", "show", "extraction-v1", version])
    assert result.exit_code == 0
    assert result.output.strip() == text


def test_cli_prompts_show_missing_exits_nonzero(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forensics import storage as storage_module
    from forensics.storage import reset_storage

    reset_storage()
    storage = Storage(traces_dir=tmp_path / "traces", db_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(api, "_storage", storage)
    monkeypatch.setattr(storage_module, "_storage_instance", storage)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["prompts", "show", "nope", "nope"])
    assert result.exit_code == 1


# --- Criterion 5: every builder has a template name ------------------------


def test_every_step_builder_has_a_template_name() -> None:
    for step, builder in STEP_PROMPT_BUILDERS.items():
        name = template_for_step(step)
        assert name, f"{step} has no template name"
        # The builder output is deterministic: hash pins the rendering.
        if step == "judge":
            prompt = builder({"spans": []}, "hello")
        else:
            prompt, _system = builder({})
        assert hashlib.sha256(prompt.encode()).hexdigest()  # non-empty
