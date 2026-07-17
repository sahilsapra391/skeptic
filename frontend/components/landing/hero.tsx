"use client";

/**
 * B1 — landing topbar + hero (design 2a desktop / 2b mobile).
 *
 * LandingTopbar and LandingHero are TOP-LEVEL siblings on the page: the
 * sticky nav and the sticky morphing-wordmark band stick against the
 * window, so they must never be caged inside the hero section's box. The
 * assembly calls useWordmarkDraw() ONCE and passes `draw` to both (the
 * draw-once sessionStorage flag is consumed at module scope — a second
 * hook call would still share it, but a single owner keeps replay in sync).
 *
 * The composer is a visual clone of the app's (app/(app)/new/page.tsx) —
 * same classes, same speech wiring, same auto-grow — but it never parses:
 * submit hands off to /new?pitch=… so the clarify/spec machinery,
 * provenance transcript and cancel path all live in one place (guardrail:
 * no silent parser guesses duplicated here).
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";

import { useSpeechToText } from "@/lib/use-speech";

import { LandingWordmark, useWordmarkDraw } from "./landing-wordmark";
import type { LandingTheme } from "./use-landing-theme";
import styles from "./hero.module.css";

export type WordmarkDraw = ReturnType<typeof useWordmarkDraw>;

// copy-deck Hero: rotate 3.6s, pause on focus/typing
const PLACEHOLDERS = [
  "sell a 30-delta put on SPY every week, close at 50% profit or 21 days…",
  "iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop…",
  "buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry…",
  "covered call on SPY, sell the 30-delta monthly, roll at 21 DTE…",
];

// the app's PRESETS entries for the three landing chips (labels/structure
// sublabels/phrases verbatim — click fills the composer, app parity)
const PRESETS = [
  {
    label: "Weekly income put",
    sub: "short put",
    phrase: "sell a 30-delta put on SPY every week, close at 50% profit or 21 days",
  },
  {
    label: "Calm-market condor",
    sub: "iron condor",
    phrase: "iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop",
  },
  {
    label: "Crash-insurance put",
    sub: "long put",
    phrase: "buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry",
  },
];

// scroll-morph constants (component-specs §2): scrub window 0→260px,
// translate(tx·q, -28px·q) scale(1 − 0.8q), q = smoothstep
const MORPH_SCROLL_PX = 260;
const MORPH_TY = -28;
const MORPH_SCALE = 0.8;
const MORPH_TX_FALLBACK = -630; // 1440 shell constant until measured

export function LandingTopbar({ theme, draw }: { theme: LandingTheme; draw: WordmarkDraw }) {
  const navRef = useRef<HTMLElement | null>(null);
  const borderRef = useRef<HTMLDivElement | null>(null);
  const slotRef = useRef<HTMLDivElement | null>(null);
  const logoRef = useRef<HTMLDivElement | null>(null);
  const txRef = useRef(MORPH_TX_FALLBACK);

  useEffect(() => {
    let raf = 0;
    // tx is responsive: measure hero-logo center → nav-left slot center at
    // mount + resize. Both are horizontal-only and scroll-independent, but
    // getBoundingClientRect includes transforms — strip the scrub transform
    // for the measurement (style writes settle before the next paint).
    const measure = () => {
      const slot = slotRef.current;
      const logo = logoRef.current;
      if (!slot || !logo) return;
      const prev = logo.style.transform;
      logo.style.transform = "none";
      const lr = logo.getBoundingClientRect();
      logo.style.transform = prev;
      const sr = slot.getBoundingClientRect();
      // hidden below md → zero rects → keep the last/fallback value
      if (lr.width > 0 && sr.width > 0) {
        txRef.current = sr.left + sr.width / 2 - (lr.left + lr.width / 2);
      }
    };
    const apply = () => {
      raf = 0;
      const p = Math.min(Math.max(window.scrollY / MORPH_SCROLL_PX, 0), 1);
      const q = p * p * (3 - 2 * p);
      // transform-only, on the WRAPPER — the svg itself owns the draw CSS
      if (logoRef.current) {
        logoRef.current.style.transform =
          q === 0
            ? ""
            : `translate(${txRef.current * q}px, ${MORPH_TY * q}px) scale(${1 - MORPH_SCALE * q})`;
      }
      if (borderRef.current) borderRef.current.style.opacity = q.toFixed(3);
      if (navRef.current) {
        navRef.current.style.backgroundColor = q > 0.05 ? "var(--ground)" : "transparent";
      }
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(apply);
    };
    const onResize = () => {
      measure();
      schedule();
    };
    measure();
    apply();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", onResize);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <>
      {/* mobile topbar (2b): static s-mark + Sign in, no morph below md */}
      <nav className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line-softer bg-ground px-[18px] md:hidden">
        {/* eslint-disable-next-line @next/next/no-img-element -- tiny static brand asset, app idiom (boot-splash) */}
        <img
          src={theme.resolved === "light" ? "/brand/s-mark-black.svg" : "/brand/s-mark-white.svg"}
          alt="Skeptic"
          className="block h-[22px] w-auto"
        />
        <Link href="/signin" className="px-1 py-2.5 text-[13px] font-semibold text-ink-2">
          Sign in
        </Link>
      </nav>

      {/* desktop nav (2a): right-aligned auth affordances; border + bg fade in with scroll q */}
      <nav
        ref={navRef}
        className="sticky top-0 z-30 hidden h-16 items-center justify-end gap-[18px] px-11 transition-colors duration-200 md:flex"
      >
        {/* empty left slot the wordmark morphs into — measured, never styled */}
        <div ref={slotRef} aria-hidden className="mr-auto h-9 w-[92px]" />
        <Link href="/signin" className="py-[2px] text-[12.5px] text-ink-4 hover:text-ink-3">
          Sign in
        </Link>
        <Link
          href="/signup"
          className="rounded-[10px] border border-line bg-raised-2 px-4 py-2 text-[13px] font-semibold text-ink-2 hover:border-trust-border hover:bg-raised-3 hover:text-ink"
        >
          Get 5 free backtests
        </Link>
        <div
          ref={borderRef}
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-line"
          style={{ opacity: 0 }}
        />
      </nav>

      {/* sticky wordmark band (2a): pins at top:10px and scrubs into the nav slot */}
      <div className="pointer-events-none sticky top-[10px] z-40 mt-[119px] hidden h-[104px] items-center justify-center md:flex">
        <div ref={logoRef} className="pointer-events-auto will-change-transform">
          <LandingWordmark run={draw.run} onReplay={draw.replay} className="block h-auto w-[460px]" />
        </div>
      </div>
    </>
  );
}

