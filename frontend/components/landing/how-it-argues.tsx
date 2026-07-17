"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";

import {
  INTERVIEW_QUESTIONS,
  bumpInterviewQuestion,
  peekInterviewQuestion,
} from "@/lib/interview-questions";

/**
 * Landing §2 "How it argues" — three panel cards (design 2a lines 107–144,
 * 2b lines 417–450; copy verbatim from copy-deck §2). Card 02 is a live
 * clone of the design-system QuestionCard (answer text is never read — the
 * demo compiles the same fixture spec either way, exactly like the design's
 * page logic). Quotes inside cards are body text, never serif. Trust hue
 * only — no P/L tokens on this surface.
 */

// the gauntlet stages + the verdict, stepped through like the real app's
// progress. mobileSuffix folds the essential note into the label below md.
const ATTACK_ROWS: { label: string; note: string; mobileSuffix?: string; verdict?: boolean }[] = [
  { label: "Backtest", note: "real bid/ask, never mid", mobileSuffix: " — real bid/ask, never mid" },
  { label: "Test on data it never saw", note: "last 30% kept hidden" },
  { label: "Test each time period", note: "rolling ~2-month windows" },
  { label: "Reshuffle the trades 1,000×", note: "how much was luck?" },
  { label: "Nudge the settings", note: "does it survive small changes?", mobileSuffix: " ±20%" },
  { label: "The honest verdict", note: "grounded in the numbers above", verdict: true },
];
const STEP_MS = 900;

