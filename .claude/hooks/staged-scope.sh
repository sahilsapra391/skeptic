#!/usr/bin/env bash
# staged-scope.sh: what you are committing is what you think you are committing.
#
# V-233. An uncommitted Dockerfile edit from an abandoned branch rode `git add -A`
# into a commit whose subject was "store the reconciliation at creation", merged
# inside an unrelated PR, and reached production unreviewed. Nobody was careless
# about the Dockerfile; the commit message was written from INTENT while the commit
# was built from the TREE, and nothing compared the two.
#
# So this compares them. It fires as a PreToolUse hook on `git commit` and blocks
# in two cases:
#
#   1. A staged path lies outside the directories this branch has previously
#      touched. On a branch about the honesty layer, a staged Dockerfile is a
#      surprise worth one keystroke.
#   2. A DEPLOY-PATH file is staged and its path does not appear in the commit
#      message. Those are the files whose silent change is most expensive:
#      Dockerfile, railway.json, CI workflows, launch.json, the hooks themselves.
#
# It always PRINTS the staged list, because the cheapest version of this defect is
# caught by looking.
#
# Override: SKEPTIC_ALLOW_WIDE_COMMIT=1, named in the block message. One env var,
# explicit, and it appears in the shell history of whoever used it.
set -uo pipefail

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo" || exit 0

staged=$(git diff --cached --name-only 2>/dev/null) || exit 0
[ -z "$staged" ] && exit 0
[ -n "${SKEPTIC_ALLOW_WIDE_COMMIT:-}" ] && exit 0

# the commit message, from -F/-m if the tool passed one, else the prepared file
msg=""
for f in "$repo/.git/COMMIT_EDITMSG" "$repo/.git/MERGE_MSG"; do
  [ -f "$f" ] && msg="$msg $(cat "$f" 2>/dev/null)"
done

deploy_surface=$(grep -E \
  -e '^backend/(Dockerfile|railway\.json|\.dockerignore)$' \
  -e '^\.github/workflows/' \
  -e '^\.claude/(launch\.json|settings\.json)$' \
  -e '^\.claude/hooks/' \
  <<<"$staged" || true)

undocumented=""
if [ -n "$deploy_surface" ] && [ -n "$msg" ]; then
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    base=$(basename "$path")
    grep -qF "$base" <<<"$msg" || undocumented="$undocumented$path"$'\n'
  done <<<"$deploy_surface"
fi

# directories this branch has touched before now (its own commits vs main)
base_ref=$(git merge-base HEAD main 2>/dev/null || echo "")
known=""
if [ -n "$base_ref" ]; then
  known=$(git diff --name-only "$base_ref"..HEAD 2>/dev/null | xargs -n1 dirname 2>/dev/null | sort -u)
fi

surprises=""
if [ -n "$known" ]; then
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    dir=$(dirname "$path")
    grep -qxF "$dir" <<<"$known" || surprises="$surprises$path"$'\n'
  done <<<"$staged"
fi

[ -z "$undocumented" ] && [ -z "$surprises" ] && exit 0

reason="Staged files this commit does not obviously account for.

STAGED ($(grep -c . <<<"$staged") files):
$(sed 's/^/  /' <<<"$staged" | head -20)"

if [ -n "$undocumented" ]; then
  reason="$reason

DEPLOY-PATH FILES NOT MENTIONED IN THE MESSAGE:
$(sed 's/^/  /' <<<"$undocumented")
These change how the app is built, deployed, or guarded. A commit that alters one
without saying so is how an unreviewed Dockerfile edit reached production inside a
commit about provenance (V-232). Name the file in the message, or unstage it."
fi

if [ -n "$surprises" ]; then
  reason="$reason

OUTSIDE THIS BRANCH'S PREVIOUS SCOPE:
$(sed 's/^/  /' <<<"$surprises" | head -12)
This branch has not touched those directories before. That is often fine on a
first commit; it is also exactly what leftover working-tree state from another
branch looks like. Check \`git status\` before proceeding."
fi

reason="$reason

Approve if the staged list is what you meant. To skip this check deliberately:
SKEPTIC_ALLOW_WIDE_COMMIT=1 git commit ..."

jq -nc --arg r "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $r
  }
}'
exit 0
