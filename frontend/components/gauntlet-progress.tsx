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
  { t: "Nudge the settings", n: "does it survive small changes?" },
  { t: "The honest verdict", n: "grounded in the numbers above" },
];

// the "Attacking your strategy…" heading fades into a sibling phrase
// every few seconds — same promise, twenty ways
const HEADINGS = [
  "Attacking your strategy",
  "Stress-testing your idea",
  "Interrogating the edge",
  "Hunting for luck in the results",
  "Trying to break it",
  "Poking holes in the thesis",
  "Cross-examining the numbers",
  "Auditing every fill",
  "Questioning the win rate",
  "Searching for the catch",
  "Testing it against history",
  "Checking if it was skill",
  "Putting the edge on trial",
  "Challenging every assumption",
  "Probing for weak spots",
  "Grilling the strategy",
  "Second-guessing the returns",
  "Shaking the tree",
  "Looking for the flaw",
  "Playing devil's advocate",
];

const TIPS = [
  "One stated exit rule is enough — the parser never invents the ones you didn't give.",
  "Chart mode: pin up to 10 examples. More examples make the test stricter, not easier.",
  "Below your minimum-trades bar (Settings, standard 15) the verdict is withheld — good-looking numbers don't override it.",
  "Every re-run of the same strategy family counts as a trial — the deflated Sharpe gets harder to impress each time.",
  "On the results screen, ask questions — answers use only this run's computed stats.",
  "Commission and slippage come from the spec you confirmed — Settings supply the starting values when a strategy is first compiled, and a variant inherits its parent's.",
  "In the sensitivity grid, the ringed column is your spec — brighter neighbors did better.",
  "The verdict leads with the most uncomfortable finding on purpose. That's the product.",
  "Fills never happen at mid price — buys lean toward the ask, sells toward the bid, plus slippage.",
  "The shaded strip on the equity chart is data the strategy never saw during testing.",
  "Hover the equity curve for the exact date, account value and drawdown at any point.",
  "Hover a walk-forward bar to see that window's dates, return and trade count.",
  "Verbiage Complexity in Settings rewrites everything in plain English — same numbers, zero jargon.",
  "Switch between light and dark mode in Settings → Appearance; the accent color is yours to pick too.",
  "The accent color never touches profit or loss — green and red stay strictly P/L.",
  "A 'plateau' in the nudge test is good: small settings changes don't wreck the result.",
  "A 'cliff' is the warning: the result only works at exactly your settings. That smells like luck.",
  "Recommendations are computed from sweeps we actually ran on your strategy — never opinion.",
  "The trust band is a range, not a score — precision would be dishonest.",
  "Trust level 5 still isn't a promise. It means the strategy survived everything we could throw at it.",
  "If out-of-sample keeps less than half the in-sample result, the verdict flags it as curve-fit.",
  "The Monte Carlo test reshuffles your trade order 1,000 times to measure how much was sequence luck.",
  "Every run is seeded — same strategy, same data, same seed gives the identical result.",
  "The trade log shows fills first; skipped entries hide behind their own toggle, each with a reason.",
  "SKIP reasons matter: zero bids and wide spreads are the market telling you the fill was fantasy.",
  "SPY has options history to 2020 in the lake; QQQ and IWM start much later — coverage bounds every verdict.",
  "The data window is printed on every result — verdicts never pretend to more history than they have.",
  "Speak your strategy: the mic in the composer does dictation.",
  "Presets on the home page reorder by what you actually run most.",
  "The library sorts by trust, not by return. On purpose.",
  "Old runs stay honest: their verdicts were computed on the coverage available at the time.",
  "The parser asks rather than guesses — a vague trigger like 'when it dips' earns you a question.",
  "Answer clarifying questions with the chips or your own words — either works.",
  "You can edit every dial on the spec screen before anything runs — nothing runs unconfirmed.",
  "Custom exits compose: profit target, stop loss and a time exit can all apply at once.",
  "0DTE strategies are refused for now — honest simulation needs minute data we don't serve yet.",
  "The sidebar is drag-resizable — pull the edge, or drop it below 120px to snap to icons.",
  "The deflated Sharpe punishes retries: 100 attempts at one family makes a good number likely luck.",
  "The gauntlet's five attacks run on every single backtest — there's no express lane.",
  "Grounded Q&A refuses to invent: if a number isn't in this run's stats, it says so.",
  "Verdict text is validated number-by-number against computed stats — hallucinated digits get rejected.",
  "If the narration model misbehaves, a deterministic template ships instead. A run never fails because an LLM did.",
  "Expiration is simulated honestly: ITM shorts get assigned, stock unwinds at the next open.",
  "The equity curve is net of costs — commissions and slippage are already in it.",
  "Walk-forward windows are ~2 months each; one great quarter can't carry a verdict.",
  "Insufficient evidence isn't failure — it unlocks automatically as more data accrues.",
  "The 'as specced' column is ringed in the sensitivity grid so you can see exactly where you stand.",
  "Charts prefetch on the home page — Show on Chart opens instantly after the first visit.",
  "Trust is computed by fixed rules, not vibes — the LLM narrates the verdict; it never chooses it.",
];