export function HowItArgues() {
  const [answered, setAnswered] = useState(false);
  const [draft, setDraft] = useState("");
  // a different real-style clarifying question each visit. SSR + first
  // client render show the canonical one so hydration matches; the mount
  // effect swaps in this visit's question and advances for next reload.
  const [interview, setInterview] = useState(INTERVIEW_QUESTIONS[8]);
  useEffect(() => {
    setInterview(peekInterviewQuestion());
    bumpInterviewQuestion();
  }, []);

  // step = completed stages (0..6); the active stage is `step`. It walks
  // the gauntlet and loops, so the card reads as a live run, not a static
  // checklist (owner 2026-07-17). Reduced motion pins it fully complete.
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStep(ATTACK_ROWS.length);
      return;
    }
    const id = window.setInterval(
      () => setStep((s) => (s >= ATTACK_ROWS.length ? 0 : s + 1)),
      STEP_MS,
    );
    return () => window.clearInterval(id);
  }, []);
  const pct = Math.round((step / ATTACK_ROWS.length) * 100);

  const answer = () => setAnswered(true);
  const reset = () => {
    setAnswered(false);
    setDraft("");
  };

  return (
    <section
      id="how-it-argues"
      className="border-t border-line-softer px-6 pb-[44px] pt-[52px] md:px-14 md:pb-16 md:pt-[84px] xl:px-[120px]"
    >
      <div className="mx-auto max-w-[1440px]">
        <div className="font-mono text-[10.5px] font-medium tracking-[.14em] text-trust md:text-[11.5px]">
          HOW IT ARGUES
        </div>
        <h2 className="mt-3 max-w-[760px] font-serif text-[27px] font-medium leading-[1.2] text-ink md:mt-[14px] md:text-[40px] md:leading-[1.15]">
          Three moves. No guesses.
        </h2>

        <div className="mt-[22px] grid items-stretch gap-[14px] md:mt-[38px] md:grid-cols-3 md:gap-[22px]">
          {/* 01 — YOU PITCH */}
          <div className="flex flex-col gap-3 rounded-[14px] border border-line bg-panel p-4 md:gap-[14px] md:p-5">
            <div className="flex justify-between font-mono text-[10.5px] tracking-[.12em] text-ink-3 md:text-[11px]">
              <span>01 — YOU PITCH</span>
              <span className="hidden text-ink-5 md:inline">english, or a chart</span>
            </div>
            <div className="overflow-hidden rounded-lg border border-line-soft">
              {/* the still is a LIGHT capture — it follows the page: shown
                  as-is on a light page, inverted (hue preserved) on a dark
                  one. Driven off the painted <html data-theme>, NOT a React
                  prop, so it can never mismatch the actual background. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/landing/chart-teach-v2.png"
                alt="pin winning examples on the SPY chart"
                className="block w-full [[data-theme=dark]_&]:[filter:invert(1)_hue-rotate(180deg)]"
              />
            </div>
            {/* "1993" = SPY inception per copy-deck §2 (flagged VERIFY there) */}
            <div className="text-[13px] leading-[1.55] text-ink-2 md:text-[13.5px] md:leading-[1.6]">
              {'"You showed me winners — that\'s what eyes do. I\'ll test every look-alike since 1993, losers included."'}
            </div>
          </div>

          {/* 02 — IT INTERVIEWS */}
          <div className="flex flex-col gap-3 rounded-[14px] border border-line bg-panel p-4 md:gap-[14px] md:p-5">
            <div className="flex justify-between font-mono text-[10.5px] tracking-[.12em] text-ink-3 md:text-[11px]">
              <span>02 — IT INTERVIEWS</span>
              <span className="hidden text-ink-5 md:inline">ambiguity earns a question</span>
            </div>
            <div className="text-[13px] leading-[1.55] text-ink-2 md:text-[13.5px] md:leading-[1.6]">
              {'"Here\'s what I heard — every dial is adjustable."'}
            </div>

            {answered ? (
              <div className="flex flex-col gap-2 rounded-[14px] border border-dashed border-trust-border bg-trust-dim px-4 py-[14px] motion-safe:animate-fade-rise md:px-5 md:py-4">
                <div className="font-mono text-[12.5px] text-trust">
                  ✓ spec compiled — 10 dials, every one adjustable
                </div>
                <div className="font-mono text-[11.5px] text-ink-3">
                  window: last 1 year · 220 sessions · ~3s
                </div>
                <button
                  type="button"
                  onClick={reset}
                  className="self-start font-mono text-[11px] text-ink-4 hover:text-ink-3"
                >
                  ↺ ask me again
                </button>
              </div>
            ) : (
              <div className="rounded-[14px] border border-trust-border bg-trust-dim px-4 py-[14px] md:px-5 md:py-4">
                <div className="mb-1 font-mono text-[10px] font-medium tracking-[.12em] text-trust md:text-[10.5px]">
                  {"QUESTION 1 OF 1 — I DON'T GUESS"}
                </div>
                <div className="mb-3 text-[14.5px] font-semibold leading-[1.4] text-ink md:mb-[14px] md:text-[16.5px] md:leading-[1.375]">
                  {interview.q}
                </div>
                <div className="flex flex-wrap items-center gap-1.5 md:gap-2">
                  {interview.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      onClick={answer}
                      className="rounded-full border border-trust-border px-[13px] py-[7px] text-[12.5px] text-trust hover:bg-trust-dim md:px-[14px] md:py-1.5 md:text-[13px]"
                    >
                      {option}
                    </button>
                  ))}
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && draft.trim()) {
                        e.preventDefault();
                        answer();
                      }
                    }}
                    placeholder="or answer in your own words ↵"
                    aria-label="Answer in your own words"
                    className="min-w-0 flex-1 rounded-[9px] border border-line bg-panel-deep px-3 py-2 font-mono text-[13px] text-ink placeholder:text-ink-4"
                  />
                  <button
                    type="button"
                    disabled={!draft.trim()}
                    onClick={answer}
                    className={clsx(
                      "rounded-[9px] px-4 py-2 text-[13px] font-semibold",
                      draft.trim()
                        ? "bg-trust text-on-accent"
                        : "cursor-not-allowed bg-raised-2 text-ink-4",
                    )}
                  >
                    answer
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 03 — IT ATTACKS ITSELF */}
          <div className="flex flex-col gap-[10px] rounded-[14px] border border-line bg-panel p-4 md:gap-[13px] md:p-5">
            <div className="flex justify-between font-mono text-[10.5px] tracking-[.12em] text-ink-3 md:text-[11px]">
              <span>03 — IT ATTACKS ITSELF</span>
              <span className="hidden text-ink-5 md:inline">then the verdict</span>
            </div>
            <div className="flex flex-col gap-[9px] font-mono text-[12px] md:gap-[10px] md:text-[13px]">
              {ATTACK_ROWS.map((row, i) => {
                const done = i < step;
                const active = i === step;
                return (
                  <div
                    key={row.label}
                    className={clsx(
                      "flex items-baseline gap-[9px] transition-[color,opacity] duration-300 md:gap-[10px]",
                      active
                        ? "text-ink"
                        : done
                          ? row.verdict
                            ? "text-ink"
                            : "text-ink-3"
                          : "text-ink-5 opacity-60",
                    )}
                  >
                    <span className="flex w-[14px] shrink-0 justify-center md:w-4">
                      {active ? (
                        <span className="inline-block h-[7px] w-[7px] animate-pin-pulse rounded-full bg-trust motion-reduce:animate-none" />
                      ) : done ? (
                        <span className="text-trust">{row.verdict ? "▶" : "✓"}</span>
                      ) : (
                        <span>{row.verdict ? "▶" : "○"}</span>
                      )}
                    </span>
                    <span className="flex-1">
                      {row.label}
                      {row.mobileSuffix && <span className="md:hidden">{row.mobileSuffix}</span>}
                    </span>
                    <span
                      className={clsx(
                        "hidden text-[10.5px] md:inline",
                        active ? "text-trust" : "text-ink-5",
                      )}
                    >
                      {row.note}
                    </span>
                  </div>
                );
              })}
            </div>
            {/* progress bar walks the gauntlet with the step; trust hue, not P/L */}
            <div className="mt-[2px] hidden h-[6px] overflow-hidden rounded-[3px] bg-line-softer md:block">
              <div
                className="h-full rounded-[3px] bg-trust transition-[width] duration-300 ease-out"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="hidden font-mono text-[11px] text-ink-4 md:block">
              live numbers stream while it runs — never a loading bar
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
