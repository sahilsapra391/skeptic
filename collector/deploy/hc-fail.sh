#!/usr/bin/env bash
# hc-fail.sh — flip a Healthchecks tile red from a systemd ExecStopPost=.
#
# Why this exists as a unit hook and not just inline in collect-eod.sh: the
# chain's own /fail block cannot run when the unit is KILLED — the 45-min
# TimeoutStartSec wall, an OOM kill of bash on the 1 GB box, or a reboot all
# SIGTERM/SIGKILL the cgroup mid-chain. collect.py pings SUCCESS as step 1 of
# 11, so without this the tile would sit green while the derivations and the
# coverage ledger never ran — the exact silent shape the Jul 27-31 outage had,
# which is the whole reason the schedules moved off Actions.
#
# ExecStopPost= runs on every exit path including timeout and signal death,
# and systemd exports $SERVICE_RESULT / $EXIT_STATUS into it.
#
# Usage: ExecStopPost=/usr/bin/bash .../hc-fail.sh <label> [ENV_VAR_NAME]
#   ENV_VAR_NAME defaults to HEALTHCHECK_URL (the EOD tile). The improve and
#   quality lanes pass their OWN var so the EOD tile keeps meaning exactly
#   "tonight's lake is whole" — those vars are optional, and until the owner
#   creates the checks and adds the URLs, this logs instead of paging.
# Env: SERVICE_RESULT (from systemd), <ENV_VAR_NAME> (from collector/.env)
set -uo pipefail

label="${1:-job}"
url_var="${2:-HEALTHCHECK_URL}"
result="${SERVICE_RESULT:-unknown}"

# A clean exit needs nothing: a failing chain already pinged /fail itself with
# the failed step names, and a passing chain pinged success.
[ "$result" = "success" ] && exit 0

# exit-code deaths already self-reported from inside the script (it reaches its
# own /fail block before returning non-zero). Anything else — timeout,
# core-dump, signal, oom-kill, start-limit-hit — never got there.
if [ "$result" = "exit-code" ]; then
    exit 0
fi

cd "$(dirname "$0")/.." || exit 0   # /opt/skeptic/collector
UV="${UV:-/usr/local/bin/uv}"

# Same parser as everything else that reads this .env (see collect-eod.sh).
url=$(SK_HC_VAR="$url_var" "$UV" run --env-file .env python -c \
    'import os; print(os.environ.get(os.environ["SK_HC_VAR"], "").rstrip("/"))' 2>/dev/null)

if [ -z "$url" ]; then
    echo "!! ${label} died abnormally (${result}) but ${url_var} is unset — nothing paged"
    exit 0
fi

curl -fsS -m 10 --retry 3 \
    --data-raw "${label} died abnormally: SERVICE_RESULT=${result} EXIT_STATUS=${EXIT_STATUS:-?} (killed before it could report — check journalctl -u ${label})" \
    "${url}/fail" >/dev/null 2>&1 \
    && echo "== pinged Healthchecks /fail: ${label} ${result} ==" \
    || echo "!! ${label} died (${result}) and the /fail ping ALSO failed — journal is the only record"
exit 0