// the same 50 lessons with the jargon translated away — shown when
// Verbiage Complexity is set to Retail
const TIPS_RETAIL = [
  "One stated exit rule is enough — the parser never invents the ones you didn't give.",
  "Chart mode: pin up to 10 examples. More examples make the test stricter, not easier.",
  "Below your minimum-trades bar (Settings, standard 15) the verdict is withheld — good-looking numbers don't override it.",
  "Every re-run of a similar strategy raises the bar — retrying until something works gets caught.",
  "On the results screen, ask questions — answers use only this run's computed numbers.",
  "Trading costs come from the setup you confirmed — Settings fill in the starting numbers when you first describe a strategy, and a variant keeps the original run's.",
  "In the settings-nudge grid, the ringed column is your exact setup — brighter neighbors did better.",
  "The verdict leads with the most uncomfortable finding on purpose. That's the product.",
  "Trades never fill at the perfect midpoint price — buys pay a little more, sells get a little less, like real life.",
  "The shaded strip on the account chart is data the strategy never saw during testing.",
  "Hover the account-value chart for the exact date, balance and loss-from-peak at any point.",
  "Hover a time-window bar to see that window's dates, return and trade count.",
  "You're in plain-English mode now — Settings can switch back to the technical wording any time.",
  "Switch between light and dark mode in Settings → Appearance; the accent color is yours to pick too.",
  "The accent color never touches profit or loss — green and red stay strictly for money.",
  "'Stable' in the nudge test is good: small settings changes don't wreck the result.",
  "'Fragile' is the warning: the result only works at exactly your settings. That smells like luck.",
  "Suggestions come from tests we actually ran on your strategy — never opinion.",
  "The trust band is a range, not a score — false precision would be dishonest.",
  "Trust level 5 still isn't a promise. It means the strategy survived everything we could throw at it.",
  "If the hidden-data result keeps less than half of the training result, the verdict calls it out.",
  "The luck check reshuffles your trade order 1,000 times to see how much was just sequence.",
  "Every run is repeatable — the same strategy on the same data always gives the identical result.",
  "The trade log shows real trades first; skipped entries hide behind their own toggle, each with a reason.",
  "Skip reasons matter: no buyers or huge price gaps are the market saying the trade was fantasy.",
  "SPY has options history to 2020 here; QQQ and IWM start much later — every verdict says what data it used.",
  "The data window is printed on every result — verdicts never pretend to more history than they have.",
  "Speak your strategy: the mic in the chatbox does dictation, tuned for tickers and numbers.",
  "Presets on the home page reorder by what you actually run most.",
  "The library sorts by trustworthiness, not by biggest return. On purpose.",
  "Old runs stay honest: their verdicts reflect the data that existed when they ran.",
  "The parser asks rather than guesses — a vague trigger like 'when it dips' earns you a question.",
  "Answer clarifying questions with the chips or your own words — either works.",
  "You can edit every dial on the review screen before anything runs — nothing runs unconfirmed.",
  "Exits stack: a profit target, a stop loss and a time limit can all apply at once.",
  "Same-day-expiry strategies are refused for now — honest testing needs finer data than we serve yet.",
  "The sidebar is drag-resizable — pull the edge, or push it small to snap to icons.",
  "Retries get punished: 100 attempts at the same idea makes one good result likely luck.",
  "All five attacks run on every single backtest — there's no express lane.",
  "The Q&A refuses to invent: if a number wasn't computed for this run, it says so.",
  "Every number in the verdict is checked against the computed results — made-up digits get rejected.",
  "If the writing model misbehaves, a plain fallback ships instead. A run never fails because an AI did.",
  "Expiration is simulated honestly: options that finish in the money become stock, sold at the next open.",
  "The account chart already includes all costs — commissions and slippage are baked in.",
  "Time windows are about 2 months each; one great quarter can't carry a verdict.",
  "'Not enough evidence' isn't failure — it unlocks automatically as more data accrues.",
  "The 'as specced' column is ringed in the nudge grid so you can see exactly where you stand.",
  "Charts preload on the home page — Show on Chart opens instantly after the first visit.",
  "Trust is computed by fixed rules, not vibes — the AI writes the words; it never picks the verdict.",
];

