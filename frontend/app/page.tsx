"use client";

/**
 * New Analysis — the entry point and the whole run flow as one continuous
 * chat-led surface: compose (describe it / show it on the chart) → spec
 * confirmation → gauntlet → results. One primary action per phase.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { getCoverage, getRun, listRuns, parseText, prefetchBars, startBacktest } from "@/lib/api";
import type { ParseQuestion, RunPayload, SpecDraft, Structure } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";
import { useSpeechToText } from "@/lib/use-speech";

import { ChartTeach } from "@/components/composer/chart-teach";
import { GauntletProgress } from "@/components/gauntlet-progress";
import { ResultsView } from "@/components/results/results-view";
import { SpecScreen } from "@/components/spec/spec-screen";

type Phase = "compose" | "clarify" | "spec" | "running" | "results";
type Mode = "text" | "chart";

/** Starter strategies. Ordered by what the library says the user actually
 * runs (structure counts from past runs), most-used first. */
/** The hero headline rotates — a different version of the same promise on
 * every visit (sequential, persisted, so it always changes). */
const HEADLINES = [
  "Describe a strategy. I'll try to break it.",
  "Bring your thesis. I'll bring the evidence.",
  "Pitch me a trade. I'll play the skeptic.",
  "Bring me your best idea. I'll stress-test it.",
  "Describe a strategy. Let's see what survives.",
  "Got an edge? Prove it against the data.",
  "Tell me the trade. I'll tell you where it breaks.",
  "Describe a strategy. The data gets the last word.",
  "Show me a winner. I'll check if it was luck.",
  "Your idea versus six years of market data. Go.",
];

