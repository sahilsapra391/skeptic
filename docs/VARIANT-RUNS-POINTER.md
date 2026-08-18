# Variant Runs phase — where the brief lives

The authority for this phase is **`SKEPTIC-VARIANT-RUNS-BRIEF.md`**, kept
outside the repo at `Documents/Projects Misc/Skeptic/` per the Development
`CLAUDE.md` rule that product specs and briefs live there, not in repos.

This pointer exists because that rule has a sharp edge: a session working
inside this repo cannot read `Projects Misc/`, and this phase opened with the
brief being reconstructed from conversation before anyone realised a document
existed. If you are picking this work up, go read the brief first. Where the
brief and any code comment disagree, the brief wins.

## What the phase is

"Run a variant": one button on a finished run that reopens the confirmed spec
dials prefilled from that run, carries the original prompt and clarifying Q&A
forward without replaying the interview, and submits a new run for a new
credit. Lineage is recorded; the deflated Sharpe still does the arguing.

**The rule the whole phase exists to hold:** nothing on the variant path may
change a value the user did not change. When an ambiguity comes up that the
brief does not cover, resolve it that way.

## PR sequence

| PR | Scope | State |
| --- | --- | --- |
| PR-0 | Latent correctness fixes in the normal run path (V-17, V-36), the round-trip guard (V-18), the no-op proof (V-68), the read-only audit script (V-24) | merged in #143 |
| PR-A1 | Schema, endpoint, tier classifier, locks, costs/seed/window inheritance, zero-edit guard, provenance sections 1-3 + section 5 diff, entry points, lineage header and grouping | complete in #145 |
| PR-A2 | Per-exchange Q&A reconciliation: SUPERSEDED / NOT APPLICABLE states, the label table, the unmapped counter | not started |
| PR-B | The argue-back: sensitivity lookup at the confirm step, and the two-counter separation | not started |

PR-A1 renders the carried Q&A bulk-inherited (every exchange STILL HOLDS,
which is the brief's own fallback), so it is a valid system state without A2
rather than a stub with a TODO in it.

## Standing constraints

`backend/app/engine/`, `backend/app/honesty/` and `backend/app/parser/` are
frozen for this phase. `spec_to_draft` may be imported and called from
`app/api/`, never edited. If a chunk finds itself editing those trees, stop and
flag.

## Live IDs

> **Last synced: PR #145 (PR-A1), commit `afd8cb3`.** This list is a
> duplicate of the brief's section 9, kept here only because a repo-only
> session cannot read `Projects Misc/`. Per V-87 both are updated in the
> **same commit** at PR close, and per V-136 the sha above is copied from the
> commit, never typed from recall. **If you are reading this with merged PRs
> past #145, assume this index is stale and go read the brief** — that
> duplication is exactly how it drifted the first time, so the marker is the
> drift detector.

V-02 V-03 V-04 V-05 V-06 V-07 V-08 V-09 V-10 V-12 V-13 V-14 V-16 V-17
V-18 V-19 V-20 V-21 V-22 V-23 V-24 V-25 V-26 V-27 V-28 V-29 V-30 V-31
V-32 V-33 V-34 V-35 V-36 V-37 V-38 V-39 V-40 V-41 V-42 V-43 V-44 V-45
V-46 V-49 V-50 V-51 V-52 V-53 V-54 V-55 V-56 V-57 V-58 V-59 V-60 V-61
V-62 V-63 V-64 V-65 V-66 V-67 V-68 V-69 V-70 V-71 V-72 V-73 V-74 V-75
V-76 V-77 V-78 V-79 V-80 V-81 V-82 V-83 V-84 V-85 V-86 V-87 V-88 V-89
V-90 V-91 V-92 V-93 V-94 V-95 V-96 V-97 V-98 V-99 V-100 V-101 V-102 V-103
V-104 V-105 V-106 V-107 V-108 V-109 V-110 V-111 V-112 V-113 V-114 V-115
V-116 V-117 V-118 V-119 V-120 V-121 V-122 V-123 V-124 V-125 V-126 V-127
V-128 V-129 V-130 V-131 V-132 V-133 V-134 V-135 V-136 V-137 V-138 V-139
V-140 V-141 V-142 V-143 V-144 V-145 V-146 V-147 V-148 V-149 V-150 V-151
V-152 V-153 V-154 V-155 V-156 V-157 V-158 V-159 V-160 V-161 V-162 V-163
V-164 V-165 V-166 V-167 V-168 V-169 V-170 V-171 V-172 V-173 V-174 V-175
V-176 V-177 V-178 V-179

Superseded: V-11 by V-25, V-15 by V-26. V-119 is rebalanced by V-126 (tier (b) measured at 1 in 99). V-64 is corrected twice: V-71 moves
the baseline to before PR-0 merges, and V-86 splits "the count stopped
growing" into a bounded sanity check plus an unbounded actual test. V-72's
single-index instruction is corrected by V-87.
Retired: V-01. Never issued: V-47, V-48.

Recorded but deliberately out of phase, and not to be lost:

- **V-57** — enumerated spec-field vocabulary on parser question ids. The
  correct long-term fix for the Q&A-to-field mapping, and it also serves
  Phase 4 discovery mode. Belongs in a parser phase with its own regression
  budget. When it lands, PR-A2's label table becomes a thin adapter and dies.
- **V-61** — port `draftToSpec` to Python and make the server the sole
  authority on spec construction. V-17 and V-36 are the same failure twice:
  something other than the confirmed spec deciding what runs. The client
  should send dial values and nothing else. PR-0 buys time for this; it does
  not substitute for it.
- **V-49** — side-by-side comparison of two variants. Ask before building.
