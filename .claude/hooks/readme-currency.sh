#!/usr/bin/env bash
# readme-currency.sh: keep README.md honest.
#
# Owner directive (2026-08-05): every major change to the application must be
# reflected in the README, guaranteed, not remembered. An instruction in
# CLAUDE.md is a good intention; this hook is the enforcement.
#
# Fires as a PreToolUse hook on `git commit`. If the staged change touches
# application surface (engine, honesty layer, parser, API, frontend, collector,
# deploy units, workflows, or the spec docs the README describes) and README.md
# is NOT staged alongside it, the commit is escalated to a user prompt with the
# offending paths named.
#
# Deliberately NOT triggered by: tests, fixtures, lockfiles, generated caches,
# or the README/LICENSE themselves. Those are not "major changes to the
# application" and blocking on them would train everyone to click through.
#
# Exit 0 with no stdout = allow. Anything else routes through the JSON below.
set -uo pipefail

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo" || exit 0

staged=$(git diff --cached --name-only 2>/dev/null) || exit 0
[ -z "$staged" ] && exit 0

# README already updated in this commit: nothing to enforce.
grep -qx 'README.md' <<<"$staged" && exit 0

significant=$(grep -E \
  -e '^backend/app/' \
  -e '^frontend/(app|components|lib)/' \
  -e '^collector/[^/]+\.py$' \
  -e '^collector/deploy/' \
  -e '^\.github/workflows/' \
  -e '^docs/(TECH-SPEC|DATA-PIPELINE|BUILD-PLAN)\.md$' \
  -e '^docs/strategy-spec\.schema\.json$' \
  <<<"$staged" | grep -vE '(^|/)(tests?|fixtures?|__pycache__)/' || true)

[ -z "$significant" ] && exit 0

count=$(grep -c . <<<"$significant")
list=$(head -8 <<<"$significant" | sed 's/^/  - /')
[ "$count" -gt 8 ] && list="$list
  ... and $((count - 8)) more"

reason="This commit changes application surface but does not update README.md.

$list

Standing owner rule: every major change to the application must be reflected in
the README in the same change. Update README.md now if this alters how the
application works, what it guarantees, its architecture, its data sources, or
how it is operated.

Approve to proceed if this is genuinely not a major change (a refactor with no
behavioural difference, a typo, a dependency bump)."

# jq builds the JSON so the reason is escaped correctly regardless of content.
jq -nc --arg r "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $r
  }
}'
exit 0
