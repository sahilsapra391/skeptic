"use client";

/**
 * Run-in-progress state: the gauntlet attacking the strategy stage by
 * stage. As each stage finishes, its REAL headline stat appears in the
 * live feed — computed numbers only, never a fabrication. While the
 * engine crunches, rotating tips teach the platform.
 */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { useSettings } from "@/lib/settings";
import { GAUNTLET_STAGES } from "@/lib/types";

const RETAIL_STAGES: { t: string; n: string }[] = [
  { t: "Backtest", n: "fills priced at real bid/ask, never mid" },
  { t: "Test on data it never saw", n: "last 30% of history kept hidden" },
  { t: "Test each time period", n: "rolling ~2-month windows" },
  { t: "Reshuffle the trades 1,000×", n: "how much was luck?" },
  { t: "Nudge the settings ±20%", n: "does it survive small changes?" },
  { t: "The honest verdict", n: "grounded in the numbers above" },
];

const TIPS = [
  "One stated exit rule is enough — the parser never invents the ones you didn't give.",
  "Chart mode: pin up to 10 examples. More examples make the test stricter, not easier.",
  "Below 15 finished trades the verdict is withheld — good-looking numbers don't override it.",
  "Every re-run of the same strategy family counts as a trial — the deflated Sharpe gets harder to impress each time.",
  "On the results screen, ask questions — answers use only this run's computed stats.",
  "Commission and slippage are editable in Settings and apply to every new run.",
  "In the sensitivity grid, the ringed column is your spec — brighter neighbors did better.",
  "The verdict leads with the most uncomfortable finding on purpose. That's the product.",
];

export function GauntletProgress({
  stage,
  name,
  previews = [],
}: {
  stage: number;
  name: string;
  previews?: string[];
}) {
  const settings = useSettings();
  const stages = settings.verbiage === "retail" ? RETAIL_STAGES : GAUNTLET_STAGES;
  const [tipIndex, setTipIndex] = useState(() => Math.floor(Math.random() * TIPS.length));

  useEffect(() => {
    const id = setInterval(() => setTipIndex((i) => (i + 1) % TIPS.length), 6000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="mx-auto mt-[7vh] max-w-[720px]">
      <h2 className="mb-1.5 text-[30px] font-[650]">Attacking your strategy…</h2>
      <p className="mb-[26px] text-[15px] text-ink-3">{name}</p>
      <div className="mb-6 h-[6px] overflow-hidden rounded-[3px] bg-line-softer">
        <div
          className="h-full rounded-[3px] bg-trust transition-[width] duration-500"
          style={{ width: `${Math.min(100, (stage / stages.length) * 100)}%` }}
        />
      </div>
      <div className="flex flex-col gap-[13px]">
        {stages.map((st, i) => (
          <div
            key={st.t}
            className={clsx(
              "flex items-baseline gap-3 font-mono text-[14.5px]",
              i < stage ? "text-ink-3" : i === stage ? "text-ink" : "text-ink-5",
            )}
          >
            <span className="inline-block w-[20px]">
              {i < stage ? "✓" : i === stage ? "▶" : "○"}
            </span>
            <span className="flex-1">{st.t}</span>
            <span className="text-[12px] text-ink-4">{st.n}</span>
          </div>
        ))}
      </div>

      {previews.length > 0 && (
        <div className="mt-7 rounded-[14px] border border-trust-border bg-trust-dim px-5 py-4">
          <div className="mb-2.5 font-mono text-[11px] font-medium tracking-[.14em] text-trust">
            LIVE FROM THE GAUNTLET — REAL NUMBERS, NOT A LOADING BAR
          </div>
          <div className="flex flex-col gap-1.5">
            {previews.map((p, i) => (
              <div
                key={i}
                className={clsx(
                  "font-mono text-[13.5px] leading-[1.55]",
                  i === previews.length - 1 ? "text-ink" : "text-ink-3",
                )}
              >
                {p}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 rounded-[14px] border border-line bg-panel px-5 py-4">
        <div className="mb-1.5 font-mono text-[11px] font-medium tracking-[.14em] text-ink-4">
          WHILE YOU WAIT
        </div>
        <p className="text-[14.5px] leading-[1.6] text-ink-2">{TIPS[tipIndex]}</p>
      </div>
    </div>
  );
}
