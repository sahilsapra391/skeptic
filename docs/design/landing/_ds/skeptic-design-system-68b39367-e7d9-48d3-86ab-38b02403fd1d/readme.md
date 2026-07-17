# Skeptic Design System

**Skeptic — the backtester that argues with you.** A research instrument for retail options traders: describe a strategy in plain English, Skeptic compiles it, backtests it on real options data, then attacks its own result (out-of-sample split, walk-forward, Monte Carlo, sensitivity sweeps) and delivers an honest verdict about whether the edge is real or curve-fit noise. The entire identity is **adversarial honesty** — a scientific instrument built by someone who lost money to a pretty equity curve, not a trading app chasing dopamine. Subject world: quant research desks, lab notebooks, aviation instrument panels. Never: crypto dashboards, confetti, "AI magic sparkle."

One product surface: the **desktop web app** (Next.js) — five core screens: New Analysis (chat-led composer), Spec Confirmation, Results/Verdict (the hero screen), Strategy Library, Data Observatory, plus Settings and the run-in-progress "gauntlet" state. Mobile is read-only review, not a creation surface. Radical simplicity is a hard requirement: English is the only configuration language, one primary action per screen, progressive disclosure everywhere.

## Sources

- GitHub: **https://github.com/sahilsapra391/skeptic** — ground truth. Tokens from `frontend/app/globals.css` + `frontend/tailwind.config.ts`; fonts from `frontend/app/layout.tsx`; components under `frontend/components/`; brand SVGs from `frontend/public/brand/`; product philosophy in `docs/claude-design-brief.md`, `docs/skeptic-prd.md`, `docs/HONESTY.md`. Explore the repo to design deeper surfaces (e.g. `frontend/app/data/page.tsx`, `frontend/components/charts/market-chart.tsx`, `frontend/components/spec/spec-screen.tsx` were not recreated here).

## Content fundamentals

- **Verdicts lead with the uncomfortable part.** "This edge disappears out-of-sample" comes before any praise. A damning verdict gets the same visual weight as a favorable one.
- **Sentence case, plain verbs, no exclamation marks, no hype, no emoji.** "Run analysis," never "Discover your edge! 🚀". The only pictographic character in the product is the functional `⚠` on warn lines.
- **The product speaks in first person, confidently adversarial:** "Describe a strategy. I'll try to break it." / "QUESTION 1 OF 2 — I DON'T GUESS" / "Hunting for ambiguity. I don't guess…". The user is "you/your".
- **Refusal is a feature.** "I don't have enough data to answer that" is said proudly; data limitations are surfaced, never hidden ("gaps = no greeks that day, never interpolated"). Insufficient-data states are principled, not broken.
- **Numbers are sacred.** Every number traces to a computation; nothing decorative is dressed as data. Honest counters everywhere: "day 247 of collection →", "$25k start · net of costs".
- **Microcopy register:** lowercase mono for system status and fine print ("reading the lake…", "re-judged at your evidence bar"); ALL-CAPS mono with letterspacing for structural labels ("VERDICT — THE HONEST READ", "WHILE YOU WAIT"); ` — ` em-dash appositions and ` · ` interpunct separators are house style.
- **Standing disclaimer on every results surface:** "Research tool, not financial advice. Backtests overstate live results."
- Two verbiage registers exist (institutional/retail) — same numbers, different words. Headlines rotate through variations of the same promise ("Bring your thesis. I'll bring the evidence.").

## Visual foundations

