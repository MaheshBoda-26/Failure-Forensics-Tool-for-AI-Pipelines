"""Spec 05: the deterministic checks are a plugin registry, not a closed set.

Acceptance criteria exercised here:

1. The 7 built-ins still exist with the same names, steps and verdicts.
2. ``CHECK_PLUGINS`` loads ``module:attr`` specs.
3. A custom plugin runs on its step and shows up in ``deterministic_checks``.
4. Its evidence reaches the diagnosis evidence chain.
5. A plugin failure changes the root cause the analyzer names.
6. A bad spec raises a clear error naming the offending entry.
7. A plugin may be a function *or* a class with ``__call__``.

The registry is process-global by design (plugins are loaded once at startup),
so an autouse fixture snapshots and restores it: without that, importing the
sample plugin module in one test would leak its checks into every later test in
the session and silently change unrelated diagnoses.
"""

import json
from collections.abc import Iterator

import pytest

from forensics.analyzer_simple import CheckResult, analyze_trace, run_deterministic_checks
from forensics.checks import (
    BUILTIN_CHECK_NAMES,
    check_plugin,
    get_all_checks,
    get_check,
    get_checks_for_step,
    load_plugins_from_config,
)
from forensics.models import FailureCategory, Span, Trace

SAMPLE_MODULE = "tests.plugin_samples"


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    """Undo any plugin registration a test performs."""
    from forensics import checks

    saved = dict(checks._CHECK_REGISTRY)
    yield
    checks._CHECK_REGISTRY.clear()
    checks._CHECK_REGISTRY.update(saved)


def _span(step: str, output: dict) -> Span:
    return Span(
        trace_id="t",
        step_name=step,
        input_json="{}",
        output_json=json.dumps(output),
        model_id="m",
        provider="p",
        prompt="",
        raw_response="",
        status="success",
    )


# --- Criterion 1: built-ins are unchanged ----------------------------------


def test_builtin_checks_keep_their_names_steps_and_origin() -> None:
    expected_steps = {
        "entity_grounding": "extraction",
        "label_enum": "classification",
        "label_grounding": "classification",
        "date_regex": "extraction",
        "context_preservation": "summarization",
        "propagation": "summarization",
        "prompt_schema": None,
    }
    for name in BUILTIN_CHECK_NAMES:
        plugin = get_check(name)
        assert plugin is not None, f"built-in '{name}' is not registered"
        assert plugin.step == expected_steps[name]
        assert plugin.origin == "builtin"

    assert [c.name for c in get_all_checks()][: len(BUILTIN_CHECK_NAMES)] == list(
        BUILTIN_CHECK_NAMES
    )


def test_builtin_verdicts_are_unchanged_through_the_registry() -> None:
    """The hallucination path must still work now that checks run via plugins."""
    doc = "Invoice total is 500.00 USD"
    trace = Trace(
        document_id="d1",
        spans=[
            _span("intake", {"cleaned_text": doc}),
            _span(
                "extraction",
                {"entities": [{"value": "ghost", "entity_type": "PERSON"}]},
            ),
        ],
    )

    diagnosis = analyze_trace(trace, doc)

    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION
    assert diagnosis.root_cause_step == "extraction"


def test_registering_builtins_twice_is_a_no_op() -> None:
    from forensics.checks import register_builtin_checks

    before = [c.name for c in get_all_checks()]
    register_builtin_checks()
    assert [c.name for c in get_all_checks()] == before


# --- Criterion 2: CHECK_PLUGINS loads specs --------------------------------


def test_load_plugins_from_config_registers_a_function_plugin() -> None:
    loaded = load_plugins_from_config(f"{SAMPLE_MODULE}:no_shouting")

    assert loaded == ["no_shouting"]
    plugin = get_check("no_shouting")
    assert plugin is not None
    assert plugin.step == "summarization"


def test_load_plugins_from_config_ignores_blank_config() -> None:
    assert load_plugins_from_config("") == []
    assert load_plugins_from_config(None) == []
    assert load_plugins_from_config("  ,  ") == []


def test_load_plugins_from_config_accepts_multiple_specs() -> None:
    loaded = load_plugins_from_config(
        f"{SAMPLE_MODULE}:no_shouting,{SAMPLE_MODULE}:RequiresInvoiceWord"
    )

    assert loaded == ["no_shouting", "requires_invoice_word"]


# --- Criterion 3: runs on matching steps, appears in deterministic_checks ---


def test_custom_plugin_runs_only_on_its_step() -> None:
    load_plugins_from_config(f"{SAMPLE_MODULE}:no_shouting")
    doc = "Contract between Acme and Beta."
    trace = Trace(
        document_id="d1",
        spans=[
            _span("intake", {"cleaned_text": doc}),
            _span("extraction", {"entities": []}),
            _span("classification", {"document_type": "contract", "confidence": 0.9}),
            _span("summarization", {"summary": "LOUD NOISE", "key_points": ["a", "b"]}),
        ],
    )

    results = run_deterministic_checks(trace, doc)

    extraction, summarization = trace.spans[1], trace.spans[3]
    assert all(c.check_name != "no_shouting" for c in results.get(extraction.span_id, []))
    summarization_checks = {c.check_name: c for c in results[summarization.span_id]}
    assert summarization_checks["no_shouting"].passed is False


