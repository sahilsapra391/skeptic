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
# V-221 POST-MORTEM. The first version of this hook found its sha by grepping
# the pointer's PROSE for "last synced". It was dead within a day: the commit
# that closed PR-A2 reworded that line to "Index synced through PR #146", the
# grep stopped matching, the hook exited silently, and one push went out
# unverified. Nothing broke, because that push's sha happened to be a valid
# ancestor, but the guard was gone and nobody could tell.
#
# Two changes came out of that. It keys on a MACHINE-READABLE ANCHOR, an HTML
# comment nobody has a reason to reword, so editing the surrounding prose cannot
# kill it. And a missing anchor is now LOUD: if the pointer file exists and the
# anchor does not, this escalates rather than shrugging. That is the V-58
# posture, applied to a guard instead of a test — fail, never skip — and it is
# the specific lesson from a guard that died silently once already.
#
# Silent (exit 0, no stdout) ONLY when the pointer file is gone, which means the
# phase ended. Every other state is reported.
set -uo pipefail

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo" || exit 0

pointer="docs/VARIANT-RUNS-POINTER.md"
[ -f "$pointer" ] || exit 0

# The anchor, not the prose. Exact form:  <!-- last-synced-sha: abc1234 -->
anchor=$(grep -m1 -oE '<!--[[:space:]]*last-synced-sha:[[:space:]]*[0-9a-f]{7,40}[[:space:]]*-->' "$pointer" || true)
sha=$(grep -oE '[0-9a-f]{7,40}' <<<"$anchor" | head -1)

if [ -z "$sha" ]; then
  reason="$pointer exists but carries no last-synced-sha anchor.

Expected a line containing exactly:

    <!-- last-synced-sha: <sha> -->

This check used to look for the words \"last synced\" in the prose, and a commit
that reworded that sentence silently disabled it for a push (V-221). The anchor
replaced the prose so an edit cannot kill the guard, and a MISSING anchor is now
reported instead of ignored, because a guard that can vanish quietly is not a
guard.

Fix: add the anchor beside the marker with a sha from \`git rev-parse\`, or delete
$pointer if the phase is genuinely over."
  jq -nc --arg r "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "ask",
      permissionDecisionReason: $r
    }
  }'
  exit 0
fi

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