- **Palette:** dark-first near-black console (`--ground #0b0c0e` → panel/raised ladder), with a paper light theme (`#f4f4f5` / ink `#16181d`). Theme swaps at runtime via `data-theme` on `<html>` (default follows market hours: light 8am–6pm ET); accent via `data-accent` (cyan default, sage, lavender, rose).
- **THE COLOR CONTRACT (non-negotiable):** the trust/accent hue family (`--ac*`, "trust") colors verdicts, trust bands, and controls ONLY. P/L green/red (`--pl-pos`/`--pl-neg`) colors profit/loss data ONLY. They never mix — a strategy can be profitable AND untrustworthy. Amber `--warn` is for warnings/demo badges, never P/L. No green-tinted theming that makes the product feel "up".
- **Type — three voices, no exceptions:** Archivo (sans) for body/UI; IBM Plex Mono for every number, label, and code-like artifact (tabular, letterspaced caps labels); Newsreader serif (400/500 + italic) for headings and important moments ONLY — the hero headline, page titles, the verdict headline. Sizes are deliberately off-grid (11.5, 12.5, 13.5, 14.5px…) — copy exact values, never round.
- **Surfaces:** panels are `--panel` with 1px `--line` border, 14px radius. Depth is a background ladder (ground → panel → raised → raised-2 → raised-3), not shadows. Shadows only on floating elements: the composer (`--shadow-soft`) and tooltips/hover cards (`--shadow-pop`).
- **Dashed borders carry meaning:** refusal, withheld, empty states, and "add new" affordances are dashed (`border-dashed`); a dashed trust border = verdict withheld. Never decorative.
- **Radii system:** 22 composer · 16 verdict block · 14 panels/cards · 12 tiles · 11 segmented shells · 10 buttons · 9 inputs/tooltips · 8 list rows · 3 bars · full pills for chips.
- **Charts:** thin strokes (1.2–1.8px), no area fills, hairline `--grid` lines, mono 10px annotations, crosshair + hover card, honest gaps (holes, never interpolation). OOS regions shaded with `--oos-shade`. Heat cells are `rgb(var(--heat-rgb) / alpha)`. The equity line is neutral `--chart-bright`; only the drawdown line is P/L red.
- **Motion:** restrained and instrument-like. 0.18s ease on every interactive state (never hard-snap); `fade-rise` 0.25s entrances; clip-path chart reveal/conceal; 1.2s opacity pulse for "live/working"; text shimmer while thinking; the boot splash draws the wordmark on (pathLength dash). No bounces, no confetti, no celebratory motion — a profitable backtest is a hypothesis, not a win.
- **Hover states:** text lightens (`ink-4 → ink-2/ink`), borders lighten (`--line → --line-hover`), backgrounds step up one raised level. Primary buttons: `bg --ink` lightens to `--ink-2`; disabled = `--raised-2` bg + `--ink-4` text + not-allowed cursor.
- **Layout:** fixed left nav rail (56px collapsed / 196px default, drag-resizable), content in a 1620px shell with 34px gutters. Focused flows narrow to a centered column (960px composer, 684px chat, 650px gauntlet). Backgrounds are flat color — no images, textures, or gradients (the only gradient in the product is the verdict block's faint trust wash `linear-gradient(180deg, var(--acd), var(--ac-faint))` and the thinking shimmer).
- **Transparency/blur:** essentially none — `--overlay-panel` is a 92% solid, no backdrop blur.

## Iconography

- **Bespoke inline stroke SVGs, hand-drawn per use** — 20×20 (nav) or 16×16 (controls) viewBox, `fill="none" stroke="currentColor"`, stroke-width 1.3–1.8, round caps/joins. No icon font, no Lucide/Heroicons, no PNGs. Copies live in `assets/icons/` (inline them so `currentColor` works; don't `<img>` them).
- **Unicode glyphs are functional icons:** `✓ ▶ ○` gauntlet stage states, `▸ ▾` disclosure, `‹` back, `→` forward links, `↻ ⟲` re-run, `⚠` warn, `·` separators, `?` in a bordered circle for hints. No emoji, ever.
- **Brand marks** in `assets/brand/`: `s-mark-{black,gray,white}.svg` (the S tile), `wordmark-{black,gray,white,white-slash-gray}.svg`, `skeptic-draw-{black,white}.svg` (self-drawing animated wordmark for the boot splash), `favicon-dark-tile-512.png`, `og-image-1200x630.png`. Light theme uses black marks, dark uses white.

## Index

- `styles.css` → imports `tokens/` (fonts, colors, typography, spacing, motion, base).
- `assets/brand/` logos + `assets/icons/` stroke SVG set.
- `guidelines/` — specimen cards (type, colors, spacing, brand, voice).
- `components/` — reusable primitives (namespace `window.Skeptic`):
  - `verdict/` **VerdictBlock**, **TrustBand** (hero + card sizes, withheld state) — the signature element
  - `core/` **Panel**, **MetricTile**, **Button**, **Hint**, **PulsingDots**, **DemoBadge**, **Disclaimer**
  - `composer/` **Composer**, **PresetChip**, **CoverageChip**, **QuestionCard**, **ThinkingIndicator**
  - `navigation/` **NavRail**
  - `gauntlet/` **GauntletProgress**
- `ui_kits/app/` — click-through recreation of the product: New Analysis → thinking → clarify → gauntlet → results, plus Library and Settings (live theme/accent switching).

**Intentional additions:** `Button` (the app styles buttons inline per-instance; this codifies the observed primary/secondary/ghost/pill recipes). Demo data in the UI kit mirrors `frontend/lib/demo.ts` shapes.

**Not recreated (source available in repo):** Data Observatory screen, the interactive market chart (`market-chart.tsx`), spec-confirmation screen, results Q&A backend. Fonts load from Google Fonts CDN — production uses the same Google families via `next/font`; no font binaries exist in the repo.
