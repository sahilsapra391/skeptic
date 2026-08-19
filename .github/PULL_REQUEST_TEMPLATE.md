## What changed

<!-- One paragraph. What the PR does, and why it is the right depth of fix. -->

## Verification

Paste real output or say plainly what you did not run. A skipped step named is
worth more than a green tick that covers a subset.

- [ ] `uv run --project backend python -m pytest backend/tests` green. Note the
      count. Two things in that command are load-bearing. `python -m` rather
      than the bare `pytest` shim, which resolves a 3.13 interpreter against a
      3.12 project and fails the whole suite with `ModuleNotFoundError:
      fastapi`. And `--project` rather than a leading `cd backend &&`, which is
      a no-op chain when the shell is already there.
- [ ] `npm --prefix frontend run lint`, `... run typecheck` and `... run build`
      green. Same reason for `--prefix` over `cd frontend &&`.
- [ ] The V-18 round-trip guard **executed** rather than skipped (it needs node
      22.6+ and fails rather than skipping, so confirm it ran).
- [ ] The overfit fixture `backend/tests/fixtures/overfit_strategy.json` is still
      flagged by the gauntlet. A green run on it is a failing build.
- [ ] Nothing above was run through a relative `cd`. The forms given need no
      directory at all, which is the point: `cd frontend && npm run lint`
      succeeds at nothing when the shell is already in `frontend`, and the clean
      report that follows reads as success on work that never ran. If you must
      `cd`, make the path absolute or print `pwd` in the same invocation.

## Manual checks no automated check can make

Only the rows this PR could plausibly break. Delete the rest.

- [ ] **Library card click contract** (if this PR touches
      `frontend/app/(app)/library/page.tsx` or the report bar in
      `frontend/components/results/results-view.tsx`): click the **body** of a
      card and land on `/runs/<id>`; click **run a variant ›** on the same card
      and land on `/new?variant=<id>` with the variant framing. Do both on a
      card that carries an ordinal badge, since the action sits beside it there.

      This row exists because the same defect shipped twice in one PR, inverted
      the second time. First a `<button>` nested inside the card's `<a>`, which
      swallowed the action. Then, fixing that, the card content was raised above
      the overlay link and swallowed the card body instead. tsc, eslint and
      1,092 backend tests passed through both, because nothing in either suite
      clicks a card. The invariant to preserve: the overlay `<Link>` is the
      **last child and carries no z-index**, so it paints above the content by
      DOM order, and the action's `relative z-10` is the single thing that beats
      it. Content carries no stacking context at all. When `frontend/` gains a
      test runner this row becomes one real test and dies.
- [ ] **The carried Q&A block makes no validity claim** (if this PR touches
      `frontend/components/results/how-built.tsx`, `backend/app/api/variant.py` or
      `backend/app/api/provenance.py`): open a variant whose parent had at least
      two exchanges and whose edit changed a field one of them asked about. Every
      carried card must render identically — no marker, no dimming, no "still
      holds" — and the edit must appear in WHAT CHANGED under its human label.

      V-213 removed per-exchange markers because deciding that an edit superseded
      an answer needs a question-to-field mapping that does not exist; matching on
      values was confidently wrong on the 0DTE path and right at most 13% of the
      time. The absence is the contract, so it is what gets checked. A reviewer
      seeing an unused match in `reconcile` and helpfully wiring it to the screen
      is the regression this row exists to catch.
- [ ] **Print / saved PDF** (if this PR touches a results surface): the variant
      button is absent, the lineage line and the carried-Q&A header are present.
- [ ] Any results surface this PR adds or changes still shows the data window it
      was computed on, and still carries the research-tool disclaimer.

## Docs

- [ ] README updated in **this** PR if the change is major (what the app does, a
      guardrail, architecture or data flow, a data source, the honesty stages,
      the API surface, or how it is operated). Mermaid diagrams the change
      contradicts are updated too. Test counts regenerated, not hand-incremented.
- [ ] Prose describing changed behaviour is swept, not just the first grep hit
      (V-182: the Settings/costs tip survived two PRs, and its retail-register
      copy survived the first fix).
- [ ] Identifiers, shas and test names cited in the description are copied from
      command output, not typed from recall, and each cited test exists and was
      seen to run (V-136, V-142).
