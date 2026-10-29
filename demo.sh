#!/bin/bash
# Failure Forensics Tool -- Demo Script
# Run this script to demonstrate the complete failure forensics workflow.

set -e

# Use the project virtualenv when it exists: a bare `python` picks the system
# interpreter, which does not have the `forensics` package installed after
# `make install`.
PY="python"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

# Default to offline deterministic fixture mode when DRY_RUN is not set.
export DRY_RUN="${DRY_RUN:-true}"

echo "=========================================="
echo "Failure Forensics Tool -- Live Demo"
echo "=========================================="
echo ""

# --- Setup -------------------------------------------------------------------
echo ">> Step 1: Validate configuration"
$PY -m forensics.cli validate-config
echo ""

echo ">> Step 2: Validate dataset"
$PY -m forensics.cli validate-dataset
echo ""

# --- Seed & Run Demo ---------------------------------------------------------
echo ">> Step 3: Seed committed live-model traces"
$PY -m forensics.cli seed
echo ""

echo ">> Step 4: Run demo on all 15 dataset documents"
$PY -m forensics.cli demo
echo ""

# --- Benchmark & Analytics ---------------------------------------------------
echo ">> Step 5: Run Fault-Injection Benchmark Scorecard"
$PY -m forensics.cli benchmark-analyzer
echo ""

echo ">> Step 6: View analytics dashboard summary"
$PY -m forensics.cli analytics
echo ""

# --- Web UI & API ------------------------------------------------------------
echo ">> Step 7: Launch React Trace Explorer + FastAPI Server"
echo "    Run in a terminal:"
echo "    make run-web"
echo "    Then open http://127.0.0.1:8000 in your browser:"
echo "  • Overview: KPIs, latest failure causal spine, stage breakdown, guarantees"
echo "  • Traces: Search/filter traces, per-stage checks, Confirm/Override diagnosis"
echo "  • Test document: Click 'Test document' to run custom text or upload a .txt file"
echo "  • Execution graph: Stage DAG + Ground-truth vs Observed Output comparison"
echo "  • Analytics: Failure correlation matrix, Fault Injection Scorecard, Eval suite, Spend"
echo ""

echo "=========================================="
echo "Demo Complete!"
echo "=========================================="
