#!/usr/bin/env bash
# pointer-sha-ancestor.sh: the phase pointer's stamped sha must be real and reachable.
#
# V-184. docs/VARIANT-RUNS-POINTER.md carries a "Last synced ... through commit
# <sha>" marker so a future session can tell how stale the duplicated live-ID
# index is. Twice this phase that marker was wrong in a way no test could see:
# once typed from recall instead of copied from `git rev-parse` (V-136), and
# once left naming a commit that an `git commit --amend` had orphaned — it
# resolved locally, and no clone could ever find it.
#
# Both are the same wrong case, so this makes the wrong case unwritable rather
# than adding a third instruction to remember. Fires as a PreToolUse hook on
# `git push`, because that is the moment a local-only sha becomes a lie other
# people can read.
#
# Checked against HEAD, not against a parsed refspec. Every push in this repo is
# of the current branch, and HEAD is the honest approximation; a hook that tried
# to parse `git push <remote> <src>:<dst>` would be guessing.
#
# Silent (exit 0, no stdout) when: the pointer file is gone (the phase ended),
# or it carries no marker line at all (someone restructured it deliberately).
# Escalates only on a sha that is present but wrong.
set -uo pipefail

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo" || exit 0

pointer="docs/VARIANT-RUNS-POINTER.md"
[ -f "$pointer" ] || exit 0

# The marker line names the sha in backticks. Take the first match only: the
# file discusses the marker rule in prose further down, and prose must not vote.
marker=$(grep -m1 -iE 'last synced' "$pointer" || true)
[ -z "$marker" ] && exit 0

sha=$(grep -oE '`[0-9a-f]{7,40}`' <<<"$marker" | head -1 | tr -d '`')
[ -z "$sha" ] && exit 0

if ! git rev-parse --verify --quiet "${sha}^{commit}" >/dev/null; then
  reason="$pointer stamps \`$sha\` as its last-synced commit, and that commit does
not exist in this repository.

This is the amended-commit failure: a sha copied before \`git commit --amend\`
rewrote it. It resolved at the time and is an orphan now, so no clone can see
what the marker points at.

Fix: re-stamp the marker from \`git rev-parse\` against a commit that is
actually reachable, then push."
else
  if git merge-base --is-ancestor "$sha" HEAD; then
    exit 0
  fi
  reason="$pointer stamps \`$sha\` as its last-synced commit, but that commit is not
an ancestor of HEAD ($(git rev-parse --short HEAD)).

The marker exists so a future session can trust how current the duplicated
live-ID index is. A sha off on another branch, or dropped by a rebase, makes it
point at a state this branch never contained.

Fix: re-stamp the marker from \`git rev-parse\` against a commit on this
branch's history, then push."
fi

jq -nc --arg r "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $r
  }
}'
exit 0
