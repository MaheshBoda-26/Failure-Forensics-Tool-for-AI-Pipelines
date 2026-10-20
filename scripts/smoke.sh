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
# which is not guaranteed to be installed. The path is a sequence of `['key']`
# and `[0]` segments and is *parsed*, never evaluated: this script may be run
# against an untrusted endpoint, and `eval()` on a value derived from that
# endpoint's response is arbitrary code execution on the caller's machine.
field() {
  python3 -c '
import json
import re
import sys

QUOTES = ("\x22", "\x27")
value = json.load(sys.stdin)
for raw in re.findall(r"\[(.*?)\]", sys.argv[1]):
    token = raw.strip()
    if len(token) >= 2 and token[0] in QUOTES and token[-1] == token[0]:
        value = value[token[1:-1]]
    else:
        value = value[int(token)]
print(value)
' "$1"
}

printf '\033[1mFailure Forensics smoke test\033[0m against %s\n' "$BASE_URL"

# ── health ──────────────────────────────────────────────────────────────────
step "health"
STATUS=$("${CURL[@]}" "${BASE_URL}/health" | field "['status']")
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
  SAVED=$("${CURL[@]}" -X POST "${BASE_URL}/pipeline/run-demo" | field "['traces_saved']")
  ok "seeded ${SAVED} trace(s)"
  [[ "$SAVED" -gt 0 ]] || die "run-demo saved nothing"
fi

TRACE_ID=$("${CURL[@]}" "${BASE_URL}/traces" | python3 -c '
import json
import sys

traces = json.load(sys.stdin)
# Prefer a trace the analyzer blamed a step for: confirming a clean diagnosis
# correctly creates no eval case, and the demo dataset always has failures.
failing = next(
    (t for t in traces if t.get("root_cause_step") not in (None, "", "none")),
    traces[0] if traces else None,
)
print(failing["trace_id"] if failing else "")
')
[[ -n "$TRACE_ID" ]] || die "no traces available"
ok "using trace ${TRACE_ID}"

# ── flag ────────────────────────────────────────────────────────────────────
step "flag"
FLAG_ID=$("${CURL[@]}" -X POST "${BASE_URL}/traces/${TRACE_ID}/flag" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"smoke","note":"smoke test"}' | field "['flag_id']")
ok "flagged as ${FLAG_ID}"

# ── diagnose ────────────────────────────────────────────────────────────────
step "diagnose"
DIAGNOSIS=$("${CURL[@]}" -X POST "${BASE_URL}/traces/${TRACE_ID}/diagnose")
DIAGNOSIS_ID=$(printf '%s' "$DIAGNOSIS" | field "['diagnosis_id']")
ROOT_CAUSE=$(printf '%s' "$DIAGNOSIS" | field "['root_cause_step']")
CONFIDENCE=$(printf '%s' "$DIAGNOSIS" | field "['confidence']")
ok "diagnosis ${DIAGNOSIS_ID} blames '${ROOT_CAUSE}' (confidence ${CONFIDENCE})"

# ── confirm (creates an eval case) ──────────────────────────────────────────
step "confirm"
CONFIRMED=$("${CURL[@]}" -X POST "${BASE_URL}/diagnoses/${DIAGNOSIS_ID}/confirm" \
  -H 'Content-Type: application/json' \
  -d '{"confirmed":true}' | field "['confirmed']")
[[ "$CONFIRMED" == "True" ]] || die "confirm returned confirmed=${CONFIRMED}"
ok "diagnosis confirmed"

CASE_COUNT=$("${CURL[@]}" "${BASE_URL}/eval/cases" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
if [[ "$ROOT_CAUSE" == "none" || -z "$ROOT_CAUSE" ]]; then
  # A clean diagnosis is a rejection of the analyzer's failure claim, so the new
  # confirm handler deliberately creates no eval case for it.
  ok "trace is clean; no eval case expected"
else
  [[ "$CASE_COUNT" -gt 0 ]] || die "confirming produced no eval case"
  ok "${CASE_COUNT} eval case(s) in the suite"
fi

# ── analytics (the headline number) ─────────────────────────────────────────
step "analytics"
ANALYTICS=$("${CURL[@]}" "${BASE_URL}/analytics")
TOTAL=$(printf '%s' "$ANALYTICS" | field "['total_traces']")
FAILURE_RATE=$(printf '%s' "$ANALYTICS" | field "['failure_rate']")
ok "total_traces=${TOTAL} failure_rate=${FAILURE_RATE}"
[[ "$TOTAL" -gt 0 ]] || die "/analytics reports no traces"
# The value comes from the endpoint's response, so it is passed as an argument
# (data), never interpolated into the Python source (code).
python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) > 0 else 1)' "$FAILURE_RATE" \
  || die "/analytics failure_rate is 0 — the demo's headline number is not populated"
ok "failure rate is non-zero"

# ── operational surfaces ────────────────────────────────────────────────────
step "operations"
"${CURL[@]}" "${BASE_URL}/scheduler/status" >/dev/null && ok "/scheduler/status reachable"
"${CURL[@]}" "${BASE_URL}/jobs" >/dev/null && ok "/jobs reachable"

printf '\n\033[1;32mAll smoke checks passed.\033[0m\n'
