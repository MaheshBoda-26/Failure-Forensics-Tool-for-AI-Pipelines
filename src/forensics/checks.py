"""Deterministic check plugin registry.

Each check is a pure function that takes a Span, Trace, and document_text,
and returns a list of CheckResult. Checks are registered via the @check_plugin
decorator so developers can add custom domain checks in 10 lines of code.

Usage:
    from forensics.checks import check_plugin, CheckResult

    @check_plugin("my_custom_check", step="extraction")
    def my_check(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
        ...
"""

from collections.abc import Callable
from dataclasses import dataclass

from forensics.analyzer_simple import CheckResult
from forensics.models import Span, Trace


@dataclass(frozen=True)
class CheckPlugin:
    """Metadata for a registered check."""

    name: str
    step: str | None  # None = all steps
    func: Callable[[Span, Trace, str], list[CheckResult]]
    description: str


# Global registry of check plugins
_CHECK_REGISTRY: dict[str, CheckPlugin] = {}


def check_plugin(
    name: str,
    step: str | None = None,
    description: str = "",
) -> Callable[
    [Callable[[Span, Trace, str], list[CheckResult]]],
    Callable[[Span, Trace, str], list[CheckResult]],
]:
    """Decorator to register a deterministic check.

    Args:
        name: Unique check name (e.g., "entity_grounding", "my_custom_check")
        step: Pipeline step this check applies to (e.g., "extraction", "classification").
              If None, runs on all steps.
        description: Human-readable description of what the check validates.

    Returns:
        The original function, unchanged, for use as a normal function.
    """

    def decorator(
        func: Callable[[Span, Trace, str], list[CheckResult]],
    ) -> Callable[[Span, Trace, str], list[CheckResult]]:
        if name in _CHECK_REGISTRY:
            raise ValueError(f"Check '{name}' already registered")
        plugin = CheckPlugin(name=name, step=step, func=func, description=description)
        _CHECK_REGISTRY[name] = plugin
        return func

    return decorator


def get_check(name: str) -> CheckPlugin | None:
    """Get a registered check by name."""
    return _CHECK_REGISTRY.get(name)


def get_all_checks() -> list[CheckPlugin]:
    """Get all registered checks."""
    return list(_CHECK_REGISTRY.values())


def get_checks_for_step(step_name: str) -> list[CheckPlugin]:
    """Get all checks that apply to a specific step."""
    return [
        plugin
        for plugin in _CHECK_REGISTRY.values()
        if plugin.step is None or plugin.step == step_name
    ]


def run_registered_checks(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
    """Run all registered checks for a span's step."""
    results: list[CheckResult] = []
    for plugin in get_checks_for_step(span.step_name):
        try:
            results.extend(plugin.func(span, trace, document_text))
        except Exception as e:
            # A misbehaving check should not crash the analyzer
            results.append(
                CheckResult(
                    check_name=plugin.name,
                    passed=False,
                    evidence=f"Check raised exception: {e}",
                    step_name=span.step_name,
                )
            )
    return results


# Re-export the built-in checks so they can be registered
def register_builtin_checks() -> None:
    """Register the 7 built-in deterministic checks."""
    from forensics.analyzer_simple import (
        check_context_preservation,
        check_date_regex,
        check_entity_grounding,
        check_label_enum,
        check_label_grounding,
        check_prompt_schema,
        check_propagation,
    )

    check_plugin(
        "entity_grounding",
        step="extraction",
        description="Extracted entity value must appear in source document",
    )(check_entity_grounding)
    check_plugin(
        "label_enum",
        step="classification",
        description="Classification output must be a valid DocumentType",
    )(check_label_enum)
    check_plugin(
        "label_grounding",
        step="classification",
        description="Classification must match ground-truth label when available",
    )(check_label_grounding)
    check_plugin(
        "date_regex",
        step="extraction",
        description="Date entities must match YYYY-MM-DD or recognized formats",
    )(check_date_regex)
    check_plugin(
        "context_preservation",
        step="summarization",
        description="Extraction entities must appear in summarization output",
    )(check_context_preservation)
    check_plugin(
        "propagation",
        step="summarization",
        description="Downstream steps must correctly consume upstream outputs",
    )(check_propagation)
    check_plugin(
        "prompt_schema",
        step=None,
        description="LLM outputs must conform to expected schema structure",
    )(check_prompt_schema)
