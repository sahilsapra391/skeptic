"use client";

/**
 * New Analysis — the entry point and the whole run flow as one continuous
 * chat-led surface: compose (describe it / show it on the chart) → spec
 * confirmation → gauntlet → results. One primary action per phase.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { getCoverage, getRun, parseText, startBacktest } from "@/lib/api";
import type { RunPayload, SpecDraft } from "@/lib/types";
import { useSpeechToText } from "@/lib/use-speech";

import { CoverageChips } from "@/components/coverage-chips";
import { ChartTeach } from "@/components/composer/chart-teach";
import { GauntletProgress } from "@/components/gauntlet-progress";
import { ResultsView } from "@/components/results/results-view";
import { SpecScreen } from "@/components/spec/spec-screen";

type Phase = "compose" | "spec" | "running" | "results";
type Mode = "text" | "chart";

const TEMPLATES: Record<string, string> = {
  "short put": "sell a 30-delta put on SPY every week, close at 50% profit or 21 days",
  "credit spread": "sell a 25-delta put spread on QQQ, $5 wide, 45 DTE, close at 50% profit",
  "iron condor": "iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop",
  "covered call": "covered call on SPY, sell the 30-delta monthly, roll at 21 DTE",
  "long call/put": "buy a 60-day SPY call after a 5% pullback, sell at +100% or -50%",
};

export default function NewAnalysisPage() {
  const [phase, setPhase] = useState<Phase>("compose");
  const [mode, setMode] = useState<Mode>("text");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<SpecDraft | null>(null);
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [earliestYear, setEarliestYear] = useState("1993");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const speech = useSpeechToText((segment) => {
    setText((t) => {
      const sep = t && !/\s$/.test(t) ? " " : "";
      const seg = t.trim() ? segment : segment[0].toUpperCase() + segment.slice(1);
      return t + sep + seg;
    });
  });

  useEffect(() => {
    getCoverage()
      .then((c) => {
        const first = c.underlying.SPY?.first;
        if (first) setEarliestYear(first.slice(0, 4));
      })
      .catch(() => undefined);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const compileText = useCallback(async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await parseText(text);
      setDraft(res.draft);
      setPhase("spec");
    } catch (e) {
      setError(e instanceof Error ? e.message : "parse failed");
    } finally {
      setBusy(false);
    }
  }, [text, busy]);

  const runGauntlet = useCallback(async () => {
    if (!draft?.exit || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await startBacktest(draft);
      setPhase("running");
      setRun(null);
      pollRef.current = setInterval(async () => {
        try {
          const payload = await getRun(run_id);
          setRun(payload);
          if (payload.status === "done") {
            if (pollRef.current) clearInterval(pollRef.current);
            setPhase("results");
          }
        } catch {
          // keep polling; transient
        }
      }, 400);
    } catch (e) {
      setError(e instanceof Error ? e.message : "backtest failed");
    } finally {
      setBusy(false);
    }
  }, [draft, busy]);

  const reset = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    setPhase("compose");
    setRun(null);
    setDraft(null);
    setText("");
    setError(null);
  }, []);

  if (phase === "results" && run) {
    return <ResultsView run={run} onEditSpec={() => setPhase("spec")} onNew={reset} />;
  }

  if (phase === "running") {
    return <GauntletProgress stage={run?.stage ?? 0} name={run?.name ?? draft?.quote ?? ""} />;
  }

  if (phase === "spec" && draft) {
    return (
      <div>
        <SpecScreen
          draft={draft}
          onChange={setDraft}
          onBack={() => setPhase("compose")}
          onRun={runGauntlet}
          earliestYear={earliestYear}
        />
        {error && (
          <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
            {error}
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <CoverageChips />

      <h1 className="mb-2.5 text-[34px] font-[650] leading-[1.1] tracking-[-.02em]">
        Describe a strategy.
        <br />
        I'll try to break it.
      </h1>
      <p className="mb-[26px] max-w-[560px] text-[15px] leading-normal text-ink-3">
        Plain English in. I compile it, backtest it, then attack the result — out-of-sample,
        walk-forward, Monte Carlo, sensitivity. The verdict leads with the uncomfortable part.
      </p>

      <div className="mb-3.5 inline-flex gap-[2px] rounded-[11px] border border-line-soft p-[3px]">
        {(["text", "chart"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={clsx(
              "rounded-[9px] px-4 py-2 text-[13px] font-semibold",
              mode === m ? "bg-raised-3 text-ink" : "text-ink-4",
            )}
          >
            {m === "text" ? "⌨ Describe it" : "⌖ Show it on the chart"}
          </button>
        ))}
      </div>

      {mode === "text" ? (
        <div>
          <div className="rounded-[14px] border border-line bg-panel px-4 pb-3 pt-4 focus-within:border-trust-border">
            <textarea
              rows={3}
              className="w-full font-mono text-[14.5px] leading-[1.6] text-ink"
              placeholder="sell a 30-delta put on SPY every week, close at 50% profit or 21 days…"
              value={
                speech.interim
                  ? `${text}${text && !text.endsWith(" ") ? " " : ""}${speech.interim}`
                  : text
              }
              onChange={(e) => {
                if (speech.listening) speech.stop();
                setText(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  compileText();
                }
              }}
            />
            <div className="mt-2 flex items-center justify-between gap-3">
              <span className={clsx("text-[12px]", speech.error ? "text-warn" : "text-ink-4")}>
                {speech.error ??
                  (speech.listening
                    ? "Listening — speak your strategy; tap the mic again to stop."
                    : "Ambiguity gets a question, never a guess.")}
              </span>
              <div className="flex items-center gap-2">
                {speech.supported && (
                  <button
                    onClick={() => (speech.listening ? speech.stop() : speech.start())}
                    title={speech.listening ? "Stop dictation" : "Dictate your strategy"}
                    className={clsx(
                      "flex h-[34px] w-[34px] items-center justify-center rounded-[9px] border",
                      speech.listening
                        ? "border-trust-border bg-trust-dim text-trust"
                        : "border-line text-ink-4 hover:border-line-hover hover:text-ink",
                    )}
                  >
                    {speech.listening ? (
                      <span className="inline-block h-[9px] w-[9px] animate-pin-pulse rounded-full bg-trust" />
                    ) : (
                      <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                        <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" />
                        <path d="M3 7.5a5 5 0 0 0 10 0" />
                        <line x1="8" y1="12.5" x2="8" y2="14.5" />
                      </svg>
                    )}
                  </button>
                )}
                <button
                  onClick={compileText}
                  disabled={!text.trim() || busy}
                  className={clsx(
                    "rounded-[9px] border px-4 py-[7px] text-[13px] font-semibold",
                    text.trim() && !busy
                      ? "border-trust-border bg-trust-dim text-trust"
                      : "cursor-not-allowed border-line bg-raised-2 text-ink-4",
                  )}
                >
                  {busy ? "Compiling…" : "Compile ↵"}
                </button>
              </div>
            </div>
          </div>
          <div className="mt-3.5 flex flex-wrap gap-2">
            {Object.entries(TEMPLATES).map(([label, phrase]) => (
              <button
                key={label}
                onClick={() => setText(phrase)}
                className="rounded-full border border-line px-[13px] py-1.5 text-[12.5px] text-ink-3 hover:border-trust-border hover:text-ink"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <ChartTeach
          onCompile={(d) => {
            setDraft(d);
            setPhase("spec");
          }}
        />
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          {error}
        </div>
      )}
    </div>
  );
}
