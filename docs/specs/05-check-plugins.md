# SPEC: Custom Check Plugin API

## Summary
Allow users to register custom deterministic checks via import paths in env. Built-in 7 checks become plugins. Zero code changes to add checks.

## Config (`.env`)
```bash
CHECK_PLUGINS=my_checks:MyCustomCheck,other.module:AnotherCheck
# Comma-separated import paths: "module:ClassName" or "module:function_name"
```

## Plugin Protocol

### `src/forensics/checks.py` — New
```python
"""Check plugin system. Built-in checks re-exported as plugins."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from forensics.models import Span, Trace
from forensics.analyzer_simple import CheckResult


@runtime_checkable
class CheckPlugin(Protocol):
    """A deterministic check plugin."""

    name: str  # unique identifier, e.g. "entity_grounding"
    applies_to: list[str]  # step names this check runs on

    def __call__(
        self,
        span: Span,
        trace: Trace,
        document_text: str,
    ) -> CheckResult: ...


# Decorator for easy registration
def check_plugin(name: str, applies_to: list[str]):
    def decorator(cls_or_fn):
        cls_or_fn._check_plugin_name = name
        cls_or_fn._check_plugin_applies_to = applies_to
        return cls_or_fn

    return decorator


# Registry
_plugin_registry: dict[str, CheckPlugin] = {}


def register_plugin(plugin: CheckPlugin) -> None:
    if plugin.name in _plugin_registry:
        raise ValueError(f"Check plugin '{plugin.name}' already registered")
    _plugin_registry[plugin.name] = plugin


def get_plugin(name: str) -> CheckPlugin | None:
    return _plugin_registry.get(name)


def get_plugins_for_step(step_name: str) -> list[CheckPlugin]:
    return [p for p in _plugin_registry.values() if step_name in p.applies_to]


def load_plugins_from_config(plugin_specs: str) -> None:
    """Load plugins from comma-separated import paths."""
    if not plugin_specs.strip():
        return
    for spec in plugin_specs.split(","):
        spec = spec.strip()
        if not spec:
            continue
        module_path, attr_name = spec.split(":", 1)
        module = __import__(module_path, fromlist=[attr_name])
        plugin = getattr(module, attr_name)
        if not hasattr(plugin, "_check_plugin_name"):
            raise ValueError(f"{spec} is not a @check_plugin")
        register_plugin(plugin)
```

## Built-in Checks as Plugins

### `src/forensics/analyzer_simple.py` — Refactor
```python
from forensics.checks import check_plugin, register_plugin

@check_plugin("entity_grounding", applies_to=["extraction"])
def check_entity_grounding(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("label_enum", applies_to=["classification"])
def check_label_enum(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("date_regex", applies_to=["extraction"])
def check_date_regex(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("context_preservation", applies_to=["summarization"])
def check_context_preservation(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("propagation", applies_to=["summarization"])
def check_propagation(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("label_grounding", applies_to=["classification"])
def check_label_grounding(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

@check_plugin("prompt_schema", applies_to=["extraction", "classification", "summarization"])
def check_prompt_schema(span: Span, trace: Trace, document_text: str) -> CheckResult:
    # ... existing logic ...

# Auto-register built-ins
for name, fn in [
    ("entity_grounding", check_entity_grounding),
    ("label_enum", check_label_enum),
    ("date_regex", check_date_regex),
    ("context_preservation", check_context_preservation),
    ("propagation", check_propagation),
    ("label_grounding", check_label_grounding),
    ("prompt_schema", check_prompt_schema),
]:
    register_plugin(fn)
```

### `src/forensics/analyzer_simple.py` — `aggregate_checks` uses registry
```python
from forensics.checks import get_plugins_for_step

def analyze_trace(trace: Trace, document_text: str, ...) -> Diagnosis:
    step_scores = []
    for span in trace.spans:
        checks = {}
        for plugin in get_plugins_for_step(span.step_name):
            result = plugin(span, trace, document_text)
            checks[plugin.name] = result.passed
            # collect evidence...
        # aggregate with AND as before
```

## User Plugin Example
```python
# my_checks.py
from forensics.checks import check_plugin, CheckResult
from forensics.models import Span, Trace


@check_plugin("pii_in_summary", applies_to=["summarization"])
def check_pii_in_summary(span: Span, trace: Trace, document_text: str) -> CheckResult:
    import re

    output = json.loads(span.output_json)
    summary = output.get("summary", "")
    # Check for email/phone in summary that wasn't in source
    pii_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    found = pii_pattern.findall(summary)
    source_pii = pii_pattern.findall(document_text)
    leaked = [p for p in found if p not in source_pii]
    return CheckResult(
        check_name="pii_in_summary",
        passed=len(leaked) == 0,
        evidence=f"Leaked PII: {leaked}" if leaked else "No PII leaked",
        step_name=span.step_name,
    )
```

Usage:
```bash
CHECK_PLUGINS=my_checks:check_pii_in_summary
```

## Acceptance Criteria
1. Built-in 7 checks work exactly as before (same names, same logic)
2. `CHECK_PLUGINS=` loads custom plugins at startup
3. Custom plugin runs on matching steps, appears in `deterministic_checks`
4. Plugin evidence appears in diagnosis evidence chain
5. Plugin can override root cause if it fails first (priority by registration order)
6. Invalid plugin spec → clear error at startup
7. Plugin can be class with `__call__` or function

## Effort
- **Files**: 1 new (`checks.py`), 1 refactored (`analyzer_simple.py`)
- **Time**: ~3 hours
- **Risk**: Low (decorator pattern, explicit registry)
