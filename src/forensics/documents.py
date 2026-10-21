"""Document-id validation and safe document lookup.

A `document_id` arrives in a request body (`POST /pipeline/run`) or in a trace
file, and several code paths turn it into a filename
(`documents/<doc_id>.txt`). Without validation, `doc_id="../../secrets"` reads
any `.txt` file the process can see. Two guards live here so every caller
shares them:

* a strict allowlist (`^[A-Za-z0-9_-]{1,64}$`) applied at the input boundary,
* resolve-and-contain on the resulting path, so a symlink or a crafted id
  cannot escape the documents directory.

Uploaded documents are persisted under the *data* directory rather than the
read-only dataset, so a trace created by `POST /pipeline/run` stays diagnosable
and evaluable after the request that created it has returned. Lookup checks the
dataset first (labelled fixtures win) and then the upload directory.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from forensics.config import get_settings
from forensics.models import DEFAULT_TENANT_ID, Trace

#: Allowlist for a document id. Deliberately excludes `.`, `/`, `\`, and any
#: whitespace, so no id can express a path.
DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Same expression as a string, for pydantic `Field(pattern=...)`.
DOC_ID_REGEX = DOC_ID_PATTERN.pattern


def is_valid_doc_id(doc_id: object) -> bool:
    """True when `doc_id` is safe to use as a filename component."""
    return isinstance(doc_id, str) and DOC_ID_PATTERN.fullmatch(doc_id) is not None


def _tenant_component(tenant_id: str | None) -> str:
    """A filename-safe tenant directory name.

    Tenant ids are generated (`uuid4().hex[:12]`) or `default`, but the value can
    reach here from a database row, so it is validated with the same allowlist a
    document id uses. An invalid value lands in the default tenant rather than
    becoming a path component.
    """
    if tenant_id and is_valid_doc_id(tenant_id):
        return tenant_id
    return DEFAULT_TENANT_ID


def documents_dir(kind: str = "dataset", tenant_id: str | None = None) -> Path:
    """`dataset/documents` or a tenant's upload directory, from settings.

    Dataset documents are read-only fixtures and deliberately shared: they are
    checked in and identical for every tenant. Uploads are namespaced by tenant
    so two tenants submitting the same `doc_id` cannot overwrite each other's
    source text — which previously let tenant B poison every later diagnosis of
    tenant A's trace.
    """
    settings = get_settings()
    base = settings.dataset_dir if kind == "dataset" else settings.data_dir
    root = Path(base) / "documents"
    if kind == "dataset":
        return root
    return root / _tenant_component(tenant_id)


def contained_path(path: Path, root: Path) -> Path | None:
    """Resolve `path` and return it only when it stays inside `root`.

    Shared by every case where an id from outside the process is turned into a
    filename, so a trace id gets the same guarantee a document id does.
    """
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError:  # pragma: no cover - resolve() only raises on a path loop
        return None
    return resolved if resolved.is_relative_to(resolved_root) else None


def safe_document_path(
    doc_id: str, kind: str = "dataset", tenant_id: str | None = None
) -> Path | None:
    """Path for `doc_id` inside the documents directory, or None if unsafe."""
    if not is_valid_doc_id(doc_id):
        return None
    root = documents_dir(kind, tenant_id)
    return contained_path(root / f"{doc_id}.txt", root)


def _flat_uploads_dir() -> Path:
    """The pre-tenant-scoping flat upload directory (`data/documents`)."""
    return Path(get_settings().data_dir) / "documents"


def read_document(doc_id: str, tenant_id: str | None = None) -> str | None:
    """Read a document by id: labelled dataset, then this tenant's uploads.

    The legacy flat `data/documents/<doc_id>.txt` location is still consulted
    for the default tenant so documents stored before tenant scoping remain
    readable; a non-default tenant never falls back to it, because that is the
    path that leaked another tenant's text.
    """
    if not is_valid_doc_id(doc_id):
        return None

    roots: list[Path] = [documents_dir("dataset")]
    if tenant_id is None or tenant_id == DEFAULT_TENANT_ID:
        roots.append(documents_dir("uploads", DEFAULT_TENANT_ID))
        roots.append(_flat_uploads_dir())
    else:
        roots.append(documents_dir("uploads", tenant_id))

    for root in roots:
        path = contained_path(root / f"{doc_id}.txt", root)
        if path is not None and path.is_file():
            try:
                return path.read_text()
            except OSError:
                continue
    return None


def has_dataset_document(doc_id: str) -> bool:
    """True when the labelled dataset already ships this document."""
    path = safe_document_path(doc_id, "dataset")
    return path is not None and path.is_file()


def store_document(doc_id: str, text: str, tenant_id: str | None = None) -> Path | None:
    """Persist an uploaded document in the owning tenant's directory.

    Written under `data/documents/<tenant_id>/` (never the checked-in dataset)
    and atomically, so a crash mid-write cannot leave a half document that a
    later read treats as the real source text. The dataset always wins on
    lookup, so an upload cannot shadow a labelled fixture, and one tenant cannot
    overwrite another tenant's upload.
    """
    if not is_valid_doc_id(doc_id) or not text:
        return None

    root = documents_dir("uploads", tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    target = contained_path(root / f"{doc_id}.txt", root)
    if target is None:
        return None

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=root, prefix=f".{doc_id}.", suffix=".tmp", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(text)
        os.replace(tmp_path, target)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return None
    return target


def document_text_from_trace(trace: Trace) -> str:
    """Recover the source text from a trace's own intake span.

    The intake span records the normalized text the pipeline actually saw, so a
    trace whose document file is gone (an upload on an ephemeral disk, an eval
    case from before uploads were persisted) is still analyzable.
    """
    for span in trace.spans:
        if span.step_name != "intake":
            continue
        try:
            payload = json.loads(span.output_json or "{}")
        except ValueError:
            return ""
        if isinstance(payload, dict):
            cleaned = payload.get("cleaned_text")
            if isinstance(cleaned, str):
                return cleaned
        return ""
    return ""


def resolve_document_text(trace: Trace) -> str:
    """Best available source text for `trace`: file first, intake span second.

    The lookup is scoped to the trace's own tenant, so a trace is never
    evaluated against another tenant's document with the same id.
    """
    return read_document(trace.document_id, trace.tenant_id) or document_text_from_trace(trace)


__all__ = [
    "DOC_ID_PATTERN",
    "DOC_ID_REGEX",
    "contained_path",
    "document_text_from_trace",
    "documents_dir",
    "has_dataset_document",
    "is_valid_doc_id",
    "read_document",
    "resolve_document_text",
    "safe_document_path",
    "store_document",
]
