# Asset requests — exact moments to export

Recordings referenced: REC 1 = 4:42:16 PM (composer flow, 79s) · REC 2 = 4:43:45 PM (chart-teach, 47s) · REC 3 = 4:44:46 PM (results scroll, 3.4s).

## Theme decision (blocks all clips)
All three captures are light theme (market-hours). The landing is dark. Pick ONE:
- **A (recommended):** force dark in Settings → re-record the two clips below (~2 min of work), OR
- **B:** keep light captures as intentional paper-on-ink contrast — then keep the stills as-is.

## Clips
1. **CLIP B — S2, step 01 artifact** · REC 2 **0:04.0 → 0:08.0** (4.0s loop)
   Chart-teach: pin lands on the SPY chart, example strip updates ("example 1 — Jul 14, 08:10 ⟶ Jul 14, 08:40 · +0.5%"), cursor moves toward "That's the idea →".
   Crop: drop browser chrome + nav rail — content column only (1920 source ≈ x 172→1920, y 112→1080). Loop with 0.6s hold on the pinned state. Muted, autoplay below fold, poster = `stills/chart-teach.png`.
2. **CLIP A — optional, hero split variant (1b) only** · REC 1 **0:22.0 → 0:27.5** (5.5s loop)
   Spec dials settle → WINDOW dropdown opens on real session counts (220 / 711 / 1,213 / 2,468 / 3,480). Hold 0.8s on open dropdown. Same crop. Not used in the chosen 2a layout — export only if we revive the split hero.

## Stills (already cropped, in `stills/`)
- `chart-teach.png` — REC 2 @ 0:06 (poster for CLIP B). On the page it currently renders through `invert(1) hue-rotate(180deg)` as a SIMULATED dark placeholder — replace with a true dark capture and drop the filter.
- `spec-window-open.png` — REC 2 @ 0:20 (window dropdown open; 1b poster / documentation)
- `spec-confirmed.png` — REC 1 @ 0:24.5 (full dial grid; documentation)

## From the owner (not exports)
- Pinned run ids: one refused (low-trade-count cause), one blessed/graded, plus the run id + payload for the REAL "84% of profit came from 13 lucky days" SPY .30Δ short put (REC 1 @ 1:05).
- Coverage endpoint values for the receipts tiles ("2009 chains since" hand-verify; "day N of collection" live).
- Legal page text: Terms, Privacy, Refunds (launch blockers per PRD H).

## Brand files in use (design system, updated cut — assets/brand/*-u1.svg)
- `skeptic-draw-white-u1.svg` — hero draw-on (pathLength dash), morphs into nav on scroll, theme-recolored via currentColor
- `wordmark-{white,black}-u1.svg` — nav/footer lockups, theme-swapped
- `s-mark-{white,black}-u1.svg` — mobile nav tile, theme-swapped
No redraws; no recolors beyond the theme swap.
