#!/usr/bin/env bash
# collect-eod.sh — the nightly EOD collection chain, moved off GitHub Actions.
#
# Why it moved (2026-08-04, after the Jul 27–31 billing block): private-repo
# Actions minutes bill against the account, and a billing block refused to
# start the job at all for a full trading week. All ten scheduled runs died in
# <5s with "the job was not started", which means collect.py never ran — so it
# pinged NEITHER success NOR /fail. Only the Healthchecks tile going quiet
# caught it. An always-on VM removes the billing dependency from the one job
# that feeds the lake every night.
#
# Step order and failure semantics mirror the workflow this replaces exactly,
# so the Healthchecks tile keeps meaning what it always meant:
#   - collect.py pings Healthchecks itself (success, or <url>/fail on error).
#     It is the ONLY step the tile observes; derivations are out of its scope.
#   - alpaca.py ran under a custom `if:` in the workflow, and GitHub implicitly
#     ANDs a custom `if:` with success() — so it was skipped when the collector
#     failed. Same here.
#   - every derivation ran under `if: always()`: one failure never skipped the
#     rest, but the job still ended red. Same here, via $rc.
#
# NOT `set -e`: a failing derivation must not abort the chain behind it.
set -uo pipefail

cd "$(dirname "$0")/.."   # /opt/skeptic/collector
# Overridable so the chain's skip/continue semantics are testable against a
# stub. The unit never sets it, so production always takes the absolute path.
UV="${UV:-/usr/local/bin/uv}"
rc=0
failed_steps=""

step() {   # step <label> <script> [args...]
    local label=$1
    shift
    local s=0
    echo "== $(date -u +%FT%TZ) ${label} =="
    # Capture the status directly. Inside `if ! cmd; then`, $? reads as 0 (the
    # negation's own status), so the log would claim rc=0 for a real failure.
    "$UV" run --env-file .env python "$@" || s=$?
    if [ "$s" -ne 0 ]; then
        echo "!! ${label} FAILED (rc=${s})"
        rc=1
        failed_steps="${failed_steps:+${failed_steps}; }${label}"
    fi
}

echo "===== $(date -u +%FT%TZ) collect-eod start ====="

# 1. The collector proper. Pings Healthchecks on the way out, either way.
step "collector (collect.py --mode all)" collect.py --mode all

# 2. Minute-lake top-up (DATA-PIPELINE job 4). Gated on the collector, because
#    the workflow's custom `if:` carried an implicit success().
if [ "$rc" -eq 0 ]; then
    step "minute-lake top-up (alpaca.py --mode eod)" alpaca.py --mode eod
else
    echo "== skipping minute-lake top-up: collector failed =="
fi

# 3-11. The `if: always()` block. Each runs regardless of anything above it;
#       each failure is recorded but never blocks the next.
step "coverage report"                  coverage.py
step "CBOE close chain derivation"      derive_cboe_eod.py
step "in-house signal derivation"       derive_inhouse_signals.py
step "IVS signal derivation"            derive_ivs_signals.py
step "flow signal derivation"           derive_flow_signals.py
step "in-house flow family"             derive_flow_inhouse.py
step "cross-source validation"          derive_cross_validation.py
step "fill-model calibration"           derive_fill_calibration.py
step "coverage ledger"                  ledger.py

# On Actions, a failed derivation ended the run red and GitHub emailed about
# the scheduled-workflow failure. Here nothing observes a failed oneshot — and
# collect.py already pinged the tile GREEN before the derivations ran. So flip
# the tile ourselves: a /fail ping after the success ping wins (last signal
# counts), naming the failed steps in the body for the dashboard.
if [ "$rc" -ne 0 ]; then
    HC_URL=$(grep -E '^HEALTHCHECK_URL=' .env 2>/dev/null | head -1 | cut -d= -f2-)
    if [ -n "${HC_URL}" ]; then
        curl -fsS -m 10 --retry 3 --data-raw "collect-eod chain failed: ${failed_steps}" \
            "${HC_URL}/fail" >/dev/null 2>&1 \
            && echo "== pinged Healthchecks /fail (${failed_steps}) ==" \
            || echo "!! /fail ping itself failed — journal is the only record"
    else
        echo "!! chain failed but HEALTHCHECK_URL is unset — nothing paged"
    fi
fi

echo "===== $(date -u +%FT%TZ) collect-eod done (rc=${rc}) ====="
exit "$rc"