def test_custom_plugin_verdict_lands_in_step_scores() -> None:
    load_plugins_from_config(f"{SAMPLE_MODULE}:no_shouting")
    doc = "Contract between Acme and Beta."
    trace = Trace(
        document_id="d1",
        spans=[
            _span("intake", {"cleaned_text": doc}),
            _span("extraction", {"entities": []}),
            _span("classification", {"document_type": "contract", "confidence": 0.9}),
            _span("summarization", {"summary": "LOUD NOISE", "key_points": ["a", "b"]}),
        ],
    )

    diagnosis = analyze_trace(trace, doc)

    scores = {score.step_name: score for score in diagnosis.step_scores}
    assert scores["summarization"].deterministic_checks["no_shouting"] is False


def test_plugin_without_a_step_applies_to_every_step() -> None:
    @check_plugin("all_steps_probe", description="Runs everywhere")
    def all_steps_probe(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
        return []

    for step in ("intake", "extraction", "classification", "summarization"):
        assert any(p.name == "all_steps_probe" for p in get_checks_for_step(step))


# --- Criterion 4: evidence reaches the diagnosis ---------------------------


def test_plugin_evidence_appears_in_the_evidence_chain() -> None:
    load_plugins_from_config(f"{SAMPLE_MODULE}:no_shouting")
    doc = "Contract between Acme and Beta."
    trace = Trace(
        document_id="d1",
        spans=[
            _span("intake", {"cleaned_text": doc}),
            _span("extraction", {"entities": []}),
            _span("classification", {"document_type": "contract", "confidence": 0.9}),
            _span("summarization", {"summary": "LOUD NOISE", "key_points": ["a", "b"]}),
        ],
    )

    diagnosis = analyze_trace(trace, doc)

    assert any("ALL CAPS" in evidence for evidence in diagnosis.evidence_chain)


# --- Criterion 5: a failing plugin changes the root cause ------------------


def _contract_trace() -> Trace:
    return Trace(
        document_id="d1",
        spans=[
            _span("intake", {"cleaned_text": "Contract between Acme and Beta."}),
            _span("extraction", {"entities": []}),
        ],
    )


def test_without_the_plugin_the_trace_passes() -> None:
    diagnosis = analyze_trace(_contract_trace(), "Contract between Acme and Beta.")

    assert diagnosis.root_cause_step == "none"


def test_plugin_failure_becomes_the_root_cause() -> None:
    load_plugins_from_config(f"{SAMPLE_MODULE}:RequiresInvoiceWord")

    diagnosis = analyze_trace(_contract_trace(), "Contract between Acme and Beta.")

    assert diagnosis.root_cause_step == "extraction"
    assert diagnosis.category is FailureCategory.UNKNOWN
    assert any("does not contain 'invoice'" in e for e in diagnosis.evidence_chain)


# --- Criterion 6: bad specs fail loudly ------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected_fragment"),
    [
        ("no_colon_here", "Invalid CHECK_PLUGINS entry 'no_colon_here'"),
        (":missing_module", "Invalid CHECK_PLUGINS entry ':missing_module'"),
        ("missing_attr:", "Invalid CHECK_PLUGINS entry 'missing_attr:'"),
        (
            "definitely_not_a_module_xyz:thing",
            "Cannot import CHECK_PLUGINS entry 'definitely_not_a_module_xyz:thing'",
        ),
        (
            f"{SAMPLE_MODULE}:no_such_attribute",
            f"CHECK_PLUGINS entry '{SAMPLE_MODULE}:no_such_attribute': module "
            f"'{SAMPLE_MODULE}' has no attribute 'no_such_attribute'",
        ),
        (
            f"{SAMPLE_MODULE}:undecorated",
            f"CHECK_PLUGINS entry '{SAMPLE_MODULE}:undecorated' is not a @check_plugin",
        ),
    ],
)
def test_bad_plugin_specs_raise_a_clear_error(spec: str, expected_fragment: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        load_plugins_from_config(spec)

    assert expected_fragment in str(excinfo.value)


# --- Criterion 7: classes work as plugins ----------------------------------


def test_class_plugin_is_instantiated_and_runs() -> None:
    loaded = load_plugins_from_config(f"{SAMPLE_MODULE}:RequiresInvoiceWord")

    assert loaded == ["requires_invoice_word"]
    plugin = get_check("requires_invoice_word")
    assert plugin is not None

    span = _span("extraction", {"entities": []})
    trace = Trace(document_id="d1", spans=[span])
    results = plugin.func(span, trace, "a contract between two parties")

    assert len(results) == 1
    assert results[0].check_name == "requires_invoice_word"
    assert results[0].passed is False


# --- Settings wiring -------------------------------------------------------


def test_settings_exposes_check_plugins() -> None:
    from forensics.config import Settings

    settings = Settings(check_plugins="my_checks:probe")

    assert settings.check_plugins == "my_checks:probe"