export function LandingHero({ draw }: { theme: LandingTheme; draw: WordmarkDraw }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [focused, setFocused] = useState(false);
  const [phIndex, setPhIndex] = useState(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const prefetchedRef = useRef(false);

  const speech = useSpeechToText((segment) => {
    // segments arrive already polished — join verbatim, no sentence-casing
    setText((t) => {
      const sep = t && !/\s$/.test(t) ? " " : "";
      return t + sep + segment;
    });
  });

  const composerValue = speech.interim
    ? `${text}${text && !text.endsWith(" ") ? " " : ""}${speech.interim}`
    : text;

  // the chatbox grows a line at a time with its content (capped, then
  // scrolls). Empty clears the inline height instead of measuring: the
  // mount-time effect can run before styles settle and freeze a bogus
  // 200px scrollHeight into an empty box (seen live at first paint)
  useEffect(() => {
    const ta = composerRef.current;
    if (!ta) return;
    if (!composerValue) {
      ta.style.height = "";
      ta.style.overflowY = "hidden";
      return;
    }
    ta.style.height = "auto";
    const capped = Math.min(ta.scrollHeight, 200);
    ta.style.height = `${capped}px`;
    ta.style.overflowY = ta.scrollHeight > 200 ? "auto" : "hidden";
  }, [composerValue]);

  // rotating placeholder pauses while focused or non-empty
  const rotationPaused = focused || composerValue !== "";
  useEffect(() => {
    if (rotationPaused) return;
    const id = window.setInterval(
      () => setPhIndex((i) => (i + 1) % PLACEHOLDERS.length),
      3_600,
    );
    return () => window.clearInterval(id);
  }, [rotationPaused]);

  // "the app hydrates on first interaction" — warm /new on first focus/keystroke
  const warm = () => {
    if (prefetchedRef.current) return;
    prefetchedRef.current = true;
    router.prefetch("/new");
  };

  // no anon-trial row yet (Turnstile/queue is a later backend chunk):
  // submit hands off to the real run flow immediately
  const submit = () => {
    const pitch = text.trim();
    if (!pitch || submitting) return;
    if (speech.listening) speech.stop();
    setSubmitting(true);
    router.push(`/new?pitch=${encodeURIComponent(pitch)}`);
  };

  const scrollToArgue = () => {
    const el = document.getElementById("how-it-argues");
    if (!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  };

  // first-visit entrance stagger, timed off the wordmark draw (~1.59s);
  // repeat visits render everything instantly
  const stagger = draw.firstVisit ? clsx("animate-fade-rise", styles.motionSafe) : undefined;
  const delayH1 = draw.firstVisit ? { animationDelay: "1.55s" } : undefined;
  const delayRest = draw.firstVisit ? { animationDelay: "1.85s" } : undefined;

  return (
    <section className="px-6 md:px-14 xl:px-[120px]">
      <div className="relative mx-auto flex min-h-[calc(100svh-56px)] w-full max-w-[1440px] flex-col items-center justify-center pb-14 md:min-h-[calc(92vh-287px)] md:justify-start md:pb-16 md:pt-[6px]">
        {/* mobile hero mark — static (no morph); desktop mark lives in the topbar band */}
        <LandingWordmark run={draw.run} onReplay={draw.replay} className="block h-auto w-[280px] md:hidden" />

        <h1
          style={delayH1}
          className={clsx(
            "mb-[10px] mt-[22px] text-center font-serif text-[29px] font-medium leading-[1.16] tracking-[-.01em] md:mb-[14px] md:mt-[18px] md:text-[clamp(32px,3.6vw,44px)] md:leading-[1.12]",
            stagger,
          )}
        >
          Pitch me a trade. I&apos;ll play the skeptic.
        </h1>
        <p
          style={delayRest}
          className={clsx(
            "mx-auto mb-[22px] max-w-[620px] text-center text-[13.5px] leading-[1.6] text-ink-3 md:mb-0 md:text-[15px] md:leading-[1.65]",
            stagger,
          )}
        >
          Describe an options strategy in plain English. Skeptic interviews you and runs it on
          real intraday options data.
        </p>

        {/* pricing's "Run your free backtest" CTA anchors here */}
        <div
          id="composer"
          style={delayRest}
          className={clsx("w-full scroll-mt-24 md:mt-8 md:w-[690px] md:max-w-[90%]", stagger)}
        >
          <div className="rounded-[22px] border border-line-soft bg-panel py-2.5 pl-6 pr-3 shadow-[var(--shadow-soft)] focus-within:border-line-hover">
            <div className="flex items-center gap-3">
              <textarea
                ref={composerRef}
                rows={1}
                value={composerValue}
                placeholder={PLACEHOLDERS[phIndex]}
                aria-label="Describe an options strategy"
                className="w-full flex-1 text-[16.5px] leading-[1.65] text-ink placeholder:text-ink-4"
                onChange={(e) => {
                  if (speech.listening) speech.stop();
                  warm();
                  setText(e.target.value);
                }}
                onFocus={() => {
                  warm();
                  setFocused(true);
                }}
                onBlur={() => setFocused(false)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
              <div className="flex items-center gap-1.5">
                {speech.supported && (
                  <button
                    type="button"
                    onClick={() => (speech.listening ? speech.stop() : speech.start())}
                    title={speech.listening ? "Stop dictation" : "Dictate your strategy"}
                    className={clsx(
                      "flex h-10 w-10 items-center justify-center rounded-full",
                      speech.listening
                        ? "bg-trust-dim text-trust"
                        : "text-ink-4 hover:bg-raised-2 hover:text-ink",
                    )}
                  >
                    {speech.listening ? (
                      <span
                        className={clsx(
                          "inline-block h-[9px] w-[9px] animate-pin-pulse rounded-full bg-trust",
                          styles.motionSafe,
                        )}
                      />
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                        <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" />
                        <path d="M3 7.5a5 5 0 0 0 10 0" />
                        <line x1="8" y1="12.5" x2="8" y2="14.5" />
                      </svg>
                    )}
                  </button>
                )}
                <button
                  type="button"
                  onClick={submit}
                  disabled={!text.trim() || submitting}
                  aria-label="Compile the strategy"
                  title={submitting ? "Compiling…" : "Compile ↵"}
                  className={clsx(
                    "flex h-10 w-10 items-center justify-center rounded-full transition-colors",
                    text.trim() && !submitting
                      ? "bg-ink text-ground hover:bg-ink-2"
                      : "cursor-not-allowed bg-raised-2 text-ink-4",
                  )}
                >
                  {submitting ? (
                    <span
                      className={clsx(
                        "inline-block h-[9px] w-[9px] animate-pin-pulse rounded-full bg-current",
                        styles.motionSafe,
                      )}
                    />
                  ) : (
                    <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2.5 8h11" />
                      <path d="M9 3.5L13.5 8 9 12.5" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
            {(speech.listening || speech.error) && (
              <div className={clsx("pb-1 pt-0.5 text-[12.5px]", speech.error ? "text-warn" : "text-ink-4")}>
                {speech.error ?? "Listening — tap the mic again to stop."}
              </div>
            )}
          </div>
        </div>

        {/* fixed 52px box on desktop: layout parity with the future submitted-anon row */}
        <div style={delayRest} className={clsx("md:flex md:h-[52px] md:items-center", stagger)}>
          <div className="mt-3 font-mono text-[11px] text-ink-4 md:mt-0 md:text-[11.5px]">
            your first backtest is free — no account, no card
          </div>
        </div>

        {/* mobile (2b) trims to the first preset + the chart chip */}
        <div
          style={delayRest}
          className={clsx(
            "mt-5 flex w-full gap-2 md:mt-0 md:w-auto md:max-w-[820px] md:flex-wrap md:justify-center",
            stagger,
          )}
        >
          {PRESETS.map((p, i) => (
            <button
              key={p.label}
              type="button"
              onClick={() => {
                warm();
                setText(p.phrase);
              }}
              title={p.phrase}
              className={clsx(
                "group min-h-[44px] rounded-[14px] border border-line-soft bg-panel px-4 py-2.5 text-left hover:border-line-hover hover:bg-raised",
                i === 0 ? "flex-1 md:flex-none" : "hidden md:block",
              )}
            >
              <div className="text-[13.5px] font-medium text-ink-2 group-hover:text-ink md:text-[14px]">
                {p.label}
              </div>
              <div className="mt-[2px] font-mono text-[10.5px] tracking-[.04em] text-ink-4 group-hover:text-ink-3 md:text-[11px]">
                {p.sub}
              </div>
            </button>
          ))}
          <Link
            href="/new?mode=chart"
            title="chart-teach mode"
            className="min-h-[44px] flex-1 rounded-[14px] border border-dashed border-line-hover px-4 py-2.5 text-left text-ink-3 md:flex-none"
          >
            <div className="text-[13.5px] font-medium md:text-[14px]">
              <span className="md:hidden">or pin it on a chart</span>
              <span className="hidden md:inline">or pin examples on a chart</span>
            </div>
            <div className="mt-[2px] font-mono text-[10.5px] tracking-[.04em] text-ink-4 md:text-[11px]">
              chart-teach →
            </div>
          </Link>
        </div>

        <p
          style={delayRest}
          className={clsx(
            "mt-[22px] text-center text-[11.5px] text-ink-4 md:mt-[30px] md:text-[12.5px]",
            stagger,
          )}
        >
          Research tool, not financial advice.
        </p>

        <button
          type="button"
          onClick={scrollToArgue}
          style={delayRest}
          className={clsx(
            "absolute inset-x-0 bottom-[10px] py-2.5 text-center font-mono text-[10px] tracking-[.14em] text-ink-4 hover:text-ink-2 md:bottom-[14px] md:text-[10.5px]",
            stagger,
          )}
        >
          HOW IT ARGUES ↓
        </button>
      </div>
    </section>
  );
}
