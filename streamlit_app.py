"""Streamlit Trace Explorer for Failure Forensics.

Run: streamlit run streamlit_app.py

Features:
- Trace list with filtering/sorting
- Interactive pipeline node graph (color-coded by status)
- Span detail panel with input/output/prompt/raw_response
- Diff view: expected vs actual output vs grounded truth
- Flagging interface with diagnosis confirm/override
- Evidence chain display
"""

import html
import itertools
import json
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from forensics.analyzer_simple import analyze_trace
from forensics.eval_simple import create_eval_cases_from_confirmed_flags
from forensics.models import FailureCategory, Flag, Trace
from forensics.storage import Storage

# ──────────────────────────────────────────────────────────────────────────────
# Page Config & Styles
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Failure Forensics - Trace Explorer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* Span status badges */
.span-success {
    background-color: #d4edda;
    color: #155724;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 500;
}
.span-warning {
    background-color: #fff3cd;
    color: #856404;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 500;
}
.span-failure {
    background-color: #f8d7da;
    color: #721c24;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 500;
}

/* Diff colors */
.diff-added { background-color: #d4edda !important; }
.diff-removed { background-color: #f8d7da !important; }
.diff-changed { background-color: #fff3cd !important; }

/* Node status colors for graph legend */
.node-success { background-color: #28a745; color: white; padding: 2px 8px; border-radius: 4px; }
.node-warning { background-color: #ffc107; color: #212529; padding: 2px 8px; border-radius: 4px; }
.node-failure { background-color: #dc3545; color: white; padding: 2px 8px; border-radius: 4px; }

/* Evidence items */
.evidence-item {
    background-color: #f8f9fa;
    border-left: 3px solid #007bff;
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 0 4px 4px 0;
}

/* Flag button */
.flag-button {
    background-color: #dc3545;
    color: white;
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: 600;
}

/* Scrollable containers */
.scrollable-json { max-height: 400px; overflow-y: auto; }

/* Hide streamlit branding */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Tighten spacing */
.block-container { padding-top: 2rem; }
</style>
""",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# Storage & Data Loading (Cached)
# ──────────────────────────────────────────────────────────────────────────────


@st.cache_resource
def get_storage() -> Storage:
    """Get storage instance (cached)."""
    return Storage()


@st.cache_data(ttl=30)
def load_traces(limit: int = 200) -> list:
    """Load all traces from storage."""
    storage = get_storage()
    return storage.list_traces(limit=limit)


@st.cache_data(ttl=30)
def load_labels() -> dict:
    """Load ground truth labels."""
    labels_path = Path("dataset/labels.json")
    if labels_path.exists():
        with open(labels_path) as f:
            return json.load(f)
    return {"documents": []}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

STATUS_COLOR_MAP = {
    "success": ("✅", "#28a745", "span-success"),
    "failure": ("❌", "#dc3545", "span-failure"),
    "degraded": ("⚠️", "#ffc107", "span-warning"),
}

CATEGORY_ICONS = {
    "extraction_hallucination": "👻",
    "misclassification": "🏷️",
    "propagation_error": "🔗",
    "prompt_failure": "📝",
    "context_loss": "🧠",
    "unknown": "❓",
}


def get_span_badge(span_status: str) -> tuple[str, str, str]:
    """Return (icon, color_hex, css_class) for span status."""
    return STATUS_COLOR_MAP.get(span_status, ("❓", "#6c757d", "span-warning"))


def get_span_status_text(checks: dict) -> str:
    """Get human-readable status for span based on check results."""
    if not checks:
        return "⚠️ No checks"
    if all(checks.values()):
        return "✅ All checks pass"
    failed = [k for k, v in checks.items() if not v]
    return f"❌ Failed: {', '.join(failed)}"


def get_category_display(category: str) -> str:
    """Format failure category for display."""
    icon = CATEGORY_ICONS.get(category, "❓")
    return f"{icon} {category.replace('_', ' ').title()}"


def safe_json_loads(json_str: str) -> dict:
    """Safely parse JSON, returning dict on failure."""
    try:
        return json.loads(json_str) if json_str else {}
    except Exception:
        return {"_raw": json_str}


def load_doc_text(doc_id: str) -> str:
    """Load document text from disk."""
    doc_path = Path(f"dataset/documents/{doc_id}.txt")
    try:
        resolved_path = doc_path.resolve()
        base_path = Path("dataset/documents").resolve()
        if not resolved_path.is_relative_to(base_path) or not resolved_path.exists():
            return ""
        return resolved_path.read_text()
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline Graph Visualization (NetworkX + Plotly)
# ──────────────────────────────────────────────────────────────────────────────

PIPELINE_STEPS = ["intake", "extraction", "classification", "summarization"]


def status_str(status: object) -> str:
    """Normalize a Span.status or TraceStatus to its plain string value.

    `Span.status` is a plain string Literal (no `.value`); `TraceStatus` is a
    StrEnum (has `.value`). Reading `.value` off a span status raised
    AttributeError on first render.
    """
    value = getattr(status, "value", status)
    return str(value)


def build_pipeline_graph(trace: Trace) -> go.Figure:
    """Create an interactive pipeline graph.

    The graph is a fixed 4-node chain, so it is built directly. It previously
    used NetworkX purely to hold four nodes and three edges, which added an
    undeclared dependency to a script that could not even import.
    """
    # Per-step display data, in pipeline order.
    node_meta: list[dict[str, str]] = []
    for step in PIPELINE_STEPS:
        span = next((s for s in trace.spans if s.step_name == step), None)
        if span:
            icon, color, _ = get_span_badge(status_str(span.status))
            node_meta.append(
                {
                    "step": step,
                    "label": f"{icon} {step.title()}",
                    "color": color,
                    "tooltip": (f"{step} | {span.latency_ms}ms | {status_str(span.status)}"),
                }
            )
        else:
            node_meta.append(
                {
                    "step": step,
                    "label": f"(pending) {step.title()}",
                    "color": "#6c757d",
                    "tooltip": f"{step} | Not executed",
                }
            )

    # Fixed horizontal layout.
    pos = {m["step"]: (i * 2.5, 0) for i, m in enumerate(node_meta)}

    # Edges: consecutive steps in the chain.
    edge_x, edge_y = [], []
    for a, b in itertools.pairwise(PIPELINE_STEPS):
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        line=dict(width=2, color="#999"),
        hoverinfo="none",
        mode="lines",
        showlegend=False,
    )

    # Node traces.
    node_x = [pos[m["step"]][0] for m in node_meta]
    node_y = [pos[m["step"]][1] for m in node_meta]
    node_text = [m["label"] for m in node_meta]
    node_color = [m["color"] for m in node_meta]
    node_hover = [m["tooltip"] for m in node_meta]

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=node_text,
        textposition="top center",
        textfont=dict(size=12, color="#333"),
        hovertext=node_hover,
        hoverinfo="text",
        marker=dict(
            size=45,
            color=node_color,
            line=dict(width=2, color="#fff"),
            symbol="circle",
        ),
        showlegend=False,
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(
            title=dict(text="Pipeline Flow", font=dict(size=16), x=0.5),
            showlegend=False,
            hovermode="closest",
            margin=dict(b=40, l=40, r=40, t=60),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            height=200,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        ),
    )

    return fig


# ──────────────────────────────────────────────────────────────────────────────
# Diff Utilities
# ──────────────────────────────────────────────────────────────────────────────


def diff_strings(expected: str, actual: str) -> list[tuple[str, str]]:
    """Return list of (type, text) where type is 'equal', 'added', 'removed'."""
    import difflib

    diff = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        lineterm="",
    )
    result = []
    for line in diff:
        if line.startswith("+"):
            result.append(("added", line[1:]))
        elif line.startswith("-"):
            result.append(("removed", line[1:]))
        elif not line.startswith("@@"):
            result.append(("equal", line))
    return result


def render_diff(expected: Any, actual: Any) -> None:
    """Render side-by-side diff with color coding."""
    exp_str = json.dumps(expected, indent=2, default=str)
    act_str = json.dumps(actual, indent=2, default=str)

    diffs = diff_strings(exp_str, act_str)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Expected**")
        for typ, text in diffs:
            if typ == "removed":
                st.markdown(
                    f"<span class='diff-removed'>{html.escape(text)}</span>", unsafe_allow_html=True
                )
            elif typ == "equal":
                st.text(text)
    with col2:
        st.markdown("**Actual**")
        for typ, text in diffs:
            if typ == "added":
                st.markdown(
                    f"<span class='diff-added'>{html.escape(text)}</span>", unsafe_allow_html=True
                )
            elif typ == "equal":
                st.text(text)


# ──────────────────────────────────────────────────────────────────────────────
# Span Detail Rendering
# ──────────────────────────────────────────────────────────────────────────────


def render_span_detail(span, step_scores: list, doc_text: str) -> None:
    """Render a detailed span view with all available data."""
    score = next((s for s in step_scores if s.step_name == span.step_name), None)
    checks = score.deterministic_checks if score else {}
    icon, _, css_class = get_span_badge(status_str(span.status))
    _ = get_span_status_text(checks)  # for side effect if any

    # Header
    col1, col2, col3 = st.columns([4, 1, 1])
    with col1:
        st.markdown(
            f'<div class="{html.escape(css_class)}">'
            f"{html.escape(str(icon))} "
            f"<strong>{html.escape(str(span.step_name).title())}</strong>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.metric("Latency", f"{span.latency_ms}ms")
    with col3:
        st.metric("Status", status_str(span.status).title())

    # Input / Output / Prompt / Raw Response
    try:
        input_data = safe_json_loads(span.input_json)
        output_data = safe_json_loads(span.output_json)
    except Exception:
        input_data = {"_raw": span.input_json}
        output_data = {"_raw": span.output_json}

    tabs = st.tabs(["📥 Input", "📤 Output", "💬 Prompt", "📄 Raw Response"])

    with tabs[0]:
        st.json(input_data, expanded=False)

    with tabs[1]:
        st.json(output_data, expanded=False)

    with tabs[2]:
        if span.prompt:
            st.text_area("Prompt sent to LLM", span.prompt, height=200, disabled=True)
        else:
            st.info("No prompt recorded")

    with tabs[3]:
        if span.raw_response:
            st.text_area("Raw LLM Response", span.raw_response, height=200, disabled=True)
        else:
            st.info("No raw response recorded")

    # Token usage & cost
    if span.prompt_tokens or span.completion_tokens:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Prompt Tokens", span.prompt_tokens)
        with col2:
            st.metric("Completion Tokens", span.completion_tokens)
        with col3:
            st.metric("Reasoning Tokens", span.reasoning_tokens)

    # Checks & Evidence
    if checks:
        st.markdown("**Deterministic Checks**")
        for name, passed in checks.items():
            icon = "✅" if passed else "❌"
            st.write(f"{icon} {name.replace('_', ' ').title()}")

    if score and score.evidence:
        with st.expander("🔍 Evidence Chain", expanded=True):
            for ev in score.evidence:
                st.markdown(
                    f'<div class="evidence-item">• {html.escape(str(ev))}</div>',
                    unsafe_allow_html=True,
                )


# ──────────────────────────────────────────────────────────────────────────────
# Diff View
# ──────────────────────────────────────────────────────────────────────────────


def render_diff_view(trace: Trace, doc_text: str, labels: dict) -> None:
    """Render side-by-side diff: expected vs actual output."""
    st.subheader("📊 Diff View: Expected vs Actual")

    doc_id = trace.document_id
    label = next((d for d in labels.get("documents", []) if d["doc_id"] == doc_id), None)

    if not label:
        st.info("No ground truth label for this document")
        return

    expected_type = label.get("doc_type", "unknown")
    should_fail = label.get("should_fail", False)
    failure_category = label.get("failure_category")

    # Header
    msg = (
        f"**Document:** `{doc_id}` | **Expected:** `{expected_type}`"
        f" | **Seeded Failure:** {should_fail}"
    )
    st.write(msg)

    for span in trace.spans:
        try:
            output_data = safe_json_loads(span.output_json)
        except Exception:
            output_data = {}

        if span.step_name == "classification":
            actual_type = output_data.get("document_type", "unknown")
            match = actual_type == expected_type

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**Expected**")
                st.code(expected_type)
            with col2:
                st.markdown(f"**Actual** {'✅' if match else '❌'}")
                st.code(actual_type)
            with col3:
                if not match:
                    st.error(f"Seeded: {failure_category}")

        elif span.step_name == "extraction":
            entities = output_data.get("entities", [])
            st.markdown(f"**Extracted Entities ({len(entities)})**")
            if entities:
                for ent in entities:
                    if isinstance(ent, dict):
                        val = ent.get("value", "")
                        typ = ent.get("entity_type", "")
                        start = ent.get("start_offset", "")
                        end = ent.get("end_offset", "")
                    else:
                        val = getattr(ent, "value", "")
                        typ = getattr(ent, "entity_type", "")
                        start = getattr(ent, "start_offset", "")
                        end = getattr(ent, "end_offset", "")

                    in_doc = val in doc_text
                    status = "✅" if in_doc else "❌"
                    st.write(f"{status} **{val}** ({typ}) [{start}:{end}]")

        elif span.step_name == "intake":
            cleaned = output_data.get("cleaned_text", "")
            st.markdown(f"**Cleaned Text ({len(cleaned)} chars)**")
            st.text_area("", cleaned[:500], height=100, disabled=True, key=f"intake_{span.span_id}")

        elif span.step_name == "summarization":
            summary = output_data.get("summary", "")
            key_points = output_data.get("key_points", [])
            st.markdown("**Summary**")
            st.text_area("", summary, height=100, disabled=True, key=f"summ_{span.span_id}")
            if key_points:
                st.markdown("**Key Points**")
                for kp in key_points:
                    st.write(f"• {kp}")


# ──────────────────────────────────────────────────────────────────────────────
# Flagging Interface
# ──────────────────────────────────────────────────────────────────────────────


def render_flag_interface(trace: Trace, diagnosis, storage: Storage, doc_text: str) -> None:
    """Render flagging interface with confirm/override."""
    st.divider()
    st.subheader("🚩 Flag & Diagnosis")

    col1, col2 = st.columns([1, 2])

    with col1:
        # Flag button
        if st.button("🚩 Flag This Trace", type="primary", use_container_width=True):
            # Link the flag to the current diagnosis so the flag→eval path can
            # trace back to a specific analyzer verdict.
            flag = Flag(
                trace_id=trace.trace_id,
                user_id="streamlit_user",
                note="Flagged via Streamlit UI for review",
                diagnosis_id=diagnosis.diagnosis_id if diagnosis else None,
            )
            storage.create_flag(flag)
            st.success(f"Flagged! Flag ID: {flag.flag_id}")
            st.rerun()

        # Existing flags
        flags = storage.list_flags_for_trace(trace.trace_id)
        if flags:
            st.caption(f"Existing flags: {len(flags)}")
            for f in flags:
                st.caption(f"• {f.flag_id[:12]} by {f.user_id}")

    with col2:
        # Diagnosis display with confirm/override
        if diagnosis.root_cause_step != "none":
            cat_display = get_category_display(diagnosis.category.value)
            root_cause = f"**Root Cause:** {diagnosis.root_cause_step} — {cat_display}"
            st.error(root_cause)
            st.metric("Confidence", f"{diagnosis.confidence:.0%}")

            with st.expander("📋 Evidence Chain", expanded=True):
                for ev in diagnosis.evidence_chain:
                    st.markdown(
                        f'<div class="evidence-item">• {html.escape(str(ev))}</div>',
                        unsafe_allow_html=True,
                    )

            # Confirm/Override
            st.divider()
            st.markdown("**Human Review**")

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("✅ Confirm Diagnosis", type="secondary", use_container_width=True):
                    # Confirm the diagnosis itself. `Storage` has no
                    # `update_flag`; confirmation lives on the diagnosis row and
                    # is what `create_eval_cases_from_confirmed_flags` gates on.
                    storage.update_diagnosis_confirmation(
                        diagnosis.diagnosis_id,
                        confirmed=True,
                        overridden=diagnosis.overridden,
                        override_category=(
                            diagnosis.override_category.value
                            if diagnosis.override_category
                            else None
                        ),
                        override_reason=diagnosis.override_reason,
                    )
                    # Feed the confirmed flag into the eval suite the same way
                    # the FastAPI route does; the sweep is idempotent (dedup on
                    # (trace, failing step)) so re-clicking cannot inflate it.
                    create_eval_cases_from_confirmed_flags()
                    st.success("Diagnosis confirmed!")
                    st.rerun()

            with col_b:
                if st.button("✏️ Override Diagnosis", use_container_width=True):
                    st.session_state[f"override_{trace.trace_id}"] = True
                    st.rerun()

            # Override form
            if st.session_state.get(f"override_{trace.trace_id}", False):
                with st.form(f"override_form_{trace.trace_id}"):
                    st.markdown("**Override Diagnosis**")
                    new_category = st.selectbox(
                        "New Category",
                        options=[c.value for c in FailureCategory],
                        format_func=lambda x: get_category_display(x),
                    )
                    override_reason = st.text_area("Reason for Override")
                    submitted = st.form_submit_button("Submit Override")
                    if submitted:
                        storage.update_diagnosis_confirmation(
                            diagnosis.diagnosis_id,
                            confirmed=True,
                            overridden=True,
                            override_category=new_category,
                            override_reason=override_reason,
                        )
                        st.success("Diagnosis overridden and confirmed!")
                        create_eval_cases_from_confirmed_flags()
                        st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# Main App
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    st.title("🔍 Failure Forensics - Trace Explorer")

    storage = get_storage()
    labels = load_labels()

    # ─── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("🎛️ Controls")

        if st.button("🔄 Refresh Traces", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption("Filters")

        status_filter = st.selectbox("Trace Status", ["All", "success", "failure", "degraded"])
        category_filter = st.selectbox(
            "Failure Category",
            ["All"]
            + [
                c.value
                for c in __import__(
                    "forensics.models", fromlist=["FailureCategory"]
                ).FailureCategory
            ],
            format_func=lambda x: "All" if x == "All" else get_category_display(x),
        )

        st.divider()
        st.caption("Quick Actions")
        if st.button("📊 Run Demo", use_container_width=True):
            import subprocess

            result = subprocess.run(
                ["python", "-m", "forensics.cli", "demo"],
                capture_output=True,
                text=True,
                cwd=Path.cwd(),
            )
            if result.returncode == 0:
                st.success("Demo completed!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(result.stderr)

    # ─── Load Traces ──────────────────────────────────────────────────────────
    traces = load_traces(limit=200)

    if status_filter != "All":
        traces = [t for t in traces if status_str(t.status) == status_filter]

    if category_filter != "All":
        # `Trace` has no `diagnosis` attribute: diagnoses live in their own
        # table. Reading `t.diagnosis` raised AttributeError as soon as a
        # category other than "All" was selected, so join via storage.
        storage = Storage()
        matching: list[Trace] = []
        for t in traces:
            diag = storage.get_diagnosis_for_trace(t.trace_id)
            if diag is None:
                continue
            effective = diag.override_category or diag.category
            if str(getattr(effective, "value", effective)) == category_filter:
                matching.append(t)
        traces = matching

    if not traces:
        st.warning("No traces found. Run `python -m forensics.cli demo` to generate traces.")
        return

    # ─── Trace List Table ─────────────────────────────────────────────────────
    st.subheader(f"📋 Traces ({len(traces)})")

    trace_data = []
    for trace in traces:
        trace_data.append(
            {
                "Trace ID": trace.trace_id[:12],
                "Document": trace.document_id,
                "Status": status_str(trace.status).title(),
                "Spans": len(trace.spans),
                "Latency (ms)": trace.total_latency_ms,
                "Created": trace.created_at.strftime("%H:%M:%S") if trace.created_at else "N/A",
            }
        )

    # Clickable trace selection
    trace_df = st.dataframe(
        trace_data,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    # ─── Trace Detail ─────────────────────────────────────────────────────────
    selected_idx = trace_df.selection.rows[0] if trace_df.selection.rows else 0
    selected_trace = traces[selected_idx] if selected_idx < len(traces) else traces[0]

    doc_text = load_doc_text(selected_trace.document_id)
    # Fetch the persisted diagnosis instead of rebuilding one on every render:
    # a fresh `analyze_trace` result has a diagnosis_id that exists only in
    # memory, so Confirm/Override would run an UPDATE against 0 rows. If no
    # stored diagnosis exists yet, compute it (with the dataset's ground-truth
    # label) and persist it so the human-review buttons can act on it.
    diagnosis = storage.get_diagnosis_for_trace(selected_trace.trace_id)
    if diagnosis is None:
        expected_type = None
        for doc_info in labels.get("documents", []):
            if doc_info.get("doc_id") == selected_trace.document_id:
                expected_type = doc_info.get("doc_type", "unknown")
                break
        diagnosis = analyze_trace(selected_trace, doc_text, expected_type)
        storage.create_diagnosis(diagnosis)

    # ─── Pipeline Graph ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("🔗 Pipeline Flow")

    graph_fig = build_pipeline_graph(selected_trace)
    st.plotly_chart(graph_fig, use_container_width=True, config={"displayModeBar": False})

    # ─── Diagnosis Summary ────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Root Cause", diagnosis.root_cause_step or "None")
    with col2:
        st.metric("Category", get_category_display(diagnosis.category.value))
    with col3:
        st.metric("Confidence", f"{diagnosis.confidence:.0%}")
    with col4:
        st.metric("Spans", len(selected_trace.spans))

    # ─── Pipeline Spans Detail ────────────────────────────────────────────────
    st.divider()
    st.subheader("📍 Pipeline Spans")

    for span in selected_trace.spans:
        badge = get_span_badge(status_str(span.status))[0]
        label = (
            f"{badge} {span.step_name.title()} — {status_str(span.status)} — {span.latency_ms}ms"
        )
        with st.expander(label, expanded=False):
            render_span_detail(span, diagnosis.step_scores, doc_text)

    # ─── Diff View ────────────────────────────────────────────────────────────
    st.divider()
    render_diff_view(selected_trace, doc_text, labels)

    # ─── Flag & Diagnosis Interface ──────────────────────────────────────────
    storage = get_storage()
    render_flag_interface(selected_trace, diagnosis, storage, doc_text)


if __name__ == "__main__":
    main()
