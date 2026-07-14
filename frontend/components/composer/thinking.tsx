"use client";

/**
 * The compile-time thinking state (owner ask 2026-07-14): submitting a
 * strategy used to show only a dull pulsing dot for the parser's 10–30s
 * round-trip. This is the Claude-chat treatment instead — the prompt
 * becomes a message and a shimmering status line narrates the stages the
 * parser actually goes through, with honest elapsed time.
 */

import { useEffect, useState } from "react";

/** Statuses advance with elapsed time and never loop back — each one is
 * TRUE of the parse in flight (read → disambiguate → compile → validate);
 * nothing here claims progress the backend can't confirm. */
const STATUSES: { at: number; text: string }[] = [
  { at: 0, text: "Reading your strategy…" },
  { at: 3, text: "Marking what you stated — entry, exit, sizing…" },
  { at: 7, text: "Hunting for ambiguity. I don't guess…" },
  { at: 12, text: "Compiling the spec…" },
  { at: 18, text: "Validating every field against the schema…" },
  { at: 26, text: "Double-checking — no field gets a silent default…" },
  { at: 38, text: "Still working. A slow answer beats a wrong one…" },
];

export function ThinkingIndicator() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(
      () => setElapsed(Math.floor((Date.now() - t0) / 1000)),
      500,
    );
    return () => clearInterval(id);
  }, []);
  const status = [...STATUSES].reverse().find((s) => elapsed >= s.at) ?? STATUSES[0];

  return (
    <div className="animate-fade-rise rounded-[14px] border border-line bg-panel px-5 py-4">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-[3px]" aria-hidden>
          <span className="h-[5px] w-[5px] animate-pin-pulse rounded-full bg-trust" />
          <span
            className="h-[5px] w-[5px] animate-pin-pulse rounded-full bg-trust"
            style={{ animationDelay: "0.35s" }}
          />
          <span
            className="h-[5px] w-[5px] animate-pin-pulse rounded-full bg-trust"
            style={{ animationDelay: "0.7s" }}
          />
        </span>
        {/* key remounts the wrapper per status so fade-rise replays on
            advance; the shimmer lives on the inner span — both animate,
            neither clobbers the other's `animation` shorthand */}
        <span key={status.text} className="animate-fade-rise">
          <span className="thinking-shimmer text-[15px] font-medium">{status.text}</span>
        </span>
        <span className="ml-auto font-mono text-[12px] tabular-nums text-ink-4">{elapsed}s</span>
      </div>
      <p className="mt-2 pl-[30px] text-[12.5px] leading-[1.55] text-ink-4">
        If anything is ambiguous, you&apos;ll get a question, not a guess.
      </p>
    </div>
  );
}
