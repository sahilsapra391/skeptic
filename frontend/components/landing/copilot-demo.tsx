"use client";

/**
 * B5 — "Coming next" copilot demo: deterministic candle chart + 14s scripted
 * replay (cursor, fill dots, flag pills, signal cards, reasoning rail).
 *
 * Every number in the copy is from the stored demo run's trade log
 * (copy-deck §5, verified) — never alter or invent values here.
 *
 * The candle field is a pure module-scope computation (GLSL-style sin-hash,
 * no Math.random, no Date) with coordinates rounded to 2dp so server and
 * client emit identical markup — hydration-safe even across engines.
 *
 * Desktop overlays are authored in the design's fixed 845×324 coordinate
 * space (24px header band + 845×300 chart) and scaled as ONE unit so cursor
 * paths, pills and cards stay glued to their candles at every panel width;
 * mobile (<md) drops the overlays entirely per design 2b.
 *
 * P/L colors (--pl-pos/--pl-neg) appear ONLY on candle bodies/wicks and the
 * single "+$115" — market data, the one place they're allowed on this page.
 * The agent voice is trust; STAND ASIDE + rail line 5 are warn.
 */

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

import styles from "./copilot-demo.module.css";

// stage = 845 wide × 324 tall (24px header band + 845×300 chart)
const STAGE_W = 845;

// ---- deterministic candle field (design generator, dc.html lines 937–958) ----

// price path waypoints — exact design values
const PATH: ReadonlyArray<readonly [number, number]> = [
  [0, 225], [45, 205], [80, 190], [110, 150], [142.5, 172], [185, 145],
  [225, 160], [265, 128], [305, 148], [350, 118], [380, 140], [412.5, 126],
  [455, 148], [500, 112], [535, 132], [575, 95], [610, 118], [649.5, 83],
  [690, 106], [730, 88], [775, 98], [845, 60],
];

// anchor candles land exactly under the overlay beats: i=9 → fill dot 1,
// i=27 → fill dot 2, i=43 → the STAND ASIDE cursor pause
const ANCHORS: Readonly<Record<number, number>> = { 9: 172, 27: 126, 43: 83 };

// classic GLSL sin-hash — deterministic, no Math.random
function fr(n: number): number {
  const s = Math.sin(n) * 43758.5453;
  return s - Math.floor(s);
}

function lerpPath(x: number): number {
  for (let i = 1; i < PATH.length; i++) {
    if (x <= PATH[i][0]) {
      const t = (x - PATH[i - 1][0]) / (PATH[i][0] - PATH[i - 1][0]);
      return PATH[i - 1][1] + t * (PATH[i][1] - PATH[i - 1][1]);
    }
  }
  return PATH[PATH.length - 1][1];
}

const r2 = (n: number) => Math.round(n * 100) / 100;

type Candle = {
  cx: number;
  bodyX: number;
  bodyY: number;
  bodyH: number;
  wickY1: number;
  wickY2: number;
  up: boolean;
};

// computed once at module scope; 2dp rounding keeps SSR/CSR markup identical
const CANDLES: readonly Candle[] = Array.from({ length: 56 }, (_, i) => {
  const cx = i * 15 + 7.5;
  const base = lerpPath(cx);
  const anchor: number | undefined = ANCHORS[i];
  // anchor open = close + 7 forces the anchor candle green (SVG y inverted)
  const close = anchor !== undefined ? anchor : base + (fr(i * 7.13 + 1.7) - 0.5) * 14;
  const open = anchor !== undefined ? close + 7 : base + (fr(i * 3.77 + 9.1) - 0.5) * 14;
  const bodyY = Math.min(open, close);
  const bodyH = Math.max(1.5, Math.abs(open - close));
  return {
    cx,
    bodyX: i * 15 + 4,
    bodyY: r2(bodyY),
    bodyH: r2(bodyH),
    wickY1: r2(bodyY - (3 + fr(i * 5.1 + 3) * 9)),
    wickY2: r2(bodyY + bodyH + 3 + fr(i * 9.3 + 5) * 9),
    up: close <= open,
  };
});

function CandleMarks() {
  return (
    <>
      {CANDLES.map((c, i) => {
        // P/L colors allowed: candles are market data
        const col = c.up ? "var(--pl-pos)" : "var(--pl-neg)";
        return (
          <g key={i}>
            <line x1={c.cx} x2={c.cx} y1={c.wickY1} y2={c.wickY2} stroke={col} strokeWidth={1} />
            <rect x={c.bodyX} y={c.bodyY} width={7} height={c.bodyH} fill={col} />
          </g>
        );
      })}
    </>
  );
}

// ---- shared class strings ----

