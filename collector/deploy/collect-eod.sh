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
# Step order and failure semantics mirror the workflow this replaces exactly:
#   - collect.py pings Healthchecks itself (success, or <url>/fail on error).
#     NEW here: because collect.py pings success as step 1 of 11, the chain
#     ALSO flips the tile to /fail if any LATER step fails (see the exit
#     block), naming the failed steps. On Actions a failed derivation ended
#     the run red and GitHub emailed; nothing observes a failed oneshot, so
#     the tile has to observe the whole chain now, not just collect.py.
#     Abnormal death (the 45-min wall, OOM, reboot) can't reach that block at
#     all — skeptic-collect-eod.service carries an ExecStopPost= that pings
#     /fail on any non-success $SERVICE_RESULT, which is the only hook that
#     survives a SIGKILL.
#   - alpaca.py ran under a custom `if:` in the workflow, and GitHub implicitly
#     ANDs a custom `if:` with success() — so it was skipped when the collector
#     failed. Same here.
#   - every derivation ran under `if: always()`: one failure never skipped the
#     rest, but the job still ended red. Same here, via $rc.
#
# One thing here does NOT mirror the old workflow: the chain takes a
# cross-host lease in R2 before step 1 (see "the cross-host lease" below).
# `concurrency: group: collector` used to keep the dispatchable workflows off
# this job's back, and it stopped covering anything the moment the schedule
# moved to this host.
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

ping_fail() {   # ping_fail <body>   — flip the Healthchecks tile red
    local body=$1
    local url
    # Read the URL through the SAME parser every other consumer uses (uv's
    # --env-file, then collect.py's own rstrip("/")). A hand-rolled grep|cut
    # kept surrounding quotes, `export ` prefixes and CR line endings, so a
    # .env that pings fine from collect.py could silently fail to page here.
    url=$("$UV" run --env-file .env python -c \
        'import os; print(os.environ.get("HEALTHCHECK_URL", "").rstrip("/"))' 2>/dev/null)
    if [ -z "${url}" ]; then
        echo "!! nothing paged (HEALTHCHECK_URL unset): ${body}"
        return 0
    fi
    curl -fsS -m 10 --retry 3 --data-raw "${body}" "${url}/fail" >/dev/null 2>&1 \
        && echo "== pinged Healthchecks /fail (${body}) ==" \
        || echo "!! /fail ping itself failed — journal is the only record"
}

echo "===== $(date -u +%FT%TZ) collect-eod start ====="

# ---------------------------- the cross-host lease -------------------------
# `concurrency: group: collector` in the workflows only serializes Actions
# runs against EACH OTHER. Once the schedule moved here, a manual dispatch of
# collect-eod.yml or alpaca-backfill.yml could run at the same time as this
# chain — same Alpaca account (one shared 200 req/min budget, which alpaca.py
# paces against assuming it is alone), same R2 lake. Nothing corrupts; both
# sides just crawl, and this one has a 45-min wall to crawl into. lock.py is
# the mutex both hosts honour.
#
# It is NOT a `step`: the lease is infrastructure, so it stays out of the
# chain's workflow-parity guard (which reads what `step` runs).
LOCK_TTL="${SKEPTIC_LOCK_TTL:-3000}"   # the unit's 2700s wall + 5 min margin
LOCK_TOKEN_FILE=$(mktemp "${TMPDIR:-/tmp}/skeptic-collector-lock.XXXXXX") || {
    # Pages on the way out: hc-fail.sh skips SERVICE_RESULT=exit-code on the
    # assumption the script already reported for itself, so exiting here
    # without a ping would be a SILENT stop — the one shape this chain must
    # never have. (mktemp failing means a full disk, which on a 1 GB box with
    # an append-only log is the realistic version of this.)
    echo "!! mktemp for the lease token failed — not starting the chain"
    ping_fail "collect-eod did not start: could not create the lease token file (disk full?)"
    exit 1
}
lease_held=0

release_lease() {
    [ "$lease_held" -eq 1 ] || { rm -f "$LOCK_TOKEN_FILE"; return 0; }
    lease_held=0
    "$UV" run --env-file .env python lock.py release --token-file "$LOCK_TOKEN_FILE" \
        || echo "!! releasing the lease failed — the lane stays locked for up to ${LOCK_TTL}s"
    rm -f "$LOCK_TOKEN_FILE"
}
trap release_lease EXIT
# The EXIT trap above is what actually hands the lease back on the signal
# path too: bash runs exit traps before re-raising a fatal signal (verified —
# an untrapped SIGTERM still fires them). This one is for the JOURNAL. Without
# it the 45-min wall kills the chain with no line saying so, and "collect-eod
# start" with no matching "done" is a worse thing to read at 3 AM than one
# that says it was signalled. It also pins the status at 143 rather than
# leaving it to the re-raise.
trap 'echo "!! collect-eod terminated by a signal"; exit 143' TERM INT

acq=0
# Captured, not streamed, so the holder's identity can ride the ping body —
# a tile that says only "leased elsewhere" costs an SSH at 3 AM. Echoed back
# immediately so the journal still has everything.
acq_out=$("$UV" run --env-file .env python lock.py acquire \
    --holder "vm-collect-eod" --ttl "$LOCK_TTL" \
    --token-file "$LOCK_TOKEN_FILE" 2>&1) || acq=$?
echo "${acq_out}"
if [ "$acq" -ne 0 ]; then
    # Loud on purpose. A night the chain never ran has to look exactly like a
    # night it ran and failed — the Jul 27-31 outage was invisible precisely
    # because a job that never starts reports nothing.
    #
    # 75 (EX_TEMPFAIL) is a refusal: someone holds the lane, and the fix is to
    # wait or release it. Anything else is the lock ITSELF failing (R2 down,
    # bad credentials), which is a different page and a different fix — the
    # tile body is all the owner gets at 21:30, so it has to say which.
    if [ "$acq" -eq 75 ]; then
        holder=$(printf '%s\n' "${acq_out}" | grep -m1 "REFUSING" || true)
        reason="the collector lane is leased elsewhere — ${holder:-holder unknown, see the journal}"
    else
        reason="the lease could not be read or written (rc=${acq})"
    fi
    echo "!! not starting the chain: ${reason}"
    ping_fail "collect-eod did not start: ${reason}"
    exit "$acq"
fi
lease_held=1

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
    ping_fail "collect-eod chain failed: ${failed_steps}"
fi

echo "===== $(date -u +%FT%TZ) collect-eod done (rc=${rc}) ====="
exit "$rc"
