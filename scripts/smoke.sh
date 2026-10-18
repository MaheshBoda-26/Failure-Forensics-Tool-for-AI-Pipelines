#!/usr/bin/env bash
#
# End-to-end smoke test: the loop a demo actually has to survive.
#
#   health -> ui -> seed a run -> flag a trace -> diagnose -> confirm -> analytics
#
# Usage:
#   scripts/smoke.sh [BASE_URL]
#
#   BASE_URL   defaults to $BASE_URL, then http://127.0.0.1:8000
#   API_KEY    set to a `forensics auth create-key` value when AUTH_ENABLED=true
#
# Exits non-zero on the first failure, so it is usable as a deploy gate. Uses
# curl and python3 only (both guaranteed on a machine that runs this service).

set -euo pipefail

BASE_URL="${1:-${BASE_URL:-http://127.0.0.1:8000}}"
API_KEY="${API_KEY:-}"

CURL=(curl --silent --show-error --fail-with-body --max-time 600)
if [[ -n "$API_KEY" ]]; then
  CURL+=(-H "Authorization: Bearer ${API_KEY}")
fi

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Read one field out of a JSON document on stdin; python3 stands in for jq,
# which is not guaranteed to be installed.
field() {
  python3 -c '
import json, sys, ast
v = json.load(sys.stdin)
expr = sys.argv[1]

def safe_eval(node, target):
    if isinstance(node, ast.Expression):
        return safe_eval(node.body, target)
    if isinstance(node, ast.Subscript):
        value = safe_eval(node.value, target)
        slice_node = node.slice
        if isinstance(slice_node, ast.Index):  # Python < 3.9 compatibility
            slice_node = slice_node.value
        if isinstance(slice_node, ast.Constant):
            key = slice_node.value
        else:
            raise ValueError("Unsupported subscript")
        return value[key] if not isinstance(key, int) or isinstance(value, list) else value[int(key)]
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError("Unsupported expression")

node = ast.parse(expr, mode="eval")
print(safe_eval(node, v))
' "$1"
}

printf '\033[1mFailure Forensics smoke test\033[0m against %s\n' "$BASE_URL"

# ── health ──────────────────────────────────────────────────────────────────
step "health"
STATUS=$("${CURL[@]}" "${BASE_URL}/health" | field "v['status']")
[[ "$STATUS" == "ok" ]] || die "/health returned status=${STATUS}"
ok "service is up"

# ── bundled UI ──────────────────────────────────────────────────────────────
step "ui"
# The failure this catches: a deploy builds the SPA but cannot serve it, so
# `/health` stays green and every API route below passes while `/` answers 404.
UI=$("${CURL[@]}" "${BASE_URL}/") || die "GET / failed — the compiled UI is not being served"
printf '%s' "$UI" | grep -qi '<div id="root"' \
  || die "GET / did not return the SPA shell (check STATIC_DIR/frontend/dist)"
ok "compiled UI served at /"

# ── seed data if the instance is empty ──────────────────────────────────────
step "traces"
TRACE_COUNT=$("${CURL[@]}" "${BASE_URL}/traces" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
ok "${TRACE_COUNT} trace(s) already stored"
if [[ "$TRACE_COUNT" == "0" ]]; then
  printf '  seeding the dataset via POST /pipeline/run-demo (this runs 15 documents)...\n'
  SAVED=$("${CURL[@]}" -X POST "${BASE_URL}/pipeline/run-demo" | field "v['traces_saved']")
  ok "seeded ${SAVED} trace(s)"
  [[ "$SAVED" -gt 0 ]] || die "run-demo saved nothing"
fi

TRACE_ID=$("${CURL[@]}" "${BASE_URL}/traces" | field "v[0]['trace_id']")
ok "using trace ${TRACE_ID}"

# ── flag ────────────────────────────────────────────────────────────────────
step "flag"
FLAG_ID=$("${CURL[@]}" -X POST "${BASE_URL}/traces/${TRACE_ID}/flag" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"smoke","note":"smoke test"}' | field "v['flag_id']")
ok "flagged as ${FLAG_ID}"

# ── diagnose ────────────────────────────────────────────────────────────────
step "diagnose"
DIAGNOSIS=$("${CURL[@]}" -X POST "${BASE_URL}/traces/${TRACE_ID}/diagnose")
DIAGNOSIS_ID=$(printf '%s' "$DIAGNOSIS" | field "v['diagnosis_id']")
ROOT_CAUSE=$(printf '%s' "$DIAGNOSIS" | field "v['root_cause_step']")
CONFIDENCE=$(printf '%s' "$DIAGNOSIS" | field "v['confidence']")
ok "diagnosis ${DIAGNOSIS_ID} blames '${ROOT_CAUSE}' (confidence ${CONFIDENCE})"

# ── confirm (creates an eval case) ──────────────────────────────────────────
step "confirm"
CONFIRMED=$("${CURL[@]}" -X POST "${BASE_URL}/diagnoses/${DIAGNOSIS_ID}/confirm" \
  -H 'Content-Type: application/json' \
  -d '{"confirmed":true}' | field "v['confirmed']")
[[ "$CONFIRMED" == "True" ]] || die "confirm returned confirmed=${CONFIRMED}"
ok "diagnosis confirmed"

CASE_COUNT=$("${CURL[@]}" "${BASE_URL}/eval/cases" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[[ "$CASE_COUNT" -gt 0 ]] || die "confirming produced no eval case"
ok "${CASE_COUNT} eval case(s) in the suite"

# ── analytics (the headline number) ─────────────────────────────────────────
step "analytics"
ANALYTICS=$("${CURL[@]}" "${BASE_URL}/analytics")
TOTAL=$(printf '%s' "$ANALYTICS" | field "v['total_traces']")
FAILURE_RATE=$(printf '%s' "$ANALYTICS" | field "v['failure_rate']")
ok "total_traces=${TOTAL} failure_rate=${FAILURE_RATE}"
[[ "$TOTAL" -gt 0 ]] || die "/analytics reports no traces"
python3 -c 'import sys; rate=float("'$FAILURE_RATE'"); sys.exit(0 if rate > 0 else 1)' \
  || die "/analytics failure_rate is 0 — the demo's headline number is not populated"
ok "failure rate is non-zero"

# ── operational surfaces ────────────────────────────────────────────────────
step "operations"
"${CURL[@]}" "${BASE_URL}/scheduler/status" >/dev/null && ok "/scheduler/status reachable"
"${CURL[@]}" "${BASE_URL}/jobs" >/dev/null && ok "/jobs reachable"

printf '\n\033[1;32mAll smoke checks passed.\033[0m\n'