const PRESETS: { label: string; structure: Structure; phrase: string }[] = [
  {
    label: "Weekly income put",
    structure: "short_put",
    phrase: "sell a 30-delta put on SPY every week, close at 50% profit or 21 days",
  },
  {
    label: "Conservative income put",
    structure: "short_put",
    phrase: "sell a 16-delta put on SPY monthly, 45 DTE, close at 50% profit or 21 DTE",
  },
  {
    label: "Aggressive weekly put",
    structure: "short_put",
    phrase: "sell a 45-delta put on SPY every week, 30 DTE, close at 50% profit, stop at 2x credit",
  },
  {
    label: "Defined-risk put spread",
    structure: "put_credit_spread",
    phrase: "sell a 25-delta put spread on SPY, $5 wide, 45 DTE, close at 50% profit",
  },
  {
    label: "Quick-cycle put spread",
    structure: "put_credit_spread",
    phrase: "sell a 30-delta put spread on QQQ, $5 wide, 21 DTE, close at 25% profit or stop 2x",
  },
  {
    label: "Fade-the-rally call spread",
    structure: "call_credit_spread",
    phrase: "sell a 25-delta call spread on SPY, $5 wide, 30 DTE, close at 50% profit, stop at 2x credit",
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
    label: "RSI-oversold call",
    structure: "long_call",
    phrase: "buy a 60 DTE ATM call on QQQ when RSI(14) < 30, sell at 100% gain or 20 DTE, one at a time",
  },
  {
    label: "Crash-insurance put",
    structure: "long_put",
    phrase: "buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry",
  },
  {
    label: "Momentum-fade put",
    structure: "long_put",
    phrase: "long put on SPY when price is 3% above its 50 SMA, 45 DTE ATM, exit 21 DTE or 75% profit",
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
  const [headline, setHeadline] = useState(HEADLINES[0]);
  const [presets, setPresets] = useState(PRESETS);
  const [questions, setQuestions] = useState<ParseQuestion[]>([]);
  const [qIndex, setQIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [qInput, setQInput] = useState("");
  // the parser's validated spec + the draft it projected — an unedited draft
  // runs the parser spec verbatim, dial edits rebuild from the dials
  const parsedSpecRef = useRef<Record<string, unknown> | null>(null);
  const parsedDraftRef = useRef<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const speech = useSpeechToText((segment) => {
    setText((t) => {
      const sep = t && !/\s$/.test(t) ? " " : "";
      const seg = t.trim() ? segment : segment[0].toUpperCase() + segment.slice(1);
      return t + sep + seg;
    });
  });

  useEffect(() => {
    // rotate the headline once per visit (client-only to avoid a
    // hydration mismatch with the server-rendered default)
    try {
      const i = Number(localStorage.getItem("skeptic-headline") ?? "0") % HEADLINES.length;
      setHeadline(HEADLINES[i]);
      localStorage.setItem("skeptic-headline", String((i + 1) % HEADLINES.length));
    } catch {
      /* private mode — keep the default */
    }
    // warm the chart's first bars request so "Show on Chart" opens instantly
    prefetchBars();
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

  const compileText = useCallback(
    async (withAnswers?: Record<string, string>) => {
      if (!text.trim() || busy) return;
      setBusy(true);
      setError(null);
      try {
        const res = await parseText(text, withAnswers);
        if (res.status === "questions") {
          setQuestions(res.questions);
          setQIndex(0);
          setQInput("");
          if (!withAnswers) setAnswers({});
          setPhase("clarify");
        } else {
          parsedSpecRef.current = res.spec ?? null;
          parsedDraftRef.current = JSON.stringify(res.draft);
          setDraft(res.draft);
          setPhase("spec");
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "parse failed");
      } finally {
        setBusy(false);
      }
    },
    [text, busy],
  );

  const answerQuestion = useCallback(
    (answer: string) => {
      const q = questions[qIndex];
      if (!q || !answer.trim()) return;
      const next = { ...answers, [q.id]: answer.trim() };
      setAnswers(next);
      setQInput("");
      if (qIndex + 1 < questions.length) {
        setQIndex(qIndex + 1);
      } else {
        void compileText(next);
      }
    },
    [questions, qIndex, answers, compileText],
  );

  const runGauntlet = useCallback(async () => {
    if (!draft?.exit || busy) return;
    setBusy(true);
    setError(null);
    try {
      const untouched = parsedDraftRef.current === JSON.stringify(draft);
      const { run_id } = await startBacktest(draft, untouched ? parsedSpecRef.current : null);
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
    setQuestions([]);
    setAnswers({});
    parsedSpecRef.current = null;
    parsedDraftRef.current = null;
  }, []);

  if (phase === "results" && run) {
    return <ResultsView run={run} onEditSpec={() => setPhase("spec")} onNew={reset} />;
  }

  if (phase === "running") {
    return (
      <GauntletProgress
        stage={run?.stage ?? 0}
        name={run?.name ?? draft?.quote ?? ""}
        previews={run?.previews}
      />
    );
  }

  if (phase === "clarify" && questions.length > 0) {
    const q = questions[qIndex];
    return (
      <div className="mx-auto max-w-[684px]">
        <button
          onClick={() => setPhase("compose")}
          className="mb-[18px] text-[12.5px] text-ink-4 hover:text-ink-3"
        >
          ‹ edit input
        </button>
        <div className="mb-4 flex justify-end">
          <div className="max-w-[75%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-3.5 py-2.5 font-mono text-[13px] leading-[1.55] text-ink-2">
            “{text}”
          </div>
        </div>
        <div className="rounded-[14px] border border-trust-border bg-trust-dim px-5 py-4">
          <div className="mb-1 font-mono text-[10.5px] font-medium tracking-[.12em] text-trust">
            QUESTION {qIndex + 1} OF {questions.length} — I DON&apos;T GUESS
          </div>
          <div className="mb-3.5 text-[16.5px] font-semibold leading-snug">{q.question}</div>
          <div className="flex flex-wrap items-center gap-2">
            {q.options.map((opt) => (
              <button
                key={opt}
                onClick={() => answerQuestion(opt)}
                disabled={busy}
                className="rounded-full border border-trust-border px-3.5 py-[6px] text-[13px] text-trust hover:bg-trust/10"
              >
                {opt}
              </button>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <input
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") answerQuestion(qInput);
              }}
              placeholder="or answer in your own words ↵"
              autoFocus
              className="flex-1 rounded-[9px] border border-line bg-panel-deep px-3 py-2 font-mono text-[13px] text-ink placeholder:text-ink-4 focus:border-trust-border focus:outline-none"
            />
            <button
              onClick={() => answerQuestion(qInput)}
              disabled={!qInput.trim() || busy}
              className={clsx(
                "rounded-[9px] px-4 py-2 text-[13px] font-semibold",
                qInput.trim() && !busy
                  ? "bg-trust text-[#0d1216]"
                  : "cursor-not-allowed bg-raised-2 text-ink-4",
              )}
            >
              {busy ? "compiling…" : "answer"}
            </button>
          </div>
        </div>
        {error && (
          <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
            {error}
          </div>
        )}
      </div>
    );
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
      <h1 className="mx-auto mb-[30px] mt-[4vh] max-w-[820px] text-center text-[clamp(34px,4.2vw,46px)] font-[650] leading-[1.08] tracking-[-.02em]">
        {headline}
      </h1>

      <div className="mb-3.5 flex justify-center">
        <div className="inline-flex gap-[2px] rounded-[11px] border border-line-soft p-[3px]">
          {(["text", "chart"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={clsx(
                "flex items-center gap-2 rounded-[10px] px-5 py-2.5 text-[14.5px] font-semibold",
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
        <div className="mx-auto max-w-[1130px]">
          <div className="rounded-[14px] border border-line bg-panel px-4 pb-3 pt-4 focus-within:border-trust-border">
            <textarea
              rows={4}
              className="w-full font-mono text-[16px] leading-[1.65] text-ink"
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
                      "flex h-[38px] w-[38px] items-center justify-center rounded-[10px] border",
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
                  onClick={() => compileText()}
                  disabled={!text.trim() || busy}
                  className={clsx(
                    "rounded-[10px] border px-5 py-2 text-[14.5px] font-semibold",
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
          <div className="mx-auto mt-6 flex max-w-[1300px] flex-wrap justify-center gap-2.5">
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => setText(p.phrase)}
                title={p.phrase}
                className="group rounded-[12px] border border-line bg-panel px-4 py-2.5 text-left hover:border-trust-border hover:bg-trust-dim"
              >
                <div className="text-[14px] font-semibold text-ink-2 group-hover:text-ink">
                  {p.label}
                </div>
                <div className="mt-[2px] font-mono text-[11px] tracking-[.04em] text-ink-4 group-hover:text-ink-3">
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