function shuffled<T>(list: T[]): T[] {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function GauntletProgress({
  stage,
  name,
  previews = [],
}: {
  stage: number;
  name: string;
  previews?: (string | { pro: string; retail: string })[];
}) {
  const settings = useSettings();
  const retail = settings.verbiage === "retail";
  const stages = retail ? RETAIL_STAGES : GAUNTLET_STAGES;
  // previews arrive in both voices; runs stored before the split are strings
  const previewLines = previews.map((p) =>
    typeof p === "string" ? p : retail ? p.retail : p.pro,
  );
  // tips play in a shuffled order — no repeats until the pool is exhausted
  const [tipOrder, setTipOrder] = useState(() => shuffled(retail ? TIPS_RETAIL : TIPS));
  const [tipIndex, setTipIndex] = useState(0);
  const [headingIndex, setHeadingIndex] = useState(0);
  const [headingVisible, setHeadingVisible] = useState(true);

  useEffect(() => {
    // verbiage flipped mid-run (Settings in another tab): swap the pool
    setTipOrder(shuffled(retail ? TIPS_RETAIL : TIPS));
    setTipIndex(0);
  }, [retail]);

  useEffect(() => {
    const id = setInterval(() => setTipIndex((i) => (i + 1) % tipOrder.length), 6000);
    return () => clearInterval(id);
  }, [tipOrder.length]);

  useEffect(() => {
    // fade out, swap the phrase, fade back in — every 5 seconds
    const id = setInterval(() => {
      setHeadingVisible(false);
      setTimeout(() => {
        setHeadingIndex((i) => (i + 1) % HEADINGS.length);
        setHeadingVisible(true);
      }, 350);
    }, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="mx-auto mt-[7vh] max-w-[650px]">
      <h2
        className={clsx(
          "mb-1.5 font-serif text-[32px] font-medium transition-opacity duration-300",
          headingVisible ? "opacity-100" : "opacity-0",
        )}
      >
        {HEADINGS[headingIndex]}
        <span className="inline-flex w-[1.4em]">
          <span className="animate-pin-pulse">.</span>
          <span className="animate-pin-pulse" style={{ animationDelay: "0.35s" }}>.</span>
          <span className="animate-pin-pulse" style={{ animationDelay: "0.7s" }}>.</span>
        </span>
      </h2>
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

      {previewLines.length > 0 && (
        <div className="mt-7 rounded-[14px] border border-trust-border bg-trust-dim px-5 py-4">
          <div className="mb-2.5 font-mono text-[11px] font-medium tracking-[.14em] text-trust">
            {retail
              ? "LIVE RESULTS — YOUR STRATEGY'S REAL NUMBERS, AS THEY LAND"
              : "LIVE FROM THE GAUNTLET — REAL NUMBERS, NOT A LOADING BAR"}
          </div>
          <div className="flex flex-col gap-1.5">
            {previewLines.map((p, i) => (
              <div
                key={i}
                className={clsx(
                  "font-mono text-[13.5px] leading-[1.55]",
                  i === previewLines.length - 1 ? "text-ink" : "text-ink-3",
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
        <p className="text-[14.5px] leading-[1.6] text-ink-2">{tipOrder[tipIndex]}</p>
      </div>
    </div>
  );
}
