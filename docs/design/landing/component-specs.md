# Component specs — landing (L4)

Reference implementation for all of these is live in `Skeptic Landing.dc.html` option 2a (desktop) / 2b (mobile). Tokens + components from the Skeptic design system; nothing below invents new styles.

## 1. Hero embed frame
- The composer is the REAL app component (same parser). Landing shell is static; hydrate the app on first keystroke/interaction (PRD F). Until hydration, the frame is visually identical.
- States: **empty** (rotating placeholder, 3.6s, pauses on focus/typing) → **typing** → **submitted-anon**: inline row = dashed Turnstile chip + "queued honestly — {n} runs ahead of you" + "trial run: daily clock · ≤3-year window". Composer `busy` while queued.
- Past global anon budget: the row swaps to the signup CTA ("trials are busy — create a free account").
- Preset chips fill the composer (app PresetChip). Chart affordance = dashed chip, routes to chart-teach.
- Min hit target 44px on all mobile controls.

## 2. Scroll-morphing wordmark
- Hero wordmark = brand `skeptic-draw-white.svg` (design system) inside a `position:sticky` wrapper — draw-on via pathLength dash (~1.65s), theme-recolored via currentColor, once per visit via sessionStorage; click replays.
- On scroll it translates+scales into the empty nav-left slot. Scrub-linked: progress p = clamp(scrollTop/260), eased smoothstep, transform-only (60fps), fully reversible.
- Desktop constants (1440 shell): translate(-630px·q, -28px·q) scale(1 − 0.8q); nav border-bottom fades in with q.
- Mobile (2b): static nav s-mark; morph optional later (constants: translate(-132px·q, -26px·q) scale(1 − 0.7q) at 390).

## 3. Verdict showcase (S3)
- Rendered server-side from stored run payloads (pinned run ids) — zero engine load, no screenshots.
- Refusal block = VerdictBlock refusal variant **plus a new `refusalActions` slot**: pill row replacing the "track progress in Data →" link. Actions: two trustpill re-runs (window-picker options with real session counts + runtime) + one pill "edit the spec". Cause line references the run's own window.
- Range rail: 3 rows = name (mono) + meta + TrustBand `card` variant (+ `withheld` for refusals) + the run's verdict headline as a serif-italic quote. Band geometry from payload, never hand-placed. Rows hover: bg → raised, border → line-hover.
- Mobile: refusal card unchanged; graded verdicts collapse evidence behind "▸ evidence" (44px target); grid never squeezes below 480px.

## 4. Receipts strip (S4)
- MetricTile row (5-up desktop, 2×2 + line mobile). Values from the coverage endpoint at build time or hand-pinned with a verify note (build plan L4). "2009 chains" flagged VERIFY.
- CoverageChip row mirrors app home (SPY / QQQ-IWM / minute bars) — render live, keep asymmetry honest.

## 5. Copilot demo (S5)
- Pure CSS, 14s infinite loop, no JS at runtime. Elements: candle chart (generated, net-up with real pullbacks), cursor (wander→settle→hold at 3 waypoints, ease-in-out segments), fill dots + flags at candle closes (SOLD @ 2.31 idx 9 · BOUGHT BACK @ 1.12 idx 27), three signal cards, reasoning rail (6 lines, staggered opacity).
- All prices/dates from the stored demo run's trade log. P/L colors: candles + the single +$115 only. Trust cyan for the agent voice; amber for the stand-aside.
- Candle gen (reference impl in the DC): 56 candles, 15px pitch, piecewise target path with anchors at the two fills, deterministic sin-hash noise ±14, wicks 3–12px.
- Reduced-motion: freeze frame with all cards + lines visible. Mobile: chart (no overlays) + rail stacked below.

## 6. Pricing card (S6)
- One panel, three columns (rows on mobile), hairline separators — never three tier cards.
- PRD-E honesty lines verbatim under the panel. Primary button (trust fill) scrolls to the composer; ghost secondary to signup.

## 7. Sign-in affordance
- Nav right: ghost "Sign in" + secondary "Get 5 free backtests". Nav is sticky; border fades in on scroll.
- Post-anon-run conversion lives on the results screen: "Keep this run + get 5 free backtests — create an account." (claim flow re-parents the run).

## 8. Footer + theme control
- navbg band: brand col (wordmark, one-liner, live day counter) + PRODUCT + LEGAL columns + © row ("every number on this page is computed — none decorative").
- **Theme**: landing supports dark + light via `data-theme` on the page root, same tokens as the app. Default = **market hours** (light 8am–6pm ET, dark after close, re-checked each minute). Footer segmented control light / dark / market hours persists to localStorage (`sk-landing-theme`). Brand marks swap white↔black with theme; the S2 still swaps to the light capture (dark uses the invert placeholder until re-recorded).
- Disclaimer intentionally absent per owner (Jul 16) — PRD H conflict logged in README open decisions.

## Performance notes
- Zero video above the fold in 2a (draw-on SVG only). S2 clip lazy-loads below the fold, poster-first, muted loop, reduced-motion → still.
- Landing static on edge; app bundle deferred until first composer interaction.
