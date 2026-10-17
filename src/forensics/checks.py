"""Deterministic check plugin registry.

Each check is a pure function that takes a Span, Trace, and document_text,
and returns a list of CheckResult. Checks are registered via the @check_plugin
decorator so developers can add custom domain checks in 10 lines of code.

Call protocol
-------------
A plugin is called as ``plugin(span, trace, document_text)`` and must return a
``list[CheckResult]``. Built-in checks whose historical signature is narrower
are adapted inside :func:`register_builtin_checks`, so the public convention is
uniform for custom plugins.

Ground truth
------------
``label_grounding`` is the one built-in that cannot see the ground-truth
document type from ``(span, trace, document_text)``. Plugins may declare
``requires_ground_truth=True``; they are then called as
``plugin(span, expected_type)`` and are skipped silently when the analyzer was
run without labels.

Usage:
    from forensics.checks import check_plugin, CheckResult

    @check_plugin("my_custom_check", step="extraction")
    def my_check(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
        ...

Import graph note
-----------------
This module depends on ``forensics.analyzer_simple`` (for ``CheckResult`` and
the built-in implementations), so ``analyzer_simple`` must never import this
module at import time. It imports the runner lazily inside
``run_deterministic_checks``. Keeping the dependency one-directional is what
lets ``import forensics.checks`` and ``import forensics.analyzer_simple``
succeed in either order.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from forensics.models import Span, Trace

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph acyclic
    from forensics.analyzer_simple import CheckResult

PluginFunc = Callable[..., "list[CheckResult]"]


@dataclass(frozen=True)
class CheckPlugin:
    """Metadata for a registered check."""

    name: str
    step: str | None  # None = all steps
    func: PluginFunc
    description: str
    # Called as func(span, expected_type) instead of func(span, trace, document_text).
    requires_ground_truth: bool = False
    # Human-readable source, for diagnostics: "builtin" or a module:attr spec.
    origin: str = "builtin"


# Global registry of check plugins. Insertion order is the run order.
_CHECK_REGISTRY: dict[str, CheckPlugin] = {}

# Names of the seven built-in deterministic checks, in registration order.
BUILTIN_CHECK_NAMES: tuple[str, ...] = (
    "entity_grounding",
    "label_enum",
    "label_grounding",
    "date_regex",
    "context_preservation",
    "propagation",
    "prompt_schema",
)


def _register(plugin: CheckPlugin, *, replace: bool = False) -> CheckPlugin:
    """Insert a plugin into the registry. Raises on a duplicate name by default."""
    if not replace and plugin.name in _CHECK_REGISTRY:
        raise ValueError(f"Check '{plugin.name}' already registered")
    _CHECK_REGISTRY[plugin.name] = plugin
    return plugin


def check_plugin(
    name: str,
    step: str | None = None,
    description: str = "",
    *,
    requires_ground_truth: bool = False,
) -> Callable[[Any], Any]:
    """Decorator to register a deterministic check.

    Args:
        name: Unique check name (e.g., "entity_grounding", "my_custom_check").
        step: Pipeline step this check applies to (e.g., "extraction").
              If None, runs on all steps.
        description: Human-readable description of what the check validates.
        requires_ground_truth: When True the check is invoked as
            ``func(span, expected_type)``.

    Returns:
        The original function/class, unchanged, so it can still be used directly.
    """

    def decorator(target: Any) -> Any:
        func = target() if inspect.isclass(target) else target
        if not callable(func):
            raise TypeError(f"Check '{name}' must decorate a function or callable class")
        plugin = CheckPlugin(
            name=name,
            step=step,
            func=func,
            description=description or (inspect.getdoc(target) or "").split("\n")[0],
            requires_ground_truth=requires_ground_truth,
            origin=getattr(target, "_check_plugin_origin", "builtin"),
        )
        _register(plugin)
        # Stamp the target so load_plugins_from_config can find plugins whose
        # decorator auto-registered them on import.
        for attr, value in (
            ("_check_plugin_name", name),
            ("_check_plugin_step", step),
            ("_check_plugin_requires_ground_truth", requires_ground_truth),
        ):
            with contextlib.suppress(AttributeError, TypeError):
                setattr(target, attr, value)
        return target

    return decorator


def get_check(name: str) -> CheckPlugin | None:
    """Get a registered check by name."""
    return _CHECK_REGISTRY.get(name)


def get_all_checks() -> list[CheckPlugin]:
    """Get all registered checks, in registration order."""
    return list(_CHECK_REGISTRY.values())


def get_checks_for_step(step_name: str) -> list[CheckPlugin]:
    """Get all checks that apply to a specific step, in registration order."""
    return [
        plugin
        for plugin in _CHECK_REGISTRY.values()
        if plugin.step is None or plugin.step == step_name
    ]


def clear_registry() -> None:
    """Drop every registered check. Test-support only."""
    _CHECK_REGISTRY.clear()


def run_registered_checks(
    span: Span,
    trace: Trace,
    document_text: str,
    expected_type: str | None = None,
) -> list[CheckResult]:
    """Run all registered checks for a span's step.

    ``expected_type`` is the dataset's ground-truth document type; plugins that
    declare ``requires_ground_truth`` receive it and are skipped when it is None.
    """
    from forensics.analyzer_simple import CheckResult

    results: list[CheckResult] = []
    for plugin in get_checks_for_step(span.step_name):
        try:
            if plugin.requires_ground_truth:
                if expected_type is None:
                    continue
                results.extend(plugin.func(span, expected_type))
            else:
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


# --- Plugin loading from configuration --------------------------------------


def _coerce_plugin(obj: Any, spec: str) -> CheckPlugin:
    """Turn an imported object into a CheckPlugin, or explain why it is not one."""
    name = getattr(obj, "_check_plugin_name", None)
    if name is None:
        raise ValueError(
            f"CHECK_PLUGINS entry '{spec}' is not a @check_plugin "
            f"(decorate it with @check_plugin(name, step=...))"
        )
    func = obj() if inspect.isclass(obj) else obj
    if not callable(func):
        raise ValueError(f"CHECK_PLUGINS entry '{spec}' resolves to a non-callable plugin")
    return CheckPlugin(
        name=name,
        step=getattr(obj, "_check_plugin_step", None),
        func=func,
        description=(inspect.getdoc(obj) or "").split("\n")[0],
        requires_ground_truth=getattr(obj, "_check_plugin_requires_ground_truth", False),
        origin=spec,
    )


def load_plugins_from_config(plugin_specs: str | None) -> list[str]:
    """Register external plugins named by a comma-separated ``module:attr`` list.

    The offending spec is named in every error, so a bad value fails fast at
    startup with something actionable instead of a bare ImportError.

    Returns the names of plugins that became newly registered.
    """
    if not plugin_specs or not plugin_specs.strip():
        return []

    loaded: list[str] = []
    for raw in plugin_specs.split(","):
        spec = raw.strip()
        if not spec:
            continue
        if ":" not in spec:
            raise ValueError(
                f"Invalid CHECK_PLUGINS entry '{spec}': expected 'module:attr' "
                f"(e.g. 'my_checks:check_pii_in_summary')"
            )
        module_path, _, attr_name = spec.partition(":")
        module_path, attr_name = module_path.strip(), attr_name.strip()
        if not module_path or not attr_name:
            raise ValueError(
                f"Invalid CHECK_PLUGINS entry '{spec}': both module and attribute are required"
            )

        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            raise ValueError(f"Cannot import CHECK_PLUGINS entry '{spec}': {e}") from e

        try:
            obj = getattr(module, attr_name)
        except AttributeError as e:
            raise ValueError(
                f"CHECK_PLUGINS entry '{spec}': module '{module_path}' has no attribute "
                f"'{attr_name}'"
            ) from e

        plugin = _coerce_plugin(obj, spec)

        # The @check_plugin decorator already registers a plugin when its module
        # is imported. Either way the plugin is now available because of this
        # spec, so it is reported as loaded.
        if plugin.name not in _CHECK_REGISTRY:
            _register(plugin)
        loaded.append(plugin.name)

    return loaded


# --- Built-in checks --------------------------------------------------------
#
# The implementations live in ``analyzer_simple`` (that is where the domain
# logic and its unit tests are). Their historical signatures are narrower than
# the uniform plugin protocol, so they are adapted here rather than rewritten:
# rewriting them would silently change the public functions the tests call.

_BUILTIN_DESCRIPTIONS: dict[str, tuple[str | None, str]] = {
    "entity_grounding": ("extraction", "Extracted entity value must appear in source document"),
    "label_enum": ("classification", "Classification output must be a valid DocumentType"),
    "label_grounding": (
        "classification",
        "Classification must match ground-truth label when available",
    ),
    "date_regex": ("extraction", "Date entities must match YYYY-MM-DD or recognized formats"),
    "context_preservation": (
        "summarization",
        "Extraction entities must appear in summarization output",
    ),
    "propagation": ("summarization", "Downstream steps must correctly consume upstream outputs"),
    "prompt_schema": (None, "LLM outputs must conform to expected schema structure"),
}


def register_builtin_checks() -> None:
    """Register the 7 built-in deterministic checks. Idempotent."""
    from forensics.analyzer_simple import (
        check_context_preservation,
        check_date_regex,
        check_entity_grounding,
        check_label_enum,
        check_label_grounding,
        check_prompt_schema,
        check_propagation,
    )

    def _span_only(fn: Callable[[Span], list[CheckResult]]) -> PluginFunc:
        def adapter(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
            return fn(span)

        return adapter

    def _span_and_doc(fn: Callable[[Span, str], list[CheckResult]]) -> PluginFunc:
        def adapter(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
            return fn(span, document_text)

        return adapter

    def _span_and_trace(fn: Callable[[Span, Trace], list[CheckResult]]) -> PluginFunc:
        def adapter(span: Span, trace: Trace, document_text: str) -> list[CheckResult]:
            return fn(span, trace)

        return adapter

    implementations: dict[str, tuple[PluginFunc, bool]] = {
        "entity_grounding": (_span_and_doc(check_entity_grounding), False),
        "label_enum": (_span_only(check_label_enum), False),
        "label_grounding": (check_label_grounding, True),  # (span, expected_type)
        "date_regex": (_span_only(check_date_regex), False),
        "context_preservation": (check_context_preservation, False),
        "propagation": (_span_and_trace(check_propagation), False),
        "prompt_schema": (_span_only(check_prompt_schema), False),
    }

    for name in BUILTIN_CHECK_NAMES:
        if name in _CHECK_REGISTRY:
            continue
        step, description = _BUILTIN_DESCRIPTIONS[name]
        func, needs_truth = implementations[name]
        _register(
            CheckPlugin(
                name=name,
                step=step,
                func=func,
                description=description,
                requires_ground_truth=needs_truth,
                origin="builtin",
            )
        )


def __getattr__(name: str) -> Any:
    """Expose ``from forensics.checks import CheckResult`` without a real import.

    CheckResult is defined in ``analyzer_simple``; importing it eagerly here
    would re-create the cycle this module deliberately avoids.
    """
    if name == "CheckResult":
        from forensics.analyzer_simple import CheckResult

        return CheckResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BUILTIN_CHECK_NAMES",
    "CheckPlugin",
    "CheckResult",
    "check_plugin",
    "clear_registry",
    "get_all_checks",
    "get_check",
    "get_checks_for_step",
    "load_plugins_from_config",
    "register_builtin_checks",
    "run_registered_checks",
]


# Register built-ins when this module is first imported. The import below is
# safe because analyzer_simple no longer imports checks at import time.
register_builtin_checks()
