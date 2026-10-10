"""Streamlit Trace Explorer for Failure Forensics.

Run: streamlit run streamlit_app.py
"""

import json
from pathlib import Path

import streamlit as st

from forensics.analyzer_simple import analyze_trace
from forensics.storage import Storage

st.set_page_config(
    page_title="Failure Forensics - Trace Explorer",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for color coding
st.markdown(
    """
<style>
.span-success { background-color: #d4edda; color: #155724; padding: 4px 8px; border-radius: 4px; }
.span-warning { background-color: #fff3cd; color: #856404; padding: 4px 8px; border-radius: 4px; }
.span-failure { background-color: #f8d7da; color: #721c24; padding: 4px 8px; border-radius: 4px; }
.diff-added { background-color: #d4edda; }
.diff-removed { background-color: #f8d7da; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_storage() -> Storage:
    """Get storage instance (cached)."""
    return Storage()


@st.cache_data(ttl=30)
def load_traces() -> list:
    """Load all traces from storage."""
    storage = get_storage()
    return storage.list_traces(limit=100)


@st.cache_data(ttl=30)
def load_labels() -> dict:
    """Load ground truth labels."""
    labels_path = Path("dataset/labels.json")
    if labels_path.exists():
        with open(labels_path) as f:
            return json.load(f)
    return {"documents": []}


def get_span_color(checks: dict) -> str:
    """Determine span color based on check results."""
    if not checks:
        return "span-warning"  # No checks = unknown
    if all(checks.values()):
        return "span-success"
    return "span-failure"


def get_span_status_text(checks: dict) -> str:
    """Get human-readable status for span."""
    if not checks:
        return "⚠️ No checks"
    if all(checks.values()):
        return "✅ All checks pass"
    failed = [k for k, v in checks.items() if not v]
    return f"❌ Failed: {', '.join(failed)}"


def render_span_card(span, step_scores: list, doc_text: str) -> None:
    """Render a single span as a card with color coding."""
    # Find matching step score
    score = next((s for s in step_scores if s.step_name == span.step_name), None)
    checks = score.deterministic_checks if score else {}
    color_class = get_span_color(checks)
    status_text = get_span_status_text(checks)

    with st.container():
        col1, col2 = st.columns([3, 1])
        with col1:
            html = (
                f'<div class="{color_class}">'
                f"<strong>{span.step_name}</strong> - {status_text}"
                f"</div>"
            )
            st.markdown(html, unsafe_allow_html=True)
        with col2:
            st.caption(f"{span.latency_ms}ms | {span.status.value}")

        # Show input/output in expandable sections
        with st.expander(f"📥 Input / 📤 Output ({span.step_name})", expanded=False):
            try:
                input_data = json.loads(span.input_json) if span.input_json else {}
                output_data = json.loads(span.output_json) if span.output_json else {}
            except Exception:
                input_data = {"raw": span.input_json}
                output_data = {"raw": span.output_json}

            c1, c2 = st.columns(2)
            with c1:
                st.code(json.dumps(input_data, indent=2, default=str), language="json")
            with c2:
                st.code(json.dumps(output_data, indent=2, default=str), language="json")

        # Show evidence if any checks failed
        if score and score.evidence:
            with st.expander("🔍 Evidence", expanded=True):
                for ev in score.evidence:
                    st.write(f"• {ev}")


def render_diff_view(trace, doc_text: str, labels: dict) -> None:
    """Render diff view: input vs output vs expected."""
    st.subheader("📊 Diff View: Input vs Output vs Expected")

    # Find label for this trace
    doc_id = trace.document_id
    label = next((d for d in labels.get("documents", []) if d["doc_id"] == doc_id), None)

    if not label:
        st.info("No ground truth label for this document")
        return

    expected_type = label.get("doc_type", "unknown")
    should_fail = label.get("should_fail", False)
    failure_step = label.get("failure_step")
    failure_category = label.get("failure_category")

    parts = [
        f"**Document:** {doc_id}",
        f"**Expected Type:** {expected_type}",
        f"**Seeded Failure:** {should_fail}",
    ]
    st.write(" | ".join(parts))

    # Show spans with diff
    for span in trace.spans:
        try:
            output_data = json.loads(span.output_json) if span.output_json else {}
        except Exception:
            output_data = {}

        if span.step_name == "classification":
            actual_type = output_data.get("document_type", "unknown")
            match = actual_type == expected_type
            status = "✅ MATCH" if match else "❌ MISMATCH"

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown("**Expected**")
                st.code(expected_type)
            with col2:
                st.markdown(f"**Actual ({status})**")
                st.code(actual_type)
            with col3:
                if not match and failure_step == "classification":
                    st.error(f"Seeded: {failure_category}")

        elif span.step_name == "extraction":
            entities = output_data.get("entities", [])
            st.markdown(f"**Extracted Entities ({len(entities)})**")
            if entities:
                for ent in entities:
                    val = (
                        ent.get("value", "") if isinstance(ent, dict) else getattr(ent, "value", "")
                    )
                    typ = (
                        ent.get("entity_type", "")
                        if isinstance(ent, dict)
                        else getattr(ent, "entity_type", "")
                    )
                    in_doc = val in doc_text
                    status = "✅" if in_doc else "❌"
                    st.write(f"{status} {val} ({typ})")

        elif span.step_name == "intake":
            cleaned = output_data.get("cleaned_text", "")
            st.markdown(f"**Cleaned Text ({len(cleaned)} chars)**")
            st.text_area("", cleaned[:500], height=100, disabled=True)

        elif span.step_name == "summarization":
            summary = output_data.get("summary", "")
            st.markdown("**Summary**")
            st.text_area("", summary, height=100, disabled=True)


def main():
    st.title("🔍 Failure Forensics - Trace Explorer")

    storage = get_storage()
    labels = load_labels()

    # Sidebar
    with st.sidebar:
        st.header("Controls")
        if st.button("🔄 Refresh Traces"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.caption("Trace filters")
        status_filter = st.selectbox("Status", ["All", "success", "failure", "degraded"])

    # Load traces
    traces = load_traces()

    if status_filter != "All":
        traces = [t for t in traces if t.status.value == status_filter]

    if not traces:
        st.warning("No traces found. Run `python -m forensics.cli demo` to generate traces.")
        return

    # Trace list table
    st.subheader(f"📋 Traces ({len(traces)})")

    trace_data = []
    for trace in traces:
        trace_data.append(
            {
                "Trace ID": trace.trace_id[:12],
                "Document": trace.document_id,
                "Status": trace.status.value,
                "Spans": len(trace.spans),
                "Latency (ms)": trace.total_latency_ms,
                "Created": trace.created_at.strftime("%H:%M:%S") if trace.created_at else "N/A",
            }
        )

    st.dataframe(trace_data, use_container_width=True, hide_index=True)

    # Trace detail
    st.divider()
    st.subheader("🔬 Trace Detail")

    selected_trace_id = st.selectbox(
        "Select trace to inspect",
        options=[t.trace_id for t in traces],
        format_func=lambda x: (
            f"{x[:12]} - {next(t.document_id for t in traces if t.trace_id == x)}"
        ),
    )

    selected_trace = next(t for t in traces if t.trace_id == selected_trace_id)
    doc_text = ""
    # Try to load document content
    doc_path = Path(f"dataset/documents/{selected_trace.document_id}.txt")
    if doc_path.exists():
        doc_text = doc_path.read_text()

    # Run analyzer
    diagnosis = analyze_trace(selected_trace, doc_text)

    # Diagnosis summary
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Root Cause", diagnosis.root_cause_step)
    with col2:
        st.metric("Category", diagnosis.category.value)
    with col3:
        st.metric("Confidence", f"{diagnosis.confidence:.0%}")

    # Flag button
    st.divider()
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🚩 Flag This Trace", type="primary", use_container_width=True):
            from forensics.models import Flag

            flag = Flag(
                trace_id=selected_trace.trace_id,
                user_id="streamlit_user",
                note="Flagged via Streamlit UI",
            )
            storage.create_flag(flag)
            st.success(f"Flagged! Flag ID: {flag.flag_id}")
            st.rerun()
    with col2:
        # Show existing flags
        flags = storage.list_flags_for_trace(selected_trace.trace_id)
        if flags:
            st.caption(f"Existing flags: {len(flags)}")

    # Diagnosis details
    if diagnosis.root_cause_step != "none":
        st.error(f"**Root Cause:** {diagnosis.root_cause_step} ({diagnosis.category.value})")
        with st.expander("📋 Evidence Chain", expanded=True):
            for ev in diagnosis.evidence_chain:
                st.write(f"• {ev}")

    # Span cards
    st.divider()
    st.subheader("📍 Pipeline Spans")
    for span in selected_trace.spans:
        render_span_card(span, diagnosis.step_scores, doc_text)

    # Diff view
    st.divider()
    render_diff_view(selected_trace, doc_text, labels)


if __name__ == "__main__":
    main()