const SIG_CARD =
  "absolute w-[225px] rounded-[9px] border border-line-hover bg-raised px-[13px] py-[9px] shadow-pop";
const SIG_KICKER = "font-mono text-[10px] tracking-[0.1em]";
const SIG_BODY = "text-[12.5px] leading-[1.5] text-ink-2";
const SIG_META = "mt-1 font-mono text-[10.5px] text-ink-4";
const FLAG_PILL =
  "absolute rounded-full bg-ink px-[9px] py-[2px] font-mono text-[10px] text-ground";
const RAIL_LINE = "font-mono text-[11px] leading-[1.55] md:text-[11.5px]";

export function CopilotDemo() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  // overlay coordinates only mean anything at 845px — scale the whole stage
  // (header + chart + overlays) as one unit to the measured column width
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setScale(w / STAGE_W);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <section className="border-t border-line-softer px-6 pb-11 pt-[52px] md:px-14 md:pb-[70px] md:pt-[88px] xl:px-[120px]">
      <div className="mx-auto max-w-[1440px]">
        <div className="flex items-baseline justify-between">
          <span className="font-mono text-[10.5px] font-medium tracking-[0.14em] text-trust md:text-[11.5px]">
            <span className="md:hidden">COMING NEXT</span>
            <span className="hidden md:inline">WHERE THIS GOES — COMING SOON</span>
          </span>
          <span className="inline-flex items-center gap-[6px] font-mono text-[10px] text-ink-3 md:gap-[7px] md:text-[11px]">
            <span
              className={clsx(
                "animate-pin-pulse inline-block h-[5px] w-[5px] rounded-full bg-trust md:h-[6px] md:w-[6px]",
                styles.pulseDot,
              )}
            />
            autonomous session<span className="hidden md:inline"> — replayed</span>
          </span>
        </div>

        <h2 className="mb-[10px] mt-3 font-serif text-[27px] font-medium leading-[1.2] md:mb-3 md:mt-[14px] md:max-w-[760px] md:text-[40px] md:leading-[1.15]">
          <span className="hidden md:inline">Coming next: it</span>
          <span className="md:hidden">It</span> takes the trade itself.
        </h2>
        <p className="mb-5 max-w-[730px] text-[13.5px] leading-[1.6] text-ink-2 md:mb-9 md:text-[15px] md:leading-[1.65]">
          Enters, manages, exits — and narrates every call
          <span className="hidden md:inline"> as it makes it</span>. Still refuses what the
          evidence can't support.
        </p>

        <div className="grid overflow-hidden rounded-[14px] border border-line bg-panel-chart md:grid-cols-[1fr_330px]">
          {/* desktop chart column — fixed 845×324 stage scaled as one unit */}
          <div className="relative hidden md:block">
            <div ref={stageRef} className="relative aspect-[845/324] w-full overflow-hidden">
              <div
                className="absolute left-0 top-0 h-[324px] w-[845px] origin-top-left"
                style={{ transform: `scale(${scale})` }}
              >
                <div className="flex h-6 items-start justify-between px-[18px] pt-3 font-mono text-[10px] tracking-[0.08em] text-ink-4">
                  <span>SPY · 5m · demo feed</span>
                  <span>fills from the stored run</span>
                </div>
                <svg viewBox="0 0 845 300" width={845} height={300} className="block">
                  <line x1={0} y1={75} x2={845} y2={75} stroke="var(--grid)" strokeWidth={1} />
                  <line x1={0} y1={150} x2={845} y2={150} stroke="var(--grid)" strokeWidth={1} />
                  <line x1={0} y1={225} x2={845} y2={225} stroke="var(--grid)" strokeWidth={1} />
                  <CandleMarks />
                  <circle cx={142.5} cy={172} r={4.5} fill="var(--ac)" className={styles.m1} />
                  <circle
                    cx={142.5}
                    cy={172}
                    r={9}
                    fill="none"
                    stroke="var(--acb)"
                    strokeWidth={1.2}
                    className={styles.m1}
                  />
                  <circle cx={412.5} cy={126} r={4.5} fill="var(--ac)" className={styles.m2} />
                  <circle
                    cx={412.5}
                    cy={126}
                    r={9}
                    fill="none"
                    stroke="var(--acb)"
                    strokeWidth={1.2}
                    className={styles.m2}
                  />
                </svg>

                <div className={clsx("absolute left-0 top-0", styles.cursor)} aria-hidden>
                  <svg width={15} height={15} viewBox="0 0 15 15" className="block">
                    <path d="M2 1.5 L12.5 7.5 L7.5 8.5 L5.5 13.5 Z" fill="var(--ac)" />
                  </svg>
                  <span className="ml-3 rounded-full bg-trust px-[9px] py-[2px] font-mono text-[10px] text-on-accent">
                    copilot
                  </span>
                </div>

                <div className={clsx(FLAG_PILL, "left-[92px] top-[192px]", styles.m1)}>
                  SOLD 1× 512P @ 2.31
                </div>
                <div className={clsx(FLAG_PILL, "left-[360px] top-[144px]", styles.m2)}>
                  BOUGHT BACK @ 1.12
                </div>

                <div className={clsx(SIG_CARD, "left-[56px] top-[62px]", styles.sig1)}>
                  <div className={clsx(SIG_KICKER, "mb-[3px] text-trust")}>
                    ENTER — SELL THE 30Δ PUT
                  </div>
                  <div className={SIG_BODY}>Premium is rich. Case built, order placed — on its own.</div>
                  <div className={SIG_META}>credit $2.31 · Mar 4</div>
                </div>
                <div className={clsx(SIG_CARD, "left-[262px] top-[16px]", styles.sig2)}>
                  <div className={clsx(SIG_KICKER, "mb-[3px] text-trust")}>CLOSE — TARGET HIT</div>
                  <div className={SIG_BODY}>50% profit. Bought back; the edge is spent.</div>
                  <div className={SIG_META}>
                    <span className="text-pl-pos">+$115</span> · held 14d
                  </div>
                </div>
                <div className={clsx(SIG_CARD, "left-[475px] top-[128px]", styles.sig3)}>
                  <div className={clsx(SIG_KICKER, "text-warn")}>STAND ASIDE</div>
                  <div className={clsx(SIG_BODY, "mt-[3px]")}>
                    Spread is 31% of mid — a fill here would be fantasy.
                  </div>
                  <div className={SIG_META}>skipped · Apr 8</div>
                </div>
              </div>
            </div>
          </div>

          {/* mobile chart — same candles, no overlays/gridlines, solid dots */}
          <div className="md:hidden">
            <div className="flex justify-between px-[14px] pt-[10px] font-mono text-[9.5px] tracking-[0.08em] text-ink-4">
              <span>SPY · 5m · demo feed</span>
              <span>fills from the stored run</span>
            </div>
            <svg viewBox="0 0 845 300" className="block h-auto w-full">
              <CandleMarks />
              <circle cx={142.5} cy={172} r={5} fill="var(--ac)" className={styles.m1} />
              <circle cx={412.5} cy={126} r={5} fill="var(--ac)" className={styles.m2} />
            </svg>
          </div>

          <div className="flex flex-col gap-2 border-t border-line-softer bg-panel-deep px-[14px] py-3 md:gap-[9px] md:border-l md:border-t-0 md:px-[18px] md:py-[14px]">
            <div className="flex items-center gap-[6px] font-mono text-[9.5px] tracking-[0.12em] text-ink-4 md:mb-[2px] md:gap-[7px] md:text-[10px]">
              <span
                className={clsx(
                  "animate-pin-pulse inline-block h-1 w-1 rounded-full bg-trust md:h-[5px] md:w-[5px]",
                  styles.pulseDot,
                )}
              />
              REASONING — LIVE
            </div>
            <div className={clsx(RAIL_LINE, "text-ink-3", styles.l1)}>
              Mar 4 · scanning the tape — hunting rich premium at 30Δ
            </div>
            <div className={clsx(RAIL_LINE, "text-ink", styles.l2)}>
              Mar 4 · SOLD 1× SPY 512P @ 2.31 (bid 2.29 / ask 2.35)
            </div>
            <div className={clsx(RAIL_LINE, "text-ink-3", styles.l3)}>
              why · target 50% · time exit 21 DTE · theta does the work
            </div>
            <div className={clsx(RAIL_LINE, "text-ink", styles.l4)}>
              Mar 18 · BOUGHT BACK @ 1.12 — target hit ·{" "}
              <span className="text-pl-pos">+$115</span> · held 14d
            </div>
            <div className={clsx(RAIL_LINE, "text-warn", styles.l5)}>
              Apr 8 · <span className="hidden md:inline">next </span>entry looks juicy —{" "}
              <span className="hidden md:inline">but the </span>spread is 31% of mid. standing
              aside.
            </div>
            <div className={clsx(RAIL_LINE, "text-ink-2", styles.l6)}>
              <span className="hidden md:inline">edge spent. </span>every call logged + replayable
              — no human touched this trade.
            </div>
          </div>
        </div>

        <div className="mt-[14px] hidden justify-between font-mono text-[11px] text-ink-4 md:flex">
          <span>it entered, managed, and exited on its own — reasoning logged, replayable</span>
          <span>no dates, no waitlist theater</span>
        </div>
      </div>
    </section>
  );
}
