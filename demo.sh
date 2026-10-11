#!/bin/bash
# Failure Forensics Tool -- Demo Script
# Run this script to demonstrate the complete failure forensics workflow.

set -e

echo "=========================================="
echo "Failure Forensics Tool -- Live Demo"
echo "=========================================="
echo ""

# --- Setup ------------------------------------------------------------------─
echo ">> Step 1: Validate configuration"
python -m forensics.cli validate-config
echo ""

echo ">> Step 2: Validate dataset"
python -m forensics.cli validate-dataset
echo ""

# --- Run Demo ---------------------------------------------------------------─
echo ""
echo ">> Step 3: Run demo on all 15 documents"
echo "    (This runs the 4-step pipeline on each document with a fake LLM)"
python -m forensics.cli demo
echo ""

# --- Analytics ---------------------------------------------------------------
echo ""
echo ">> Step 4: View analytics dashboard"
python -m forensics.cli analytics
echo ""

# --- Trace Explorer (Streamlit) ---------------------------------------------─
echo ""
echo ">> Step 5: Launch Trace Explorer (Streamlit UI)"
echo "    Run this in a separate terminal:"
echo "    streamlit run streamlit_app.py"
echo ""
echo "    The UI provides:"
echo "  • Trace list with filtering (status, failure category)"
echo "  • Interactive pipeline graph (color-coded nodes)"
echo "  • Span detail panel (input/output/prompt/raw_response/tokens/checks/evidence)"
echo "  • Diff view (expected vs actual vs grounded truth)"
echo "  • Flagging interface with confirm/override"
echo ""

# --- Flag & Diagnose ------------------------------------------------------──
echo ">> Step 6: Flag a trace and diagnose via API"
echo "    Start API server in a separate terminal:"
echo "    uvicorn forensics.api:app --reload"
echo ""
echo "    Then flag a trace:"
echo "    curl -X POST http://localhost:8000/traces/<TRACE_ID>/flag \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"user_id\": \"demo_user\", \"note\": \"Classification wrong\"}'"
echo ""
echo "    Diagnose the trace:"
echo "    curl -X POST http://localhost:8000/traces/<TRACE_ID>/diagnose"
echo ""
echo "    Confirm diagnosis:"
echo "    curl -X POST http://localhost:8000/diagnoses/<DIAGNOSIS_ID>/confirm \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"confirmed\": true}'"
echo ""

# --- Eval Loop ---------------------------------------------------------------
echo ">> Step 7: Feedback-to-Eval Loop"
echo "    Create eval cases from confirmed flags:"
echo "    python -m forensics.cli eval-cases"
echo ""
echo "    Run eval suite against current pipeline:"
echo "    python -m forensics.cli eval-run"
echo ""
echo "    View eval results:"
echo "    python -m forensics.cli eval"
echo ""

# --- Analytics ---------------------------------------------------------------
echo ">> Step 8: Analytics via API"
echo "    curl http://localhost:8000/analytics"
echo ""

# --- Trace Explorer UI ------------------------------------------------------─
echo ">> Step 9: Full Trace Explorer (Streamlit)"
echo "    streamlit run streamlit_app.py"
echo ""
echo "    Features:"
echo "  1. Trace list with status/category filters"
echo "  2. Pipeline graph (Plotly + NetworkX, color-coded)"
echo "  3. Span detail: input/output/prompt/raw_response/tokens/checks/evidence"
echo "  4. Diff view: expected vs actual vs grounded truth"
echo "  5. Flag button → diagnosis → confirm/override"
echo ""

# --- Cleanup ---------------------------------------------------------------──
echo ">> Cleanup (optional)"
echo "    rm -rf data/traces data/forensics.db*"
echo ""

echo "=========================================="
echo "Demo Complete!"
echo "=========================================="
