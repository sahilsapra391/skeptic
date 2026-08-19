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
| PR-A1 | Schema, endpoint, tier classifier, locks, costs/seed/window inheritance, zero-edit guard, provenance sections 1-3 + section 5 diff, entry points, lineage header and grouping | **merged** (#145, merge commit `702487c`) |
| PR-A2 | Q&A reconciliation as TELEMETRY ONLY (V-213): no per-exchange state renders. The field-label table, the diff's human labels, the unmapped counter | merged in #146 |
| PR-B | The argue-back: exact-cell sensitivity lookup at the confirm step, the DSR's sweep disclosure, and the three-counter separation | merged in #153 |

**PR-B's opening constraints are settled (V-229 to V-231):** field identity comes
from the V-208 table and nowhere else; the DSR line, the lineage ordinal and
V-214's telemetry are three numbers with three homes and one test asserting they
cannot be mistaken for each other; and the argue-back renders only what
`sensitivityDetail` holds for the exact cell, with no interpolation and no
nearest-neighbour guess, because the marker died for rendering a weak mapping.

**Two standing rules came out of this phase.** **V-246:** a check keyed on prose is
fooled by prose — key on the artifact (sha anchors, stored values, parsed code), and
where prose must be checked, strip everything that is not the claim. Three instances:
the hook that grepped the phrase it guarded, the table keyed on LLM-authored labels,
the test that tripped on its own comment. Companion: an instruction's keyword is not
its claim either (V-27's "attempts" turned out to mean trials, not variants).

**V-228:** a fixture that exists to catch a
boundary failure asserts its own proximity to that boundary, so loosening it fails
the test rather than emptying it. Four instances in this phase, two of them
consecutive attempts at the same fix. Full derivation in the brief's learnings.

**Where A2 leaves PR-B.** Nothing in B depended on the marker, so its scope is
unchanged. Two things A2 established are worth carrying in: the V-208 label table
is the path-to-label authority and B's sensitivity copy should read from it rather
than writing its own field names (V-217), and V-26's two-counter separation now
has a third number nearby that must not be confused with either — the V-214
reconcile telemetry is neither lineage nor trials, carries no statistical claim,
and never appears in a verdict or an honesty payload.

PR-A1 renders the carried Q&A bulk-inherited (every exchange STILL HOLDS,
which is the brief's own fallback), so it is a valid system state without A2
rather than a stub with a TODO in it.

## Three supersessions recorded in A2 (V-207, V-219)

**V-30 is now fully superseded, in both directions.** V-203 dropped NOT
APPLICABLE; V-213 dropped SUPERSEDED and STILL HOLDS as rendered states. No
per-exchange validity claim renders at all. The carried block claims provenance
only: these questions were asked on the parent run, and nothing about whether any
answer remains true. Both directions of that claim are above the bar the
mechanism can clear.

**The rule that generalizes (V-213):** a per-exchange claim renders only when the
mapping from question to field is DETERMINISTIC, which is V-57's exact-id world.
Until then the linkage is instrumentation and stays off the screen.

**Why (V-216, with the derivation, per V-136's citation bar).** Value matching
has no relevance check. An answer matches a changed field by value alone, and
V-201's uniqueness cannot supply relevance because it only compares within the
changed set: when the field an answer actually governed was not edited, it
contributes no diff row, both guards stay silent, and a confident marker lands on
the wrong card. Eleven of the twelve findings that survived adversarial review
were instances of that one cause.

The failure is ordinary, not exotic. `spec_version` is a required top-level spec
field that `diff_specs` diffs like any other, and `frontend/lib/spec.ts`
recomputes it on every rebuild, so dragging the DTE dial to 0 lifts it 1 to 4.
Any carried answer of "1" then renders "superseded by spec version 1 to 4" on the
flagship 0DTE path. Reproduced by execution, not by argument.

Two earned constants, both measured read-only against production (99 runs, 34
with provenance, 9 carrying any conversation, 23 recorded answers):

  * **34.8% parse ceiling** — 8 of 23 answers equal ANY scalar in their own run's
    spec. Computed by `scripts/audit_answer_canonicalization.py`, which pushes
    each recorded answer and every spec scalar through the same `canonical_token`
    and counts exact matches.
  * **13.0% unique-anchor ceiling** — only 3 of 23 answers identify EXACTLY ONE
    real spec field, with bookkeeping fields (`spec_version`, `meta.*`) excluded.
    4 of 23 are ambiguous across two or three fields and would have to be
    suppressed. 13% is itself an overestimate of the marker rate, because a
    marker also requires that the uniquely-matched field be the one the user
    edited.

A feature that can be right at most one time in eight, and is confidently wrong
on the default path, is a bad trade against the invariant the brief ranks above
everything else. These two numbers are the empirical case for V-57 whenever that
phase is pitched.

## Two supersessions recorded earlier in A2

**V-53 is superseded by V-200.** The label table was specified as
question-label-to-spec-field. It is not buildable: parser question ids AND
question text are both authored by the model per call, with no enumerated
vocabulary, no validation, no uniqueness constraint and a positional `q{i}`
fallback (`parse.py:566-574`). A table keyed on either is keyed on a model
version and fails silently on any model or prompt change. Thirty candidate
mappings were proposed and zero survived. What replaced it matches on stored
truth: a recorded ANSWER, canonicalized, against the `parent` value of a changed
diff row. The cause is worth naming because it is the V-141 shape again, in the
brief itself: a table was specified against an artifact nobody had inspected.

**V-30(c) is superseded by V-203.** NOT APPLICABLE is dropped. A locked field
produces no diff row, so finding one means scanning unchanged values, which
breaks diff-anchoring and widens the false-positive surface for a state measured
at 1 run in 99 whose real disclosure is the locked dial's own copy. Tier (b)
exchanges render STILL HOLDS.

**The binding constraint is data, not mechanism (V-211).** Measured read-only
against production: 99 runs, 34 with provenance, 9 carrying a conversation at
all, 23 recorded answers. All 23 canonicalize, but only 8 (34.8%) equal any value
in their own run's spec, which is the ceiling on how often SUPERSEDED can ever
fire. Substring matching was considered and rejected on evidence: those 14
non-anchoring answers produce 80+ path hits between them, because a prose answer
containing any digit matches every numeric field sharing that digit. So V-57's
trigger will accumulate slowly, and that is the finding rather than a failure of
the counter.

**V-181, discharged:** a close commit cannot truthfully record the merge that
follows it, so A1's own close left it reading "complete, in review" and **A2's
first commit** — this one — marks it merged, from the branch that can actually
see the merge. The same rule applies to A2's own close: expect one step of lag
at the seam, and read it as the scheme working rather than as drift.

## The phase is closed

**The retrospective was amended after close (V-247a/b/c):** the incident family is six
members, not five, with a floor caveat and the V-149/V-151 pair marked as its strongest
evidence (same fault on both sides of a gate being installed). The earned constants
became a table with a population, derivation and DATE each, which immediately caught
one of them drifting — "9 of 99 runs carrying a conversation" re-checked at 11 of 101
within hours. The two ungated family members are the next phase's first infrastructure
item. Amendments live in the brief; this pointer names them so a repo-only reader is
not working from the pre-amendment text.

**Secrets (V-250):** two were exposed in this phase's transcript, neither committed, and
the owner decided on 2026-08-19 not to rotate yet. That is recorded as a decision with a
trigger, not a pending task, and the trigger lives in
[`LAUNCH-CHECKLIST.md`](LAUNCH-CHECKLIST.md) — rotate both before the database holds
anyone but the owner.

The **retrospective** is the final entry in the brief's learnings: the yield tables
(13.0% killed the marker, 98.8% shipped the argue-back), the five-member incident
family, the earned constants with derivations, and the one-sentence lesson —
everything keyed on something regenerable died, everything measured against stored
truth survived.

The **deferred ledger** closes it, with triggers verbatim so the next phase inherits a
list rather than an archaeology: V-49, V-57, V-61, V-129/V-130, V-113, V-146, V-183,
V-198, V-244. None promoted, none dropped.

## Standing constraints

`backend/app/engine/`, `backend/app/honesty/` and `backend/app/parser/` are
frozen for this phase. `spec_to_draft` may be imported and called from
`app/api/`, never edited. If a chunk finds itself editing those trees, stop and
flag.

## Live IDs

<!-- last-synced-sha: 1f9a803 -->

> **Last synced: PR #153 (PR-B), through its merge commit `1f9a803`.**
> Stamped by the first commit after #153 merged — the one-step handoff, for the
> third and last time this phase, and the only one of the three that was guarded
> rather than remembered. The sha the hook reads is the `last-synced-sha` anchor
> above, not this prose: V-221 records what happened when it grepped the prose
> instead and a rewording killed it. Update the anchor and this sentence
> together; the anchor is the enforced one.
>
> **The variant-runs phase is closed.** PR-0, A1, A2 and B are all merged. The
> brief's learnings carry the retrospective and the deferred ledger; read those
> before opening anything that touches this surface.

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
V-176 V-177 V-178 V-179 V-180 V-181 V-182 V-183 V-184 V-185 V-186 V-187
V-188 V-189 V-190 V-191 V-192 V-193 V-194 V-195 V-196 V-197 V-198 V-199
V-200 V-201 V-202 V-203 V-204 V-205 V-206 V-207 V-208 V-209 V-210 V-211
V-212 V-213 V-214 V-215 V-216 V-217 V-218 V-219 V-220 V-221 V-222 V-223
V-224 V-225 V-226 V-227 V-228 V-229 V-230 V-231 V-232 V-233 V-234 V-235
V-236 V-237 V-238 V-239 V-240 V-241 V-242 V-243 V-244 V-245 V-246 V-247
V-248 V-249 V-250 V-251

**The V-series closes at V-251.** The next phase opens its own sequence.

Superseded: V-11 by V-25, V-15 by V-26. **V-53 by V-200** (the label table is
not buildable; value matching replaces it). **V-30 by V-203 and V-213**, in both
directions: NOT APPLICABLE dropped, then SUPERSEDED and STILL HOLDS dropped as
rendered states. **V-229's payload half by V-239** (the lookup reads the raw stored sweep, not the display-only `sensitivityDetail`; identity by replaying the setter, not by a name table). **The rendering half of V-200 and V-201 by V-213** — the
matching logic survives as telemetry; nothing it produces reaches a screen. V-189 is ANSWERED by V-197 as corrected on 2026-08-19: the mechanism is
`load_local_env()` at `backend/app/main.py`, which loads `collector/.env` and so
grants any local boot production's database and auth gate by design. V-119 is rebalanced by V-126 (tier (b) measured at 1 in 99). V-64 is corrected twice: V-71 moves
the baseline to before PR-0 merges, and V-86 splits "the count stopped
growing" into a bounded sanity check plus an unbounded actual test. V-72's
single-index instruction is corrected by V-87.
Retired: V-01. Never issued: V-47, V-48.

Recorded but deliberately out of phase, and not to be lost:

- **V-57** — enumerated spec-field vocabulary on parser question ids. Still the
  correct long-term fix, and A2 measured exactly how much it would buy. Belongs
  in a parser phase with its own regression budget. Its trigger is the V-204
  tally, which will grow slowly for the reason V-211 records: the constraint is
  how little Q&A exists, not the mapping mechanism. A slow counter is the
  finding, not a broken counter.
- **V-217 — the V-208 label table keeps its second consumer pending.** It has
  one consumer now, the WHAT CHANGED list. It is the path-to-label authority and
  the marker's death does not narrow it, so it must not be folded into the diff
  renderer as a local map. Its second consumer arrives with V-57. The
  unmapped-at-render tally stays with it.
- **V-210 — the source-hunt rule.** A mechanism hunt closes at the code that
  performs the load, never at the exoneration of a candidate. "Not X" is a
  narrowed search, not an answer. Two instances this phase, both of which
  recorded a true negative as done: the launch.json pin (V-188, which worked for
  a reason nobody knew) and the uv check (V-197, which proved uv does not load
  collector/.env and concluded the origin was unknown while the loader sat at
  main.py:20).
- **V-61** — port `draftToSpec` to Python and make the server the sole
  authority on spec construction. V-17 and V-36 are the same failure twice:
  something other than the confirmed spec deciding what runs. The client
  should send dial values and nothing else. PR-0 buys time for this; it does
  not substitute for it.
- **V-49** — side-by-side comparison of two variants. Ask before building.
- **V-183** — the two Library observations, deferred under ONE trigger:
  revisit when any root carries **five or more variants in real use**. The
  ordinal badge distinguishes same-name variants by identity but not by
  content, and nothing on the card says what a variant changed. If built, the
  fix is **the first `what_changed` row on the card** — nothing else, no
  second mechanism. A threshold observation, not a TODO: below five per root
  the badge plus one click is enough.
- **V-182** — process rule, now part of the close checklist beside the
  citation sweep: a PR that changes behaviour sweeps user-facing prose
  describing that behaviour, scoped to strings naming what changed. Earned by
  the Settings/costs tip, which V-36 falsified and which survived two PRs
  because nobody looked — and whose second copy (the retail register) survived
  the first fix because that fix took the first grep hit instead of sweeping.
- **V-184 — LANDED** in this commit as `.claude/hooks/pointer-sha-ancestor.sh`,
  wired as a PreToolUse hook on `git push`. It asserts the Last-synced sha both
  resolves and is an ancestor of HEAD, escalating with the specific failure
  named (orphaned by an amend, or off on another branch). Silent when the
  pointer is gone or carries no marker. Kept here as the record of why it
  exists: converting the sha rule into an unwritable wrong case is the move that
  ended every recurring failure this phase.
