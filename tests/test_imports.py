"""Guards against the import cycle that made the CLI/API/analyzer unimportable.

At HEAD `checks.py` imported `CheckResult` from `analyzer_simple` at module
scope while `analyzer_simple` imported `checks` at module scope, so
`import forensics.api` raised:

    ImportError: cannot import name 'CheckResult' from partially initialized
    module 'forensics.analyzer_simple' (most likely due to a circular import)

These tests run in a *fresh interpreter per order* on purpose: an in-process
import cache would hide the regression, and import order is exactly the
variable that broke.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "forensics"


def _module_level_runtime_imports(path: Path) -> set[str]:
    """Every module imported at the *top level* of `path`, ignoring lazy and
    TYPE_CHECKING-only imports (which are nested inside a function or an `if`)."""
    tree = ast.parse(path.read_text())
    seen: set[str] = set()
    for node in tree.body:  # top-level statements only
        if isinstance(node, ast.ImportFrom) and node.module:
            seen.add(node.module)
        elif isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
    return seen


IMPORT_SNIPPETS: tuple[str, ...] = (
    # The order the CLI/API use in production.
    "import forensics.api; import forensics.cli; import forensics.checks; print('OK')",
    # The reverse order a plugin author would trigger.
    "import forensics.checks; import forensics.api; import forensics.cli; print('OK')",
    # analyzer_simple in isolation (its lazy import of the runner must not fire).
    "import forensics.analyzer_simple; print('OK')",
    # Deep-import the pieces the cycle actually touched.
    "from forensics.checks import CheckResult, get_all_checks; print('OK')",
)


@pytest.mark.parametrize("snippet", IMPORT_SNIPPETS)
def test_imports_succeed_in_a_fresh_interpreter(snippet: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"fresh import failed:\n{snippet}\n{result.stderr}"
    assert "OK" in result.stdout


def test_checks_re_exports_check_result() -> None:
    """`from forensics.checks import check_plugin, CheckResult` is the documented
    plugin-authoring import (spec 05), so it must work without importing
    analyzer_simple first."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from forensics.checks import check_plugin, CheckResult; "
            "r = CheckResult('x', True, 'e', 'extraction'); "
            "assert r.passed is True; print('OK')",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_no_module_level_back_edge_between_checks_and_analyzer() -> None:
    """The cycle's defining property: neither module may import the other at
    module scope. Lazy/`TYPE_CHECKING` imports are allowed and are how the fix
    works, so this inspects top-level statements only."""
    checks_imports = _module_level_runtime_imports(SRC / "checks.py")
    analyzer_imports = _module_level_runtime_imports(SRC / "analyzer_simple.py")

    assert "forensics.analyzer_simple" not in checks_imports, (
        "checks.py imports analyzer_simple at module level again; "
        "that re-creates the cycle with analyzer_simple's lazy runner import"
    )
    assert "forensics.checks" not in analyzer_imports, (
        "analyzer_simple.py imports checks at module level again; that re-creates the cycle"
    )


def test_importing_checks_registers_the_builtins() -> None:
    """Importing the plugin module alone must populate the registry, in order."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from forensics.checks import BUILTIN_CHECK_NAMES, get_all_checks; "
            "names = [c.name for c in get_all_checks()]; "
            "assert names == list(BUILTIN_CHECK_NAMES), names; print('OK')",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
