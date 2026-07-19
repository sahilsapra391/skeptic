"use client";

/**
 * The whole run flow as one continuous chat-led surface: compose
 * (describe it / show it on the chart) → spec confirmation → gauntlet →
 * results. One primary action per phase.
 *
 * Two mounts (launch L4): the /new page (URL params drive the boot), and
 * the landing's run popup (`embedded` — props drive the boot, the modal
 * owns the conversion chrome). Same parser, same engine, no demo fork.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";

import {
  ApiError,
  fetchMe,
  getCoverage,
  getRun,
  listRuns,
  parseText,
  prefetchBars,
  startBacktest,
} from "@/lib/api";
import { HEADLINES } from "@/lib/headlines";
import { myRunIds } from "@/lib/my-runs";
import { turnstileConfigured } from "@/lib/turnstile";
import { notifyCreditsChanged } from "@/lib/credits-events";
import { TurnstileWidget, type TurnstileHandle } from "@/components/landing/turnstile-widget";
import type {
  ParseQuestion,
  ProvenanceEvent,
  RunPayload,
  SpecDraft,
  Structure,
} from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";
import { useSpeechToText } from "@/lib/use-speech";

import { ChartTeach } from "@/components/composer/chart-teach";
import { ThinkingIndicator } from "@/components/composer/thinking";
import { GauntletProgress } from "@/components/gauntlet-progress";
import { ResultsView } from "@/components/results/results-view";
import { SpecScreen } from "@/components/spec/spec-screen";

type Phase = "compose" | "clarify" | "spec" | "running" | "results";
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

export function RunFlow({
  initialPitch,
  initialMode,
  embedded = false,
  onRunStarted,
  onTrialExhausted,
}: {
  initialPitch?: string;
  initialMode?: "chart";
  embedded?: boolean;
  // launch L4: the landing learns the run id the instant it's created, so
  // its background-run banner can track the run even if this popup closes
  onRunStarted?: (runId: string, demo: boolean) => void;
  // launch L4 anon armor: the backend refused this device's free run (402) —
  // the landing swaps this popup for the create-an-account gate. The reason is
  // the backend's honest detail (device-used vs trials-busy) so the gate shows
  // the right message.
  onTrialExhausted?: (reason?: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("compose");
  const [mode, setMode] = useState<Mode>("text");
  // What's actually on screen. Lags `mode` on chart → text so the chart can
  // play its collapse-upward exit before the slim chatbox takes its place.
  const [renderedMode, setRenderedMode] = useState<Mode>("text");
  const enteredFromChart = useRef(false);

  // Safety net: if the conceal animation never completes (throttled tab,
  // animations suppressed), don't leave the UI stuck in chart mode.
  useEffect(() => {
    if (mode !== "text" || renderedMode !== "chart") return;
    const id = setTimeout(() => {
      enteredFromChart.current = true;
      setRenderedMode("text");
    }, 700);
    return () => clearTimeout(id);
  }, [mode, renderedMode]);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<SpecDraft | null>(null);
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A generation counter makes "‹ edit input" a real cancel: a stale parse
  // response is dropped instead of yanking the UI forward.
  const compileGenRef = useRef(0);
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
  // Chunk A: the clarifying conversation, chronological with timestamps —
  // `questions`/`answers` above are working state (each round REPLACES
  // `questions`); this ref is the accumulated record that rides the run
  // request into provenance_json. Reset alongside `answers`.
  const transcriptRef = useRef<ProvenanceEvent[]>([]);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCancelledRef = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  // ---- launch L4 anon armor (embedded popup only) --------------------
  // The Turnstile widget: we mint the human-check token at RUN time (not at
  // mount) via this handle, so the first run always rides a fresh single-use
  // token instead of a stale/consumed one that would need a retry.
  const turnstileRef = useRef<TurnstileHandle>(null);
  // null = unknown (still resolving), true = anonymous visitor (armor
  // applies), false = signed-in (backend skips the armor). Drives whether
  // the trial disclosure + human check show. Only meaningful when embedded.
  const [isAnon, setIsAnon] = useState<boolean | null>(embedded ? null : false);
  // the honest "N runs ahead of you" + the trial's stated limits, shown
  // once the anon run is created
  const [trialNote, setTrialNote] = useState<{ queue: number; constraint: string } | null>(null);

  useEffect(() => {
    if (!embedded) return;
    let alive = true;
    // a signed-in account holder opening the landing popup is NOT anon —
    // resolve identity once so the trial framing only shows to visitors
    fetchMe()
      .then(() => alive && setIsAnon(false))
      .catch((e) => {
        if (!alive) return;
        // a definite 401 = anonymous; any other error (network / transient
        // 5xx) stays "unknown" (null) so a signed-in user isn't shown false
        // trial framing on a hiccup — null still mounts the human check, so a
        // true anon whose /me hiccuped is still gated by the backend
        setIsAnon(e instanceof ApiError && e.status === 401 ? true : null);
      });
    return () => {
      alive = false;
    };
  }, [embedded]);

  // the human check is a real gate only for an anonymous visitor with
  // Turnstile configured; everyone else runs without a token
  const humanCheckOn = embedded && isAnon !== false && turnstileConfigured();

  const speech = useSpeechToText((segment) => {
    // segments arrive already polished (lowercase, digits, canonical
    // tickers) — join verbatim, no sentence-casing
    setText((t) => {
      const sep = t && !/\s$/.test(t) ? " " : "";
      return t + sep + segment;
    });
  });

  const composerValue = speech.interim
    ? `${text}${text && !text.endsWith(" ") ? " " : ""}${speech.interim}`
    : text;

  // the chatbox grows a line at a time with its content (capped, then
  // scrolls). Empty clears the inline height instead of measuring — a
  // mount-time measurement before styles settle froze a bogus 200px into
  // the empty box on the landing's clone of this effect; same guard here
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
  }, [composerValue, renderedMode]);

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
    // (the pinned showcase examples are not usage — scoring them would give
    // every fresh visitor the same example-biased order)
    listRuns()
      .then(({ runs }) => {
        const history = runs
          .filter((r) => !r.example)
          .map((r) => `${r.name} ${r.meta}`.toLowerCase())
          .join(" · ");
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
      pollCancelledRef.current = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  // boot handoff (launch L4). Page mount: /new?pitch=<text> prefills +
  // auto-compiles, /new?mode=chart opens chart-teach — read via
  // location.search, NOT useSearchParams (the hook forces a Suspense split
  // of a client page at build, Next 14), one-shot, params consumed so a
  // refresh doesn't re-fire the parse. Embedded mount (landing popup):
  // props drive the same boot; the URL is the landing's and stays alone.
  const bootedRef = useRef(false);
  useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;
    let pitch: string | null | undefined = initialPitch;
    let modeParam: string | null | undefined = initialMode;
    if (!embedded) {
      const params = new URLSearchParams(window.location.search);
      pitch = params.get("pitch");
      modeParam = params.get("mode");
      if (params.has("pitch") || params.has("mode")) {
        window.history.replaceState(null, "", "/new");
      }
    }
    if (modeParam === "chart") {
      setMode("chart");
      setRenderedMode("chart");
      return;
    }
    if (pitch?.trim()) {
      setText(pitch);
      void compileTextRef.current(undefined, pitch);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const compileText = useCallback(
    // `source` overrides state `text` for the landing handoff: setText in
    // the same tick hasn't landed yet, so the boot effect passes the pitch
    // explicitly; clarify re-compiles keep reading state as before
    async (withAnswers?: Record<string, string>, source?: string) => {
      const input = source ?? text;
      if (!input.trim() || busy) return;
      const gen = ++compileGenRef.current;
      // the thinking view has no mic control — a live dictation must not
      // keep appending to the prompt behind it
      if (speech.listening) speech.stop();
      setBusy(true);
      setError(null);
      try {
        // a fresh compile starts a fresh story — even when it goes straight
        // to a spec, an earlier attempt's conversation must not ride along;
        // a re-compile with answers is the same conversation continuing
        if (!withAnswers) transcriptRef.current = [];
        const res = await parseText(input, withAnswers);
        if (gen !== compileGenRef.current) return; // cancelled — drop it
        if (res.status === "questions") {
          const asked = new Date().toISOString();
          transcriptRef.current.push(
            ...res.questions.map((q) => ({
              kind: "question" as const,
              id: q.id,
              question: q.question,
              options: q.options,
              asked_at: asked,
            })),
          );
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
        if (gen !== compileGenRef.current) return;
        setError(e instanceof Error ? e.message : "parse failed");
      } finally {
        if (gen === compileGenRef.current) setBusy(false);
      }
    },
    [text, busy, speech],
  );

  // live ref so the one-shot boot effect never calls a stale closure — the
  // same pattern use-speech.ts uses for onSegmentRef
  const compileTextRef = useRef(compileText);
  compileTextRef.current = compileText;

  const cancelCompile = useCallback(() => {
    compileGenRef.current++;
    setBusy(false);
    setPhase("compose");
  }, []);

  // parse in flight → the Claude-style thinking view (prompt bubble +
  // shimmering status). Derived, not stored: busy is only ever true during
  // a parse while composing/clarifying (running uses its own phase), so a
  // second flag could only ever drift out of sync with this.
  const thinking = busy && (phase === "compose" || phase === "clarify");

  const answerQuestion = useCallback(
    (answer: string) => {
      const q = questions[qIndex];
      if (!q || !answer.trim()) return;
      // a double-submit (chip double-click / repeated Enter) re-invokes with
      // a stale qIndex before re-render — never record the same answer twice
      const last = transcriptRef.current[transcriptRef.current.length - 1];
      if (!(last?.kind === "answer" && last.id === q.id)) {
        transcriptRef.current.push({
          kind: "answer",
          id: q.id,
          answer: answer.trim(),
          answered_at: new Date().toISOString(),
        });
      }
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
    // exit AND data window are required choices — never defaults
    if (!draft?.exit || !draft.window || busy) return;
    // claim the run synchronously — the human-check refresh below is awaited,
    // and without this a second click would slip past the busy guard and
    // start a duplicate run during that await
    setBusy(true);
    setError(null);
    // mint a FRESH human-check token for THIS run (not one from mount) so the
    // first run isn't rejected on a stale token; null means the widget can't
    // produce one yet — nudge instead of spending the engine on a free run
    let turnstileToken: string | null = null;
    if (humanCheckOn) {
      turnstileToken = (await turnstileRef.current?.refresh()) ?? null;
      if (!turnstileToken) {
        setBusy(false);
        setError("just finishing a quick human check — hit run once more in a second");
        return;
      }
    }
    // a narration-upgrade poll may still be armed for the PREVIOUS run —
    // kill it so its stale closure can't overwrite the new run's state
    if (pollRef.current) clearTimeout(pollRef.current);
    try {
      const untouched = parsedDraftRef.current === JSON.stringify(draft);
      const { run_id, demo, queuePosition, trialConstraint } = await startBacktest(
        draft,
        parsedSpecRef.current,
        untouched,
        transcriptRef.current,
        turnstileToken,
      );
      if (trialConstraint != null) {
        setTrialNote({ queue: queuePosition ?? 0, constraint: trialConstraint });
      }
      onRunStarted?.(run_id, demo);
      // a signed-in run just debited a credit — refresh the nav balance
      // (no navigation happens here, so it would otherwise go stale)
      notifyCreditsChanged();
      setPhase("running");
      setRun(null);
      // self-scheduling poll: each tick AWAITS the prior response before
      // arming the next, so a slow backend can never stack overlapping
      // requests (the old fixed 400ms setInterval flooded /api/runs/{id}
      // and stole threadpool threads from the running gauntlet)
      pollCancelledRef.current = false;
      // the balance changes at most once per run (refund on completion); fire
      // the refresh on the FIRST done/error, not on every narration poll tick
      let balanceNotified = false;
      const poll = async () => {
        try {
          const payload = await getRun(run_id);
          if (pollCancelledRef.current) return;
          setRun(payload);
          if (payload.status === "done") {
            setPhase("results");
            // a refusal refunds the credit at completion — refresh the balance
            if (!balanceNotified) {
              balanceNotified = true;
              notifyCreditsChanged();
            }
            // numbers are final; the narration upgrade is being written
            // off the critical path — keep a slow poll until it lands
            if (payload.narrationPending) {
              pollRef.current = setTimeout(poll, 3000);
            }
            return;
          }
          if (payload.status === "error") {
            setError(payload.error ?? "backtest failed");
            setPhase("spec");
            if (!balanceNotified) {
              balanceNotified = true;
              notifyCreditsChanged(); // an our-fault failure refunded the credit
            }
            return;
          }
        } catch {
          // transient — fall through and reschedule
        }
        if (!pollCancelledRef.current) pollRef.current = setTimeout(poll, 1200);
      };
      poll();
    } catch (e) {
      // anon armor: a spent free run (used-device OR global budget) → the
      // create-an-account gate replaces this popup, so don't also flash an
      // inline error. 403 = the human check didn't pass → a fresh token is
      // already being minted by the widget; just ask them to retry. 422
      // (intraday / >3y on the trial) falls through to its own clear message.
      if (e instanceof ApiError && e.status === 402) {
        // 402 has two callers: an ANON trial spent (device / global budget) →
        // the account gate; a SIGNED-IN account out of credits → the honest
        // message inline. isAnon===false = a resolved account (even in the
        // embedded landing popup), so a signed-in user is NEVER sent to the
        // "create a free account" gate — they already have one.
        if (onTrialExhausted && isAnon !== false) onTrialExhausted(e.detail);
        else setError(e.detail);
        return;
      }
      if (e instanceof ApiError && e.status === 403) {
        // refresh() already mints a fresh token on the next run, so no reset
        // bookkeeping here — just tell the visitor to run again
        setError("the human check didn't pass — give it a moment and run again");
      } else {
        setError(e instanceof Error ? e.message : "backtest failed");
      }
    } finally {
      setBusy(false);
    }
  }, [draft, busy, humanCheckOn, isAnon, onRunStarted, onTrialExhausted]);

  const reset = useCallback(() => {
    pollCancelledRef.current = true;
    if (pollRef.current) clearTimeout(pollRef.current);
    compileGenRef.current++;
    setPhase("compose");
    setRun(null);
    setDraft(null);
    setText("");
    setError(null);
    setBusy(false);
    setQuestions([]);
    setAnswers({});
    parsedSpecRef.current = null;
    parsedDraftRef.current = null;
    transcriptRef.current = [];
  }, []);

  if (phase === "results" && run) {
    return <ResultsView run={run} onEditSpec={() => setPhase("spec")} onNew={reset} />;
  }

  if (phase === "running") {
    return (
      <div>
        <GauntletProgress
          stage={run?.stage ?? 0}
          name={run?.name ?? draft?.quote ?? ""}
          previews={run?.previews}
        />
        {/* anon trial: honest queue position + the run's stated limits */}
        {trialNote && (
          <p className="mt-4 text-center font-mono text-[11px] leading-[1.6] text-ink-4">
            {trialNote.queue === 0
              ? "you're next in line"
              : `${trialNote.queue} run${trialNote.queue === 1 ? "" : "s"} ahead of you`}{" "}
            · {trialNote.constraint}
          </p>
        )}
      </div>
    );
  }

  // parse in flight (from compose OR the last clarify answer): the prompt
  // becomes a chat message and the parser thinks out loud under it
  if (thinking) {
    return (
      <div className="mx-auto max-w-[684px]">
        <button
          onClick={cancelCompile}
          className="mb-[18px] text-[12.5px] text-ink-4 hover:text-ink-3"
        >
          ‹ edit input
        </button>
        <div className="mb-4 flex justify-end">
          <div className="max-w-[75%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-3.5 py-2.5 font-mono text-[13px] leading-[1.55] text-ink-2">
            “{text}”
          </div>
        </div>
        <ThinkingIndicator />
      </div>
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
                  ? "bg-trust text-on-accent"
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
        {/* anon trial framing — honest about the free run's limits, so the
            visitor picks a daily ≤3y window instead of hitting the backend's
            refusal. Only shown to a confirmed anonymous visitor. */}
        {embedded && isAnon === true && (
          <p className="mt-3 text-center font-mono text-[11px] leading-[1.6] text-ink-4">
            free trial run — daily resolution, up to a 3-year window · create a
            free account for intraday and the full history
          </p>
        )}
        {/* the human check (invisible unless Cloudflare challenges) — mounted
            here so it has solved by the time RUN is clicked */}
        {embedded && isAnon !== false && (
          <div className="mt-3">
            <TurnstileWidget ref={turnstileRef} />
          </div>
        )}
        {error && (
          <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
            {error}
          </div>
        )}
      </div>
    );
  }

  const modeChips = (
    <div className="flex items-center gap-1">
      {(["text", "chart"] as const).map((m) => (
        <button
          key={m}
          onClick={() => {
            setMode(m);
            // Entering chart mode swaps immediately (the reveal plays over it);
            // leaving it waits for the conceal animation to finish.
            if (m === "chart") setRenderedMode("chart");
          }}
          className={clsx(
            "flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[13.5px] font-medium transition-colors duration-200",
            mode === m ? "bg-raised-2 text-ink" : "text-ink-4 hover:text-ink-2",
          )}
        >
          {m === "text" ? (
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11.1 1.9l3 3L6 13l-3.6.6L3 10z" />
              <path d="M9.6 3.4l3 3" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
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
  );

  return (
    <div>
      {/* embedded in the landing popup, the modal supplies the framing —
          drop the big headline so it's just the composer/chart (owner) */}
      {!embedded && (
        <div className="mx-auto mb-9 mt-[9vh] flex max-w-[900px] flex-col items-center">
          <h1 className="text-center font-serif text-[clamp(32px,3.6vw,44px)] font-medium leading-[1.12] tracking-[-.01em]">
            {headline}
          </h1>
        </div>
      )}

      {/* the landing's chart-teach popup is chart-only (owner 2026-07-17) —
          no Describe It escape hatch; the composer lives on the hero */}
      {!(embedded && initialMode === "chart") && (
        <div className="mb-4 flex justify-center">{modeChips}</div>
      )}

      {renderedMode === "text" ? (
        <div
          className={clsx(
            "mx-auto max-w-[960px]",
            enteredFromChart.current && "animate-fade-rise",
          )}
        >
          <div className="rounded-[22px] border border-line-soft bg-panel py-2.5 pl-6 pr-3 shadow-[var(--shadow-soft)] focus-within:border-line-hover">
            <div className="flex items-center gap-3">
              <textarea
                ref={composerRef}
                rows={1}
                className="w-full flex-1 text-[16.5px] leading-[1.65] text-ink placeholder:text-ink-4"
                placeholder="sell a 30-delta put on SPY every week, close at 50% profit or 21 days…"
                value={composerValue}
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
              <div className="flex items-center gap-1.5">
                {speech.supported && (
                  <button
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
                      <span className="inline-block h-[9px] w-[9px] animate-pin-pulse rounded-full bg-trust" />
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
                  onClick={() => compileText()}
                  disabled={!text.trim() || busy}
                  aria-label="Compile the strategy"
                  title={busy ? "Compiling…" : "Compile ↵"}
                  className={clsx(
                    "flex h-10 w-10 items-center justify-center rounded-full transition-colors",
                    text.trim() && !busy
                      ? "bg-ink text-ground hover:bg-ink-2"
                      : "cursor-not-allowed bg-raised-2 text-ink-4",
                  )}
                >
                  {busy ? (
                    <span className="inline-block h-[9px] w-[9px] animate-pin-pulse rounded-full bg-current" />
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
          <p className="mt-3.5 text-center text-[12.5px] text-ink-4">
            Research tool, not financial advice.
          </p>
          <div className="mx-auto mt-7 flex max-w-[1300px] flex-wrap justify-center gap-2.5">
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => setText(p.phrase)}
                title={p.phrase}
                className="group rounded-[14px] border border-line-soft bg-panel px-4 py-2.5 text-left hover:border-line-hover hover:bg-raised"
              >
                <div className="text-[14px] font-medium text-ink-2 group-hover:text-ink">
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
        <div
          className={mode === "chart" ? "animate-chart-reveal" : "animate-chart-conceal"}
          onAnimationEnd={(e) => {
            if (e.animationName === "chart-conceal") {
              enteredFromChart.current = true;
              setRenderedMode("text");
            }
          }}
        >
          <ChartTeach
            onCompile={(d) => {
              // the deferred device gate (owner 2026-07-17): opening the
              // chart is browsing — the free-run check lands HERE, when
              // "That's the idea" turns the pins into a run attempt. Only a
              // CONFIRMED anonymous visitor gates client-side; unresolved
              // identity (null — /me still in flight or hiccuped) falls
              // through to the backend armor at RUN, which is the authority
              // — a signed-in user with a stale my-runs breadcrumb must
              // never be shown the create-an-account gate.
              if (embedded && isAnon === true && myRunIds().length > 0) {
                onTrialExhausted?.(undefined);
                return;
              }
              // a chart draft supersedes any earlier chat parse — clear the
              // verbatim-spec refs so a stale spec can never ride along
              // (and the abandoned conversation, so it can't enter the
              // chart run's provenance)
              parsedSpecRef.current = null;
              parsedDraftRef.current = null;
              transcriptRef.current = [];
              setDraft(d);
              setPhase("spec");
            }}
          />
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          {error}
        </div>
      )}
    </div>
  );
}
