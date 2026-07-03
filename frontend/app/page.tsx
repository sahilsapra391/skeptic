"use client";

/**
 * New Analysis — the entry point and the whole run flow as one continuous
 * chat-led surface: compose (describe it / show it on the chart) → spec
 * confirmation → gauntlet → results. One primary action per phase.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { getCoverage, getRun, listRuns, parseText, startBacktest } from "@/lib/api";
import type { RunPayload, SpecDraft, Structure } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";
import { useSpeechToText } from "@/lib/use-speech";

import { CoverageChips } from "@/components/coverage-chips";
import { ChartTeach } from "@/components/composer/chart-teach";
import { GauntletProgress } from "@/components/gauntlet-progress";
import { ResultsView } from "@/components/results/results-view";
import { SpecScreen } from "@/components/spec/spec-screen";

type Phase = "compose" | "spec" | "running" | "results";
type Mode = "text" | "chart";

/** Starter strategies. Ordered by what the library says the user actually
 * runs (structure counts from past runs), most-used first. */
const PRESETS: { label: string; structure: Structure; phrase: string }[] = [
  {
    label: "Weekly income put",
    structure: "short_put",
    phrase: "sell a 30-delta put on SPY every week, close at 50% profit or 21 days",
  },
  {
    label: "Defined-risk put spread",
    structure: "put_credit_spread",
    phrase: "sell a 25-delta put spread on SPY, $5 wide, 45 DTE, close at 50% profit",
  },
  {
    label: "Calm-market condor",
    structure: "iron_condor",
    phrase: "iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop",
  },
  {
    label: "Covered-call income",
    structure: "covered_call",
    phrase: "covered call on SPY, sell the 30-delta monthly, roll at 21 DTE",
  },
  {
    label: "Dip-buyer call",
    structure: "long_call",
    phrase: "buy a 60-day SPY call after a 5% pullback, sell at +100% or stop 50%",
  },
  {
    label: "Crash-insurance put",
    structure: "long_put",
    phrase: "buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry",
  },
];

export default function NewAnalysisPage() {
  const [phase, setPhase] = useState<Phase>("compose");
  const [mode, setMode] = useState<Mode>("text");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<SpecDraft | null>(null);
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [earliestYear, setEarliestYear] = useState("1993");
  const [presets, setPresets] = useState(PRESETS);
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
    // presets follow usage: structures you actually run float to the front
    listRuns()
      .then(({ runs }) => {
        const history = runs.map((r) => `${r.name} ${r.meta}`.toLowerCase()).join(" · ");
        const scored = PRESETS.map((p, i) => ({
          p,
          i,
          n: history.split(STRUCTURE_LABEL[p.structure]).length - 1,
        }));
        scored.sort((a, b) => b.n - a.n || a.i - b.i);
        setPresets(scored.map((s) => s.p));
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
          } else if (payload.status === "error") {
            if (pollRef.current) clearInterval(pollRef.current);
            setError(payload.error ?? "backtest failed");
            setPhase("spec");
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
        Describe a strategy. I'll try to break it.
      </h1>
      <p className="mb-[26px] max-w-[560px] text-[15px] leading-normal text-ink-3">
        Plain English in. I compile it, backtest it, then attack the result — out-of-sample,
        walk-forward, Monte Carlo, sensitivity. The verdict leads with the uncomfortable part.
      </p>

      <div className="mb-3.5 flex justify-center">
        <div className="inline-flex gap-[2px] rounded-[11px] border border-line-soft p-[3px]">
          {(["text", "chart"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={clsx(
                "flex items-center gap-2 rounded-[9px] px-4 py-2 text-[13px] font-semibold",
                mode === m ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-2",
              )}
            >
              {m === "text" ? (
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11.1 1.9l3 3L6 13l-3.6.6L3 10z" />
                  <path d="M9.6 3.4l3 3" />
                  <line x1="2.5" y1="15" x2="13.5" y2="15" />
                </svg>
              ) : (
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
                  <line x1="3.2" y1="5.2" x2="3.2" y2="13.2" />
                  <rect x="2" y="7" width="2.4" height="3.6" rx="0.5" fill="currentColor" stroke="none" />
                  <line x1="8" y1="1.8" x2="8" y2="10.4" />
                  <rect x="6.8" y="3.6" width="2.4" height="4.2" rx="0.5" fill="currentColor" stroke="none" />
                  <line x1="12.8" y1="4.4" x2="12.8" y2="14.2" />
                  <rect x="11.6" y="6.6" width="2.4" height="3.8" rx="0.5" fill="currentColor" stroke="none" />
                </svg>
              )}
              {m === "text" ? "Describe It" : "Show on Chart"}
            </button>
          ))}
        </div>
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
                    : "")}
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
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => setText(p.phrase)}
                title={p.phrase}
                className="group rounded-[11px] border border-line bg-panel px-3.5 py-2 text-left hover:border-trust-border hover:bg-trust-dim"
              >
                <div className="text-[12.5px] font-semibold text-ink-2 group-hover:text-ink">
                  {p.label}
                </div>
                <div className="mt-[1px] font-mono text-[10px] tracking-[.04em] text-ink-4 group-hover:text-ink-3">
                  {STRUCTURE_LABEL[p.structure]}
                </div>
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
