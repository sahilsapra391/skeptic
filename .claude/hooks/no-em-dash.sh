#!/usr/bin/env bash
# no-em-dash.sh: the house punctuation rule, enforced on what is about to be committed.
#
# Owner directive: the em-dash is not house punctuation and must never reach a
# Skeptic surface. CLAUDE.md says so, which makes it a good intention; a sweep
# cleaned the tree once, which fixes a moment. This is the part that survives
# people forgetting.
#
# Fires as a PreToolUse hook on `git commit`, and reads the STAGED content of
# the staged files (`git grep --cached`), not the working tree. That is the
# text the commit will actually carry.
#
# THIS IS THE FAST SIGNAL, NOT THE GATE. The gate is
# backend/tests/test_no_em_dash.py, which CI runs on every push and pull
# request: CI cannot be clicked through and it covers contributors who never
# load .claude/. This hook exists to say the same thing seconds after the
# mistake instead of minutes later, and it deliberately re-states the pattern
# list rather than shelling into pytest, because a hook that boots the backend
# test suite is not a fast signal and would get disabled for being slow.
#
# WHAT COUNTS, AND WHY IT IS NOT ONE PATTERN. The literal character, the HTML
# entities (named, decimal, hex), the CSS escape, and the percent-encoded UTF-8
# bytes are content everywhere they appear, so they are checked in every staged
# file. The backslash-u escapes are checked in every staged file too, minus a
# four-path allowlist, and the exemption is a PATH rather than a file type on
# purpose. An escape decodes to the glyph for every consumer of the file it
# sits in: a JSON parser turns it back into a dash (the form the sweep found
# hiding in a fixture), and so does every Python and TypeScript string literal,
# which is how one reaches a browser through source a character-level grep
# calls clean. "Escapes are fine in code" would hand that to several hundred
# .ts and .py files. The only files whose job is to name U+2014 are the two
# normalizer implementations and the two tests that drive them, and they are
# listed below. This list must stay identical to
# ESCAPE_MAY_NAME_THE_CHARACTER in backend/tests/test_no_em_dash.py; a hook
# looser than the gate teaches people a rule CI then rejects.
#
# EXCLUSION, one, narrow: the design-tool exports under docs/design (the
# vendored `_ds` bundle and the generated `support.js` runtimes). No frontend
# source imports them and no code path renders their text. It keys on the PATH,
# never on the GENERATED banner those files carry, because a banner is prose and
# prose gets reworded, which is precisely how the pointer-sha guard next door
# died silently for a day (V-221).
#
# V-58 POSTURE. If this cannot do its job it says so. A missing `jq`, a git grep
# that errors: each escalates to a prompt instead of exiting 0. A guard that
# vanishes quietly is not a guard, and the whole point of this one is that it
# holds when nobody is watching.
#
# Exit 0 with no stdout = allow. Anything else routes through the JSON below.
set -uo pipefail

repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo" || exit 0

# The JSON envelope every hook in this directory speaks. Kept in one place so
# the "I cannot run" path answers in the same shape as the "I found one" path.
escalate() {
  if command -v jq >/dev/null 2>&1; then
    jq -nc --arg r "$1" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: $r
      }
    }'
  else
    # No jq means no safe escaping for an arbitrary reason string, so this
    # reports the missing dependency with a fixed one rather than shrugging.
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"The no-em-dash commit guard could not run: jq is not on PATH. Every hook in .claude/hooks needs it. Install jq, or approve if you have checked the staged text yourself. CI still enforces this (backend/tests/test_no_em_dash.py)."}}'
  fi
  exit 0
}

command -v jq >/dev/null 2>&1 || escalate "jq missing"

# Pattern pieces, assembled from bytes so this file contains no spelling it
# rejects. Writing them out would make the guard flag its own source.
bs=$(printf '\134')                 # backslash
amp=$(printf '\046')                # ampersand
pct=$(printf '%%%s' E2 80 94)       # the UTF-8 bytes, percent-encoded
em=$(printf '\342\200\224')         # the character itself

rendered="${em}"
rendered="${rendered}|${amp}mdash;?"
rendered="${rendered}|${amp}#0*8212;?"
rendered="${rendered}|${amp}#[xX]0*2014;?"
rendered="${rendered}|${bs}${bs}2014([^0-9a-fA-F]|$)"
rendered="${rendered}|${pct}"

escapes="${bs}${bs}[uU]0*2014"
escapes="${escapes}|${bs}${bs}[uU]${bs}{0*2014${bs}}"

# Staged paths. Deletions are excluded: there is nothing left in them to read.
# `escapable` collects everything EXCEPT the four normalizer paths, which are
# the only files allowed to spell the escape.
paths=()
escapable=()
while IFS= read -r -d '' path; do
  case "$path" in
    docs/design/*/_ds/*|docs/design/_ds/*) continue ;;            # vendored design system
    docs/design/*/support.js|docs/design/support.js) continue ;;  # generated runtime
  esac
  paths+=(":(literal)$path")
  case "$path" in
    backend/app/text.py|frontend/lib/punctuation.ts) ;;                     # the two normalizers
    backend/tests/test_em_dash_runtime.py|backend/tests/test_normalizer_parity.py) ;;  # and their tests
    *) escapable+=(":(literal)$path") ;;
  esac
done < <(git diff --cached --name-only -z --diff-filter=ACMR 2>/dev/null)

[ ${#paths[@]} -eq 0 ] && exit 0

# -I skips binary blobs; a PNG cannot put punctuation on a page as text.
hits=$(git grep --cached -n -I -E -i -e "$rendered" -- "${paths[@]}" 2>&1)
status=$?
if [ "$status" -gt 1 ]; then
  escalate "The no-em-dash commit guard could not search the staged content.

git grep exited $status:
$hits

This check is not skipping on an error it cannot explain, because a guard that
goes quiet when it breaks is indistinguishable from a clean tree. Approve only
if you have read the staged text yourself. CI enforces this either way
(backend/tests/test_no_em_dash.py)."
fi

if [ ${#escapable[@]} -gt 0 ]; then
  more=$(git grep --cached -n -I -E -i -e "$escapes" -- "${escapable[@]}" 2>&1)
  status=$?
  if [ "$status" -gt 1 ]; then
    escalate "The no-em-dash commit guard could not search the staged files for escapes.

git grep exited $status:
$more"
  fi
  [ -n "$more" ] && hits=$(printf '%s\n%s' "$hits" "$more")
fi

hits=$(grep . <<<"$hits" || true)   # drop the blank line an empty result leaves
[ -z "$hits" ] && exit 0

count=$(grep -c . <<<"$hits")
list=$(head -12 <<<"$hits" | cut -c1-160 | sed 's/^/  /')
[ "$count" -gt 12 ] && list="$list
  ... and $((count - 12)) more"

escalate "Staged content contains an em-dash ($count line(s)).

$list

House rule, non-negotiable: replace it with a comma, a period, or parentheses,
whichever the sentence actually wants. Not an en-dash, not a horizontal bar, not
a double hyphen, and not an HTML dash entity, which are the same defect wearing a
different glyph. Code that has to handle the character builds it from its code
point (chr(0x2014) in Python, String.fromCharCode(0x2014) in TypeScript). Only
the two normalizers and their two direct tests may spell the backslash-u escape.

The lines above are matched in the STAGED blob, so fix the file and re-stage it;
editing the working tree alone will not clear this.

CI runs the same check as a hard gate (backend/tests/test_no_em_dash.py), so
approving here only delays the failure."
