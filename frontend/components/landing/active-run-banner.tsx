"use client";

/**
 * The background-run banner (launch L4, owner-reported behavior).
 *
 * From the moment a visitor submits a prompt, their run is "in progress":
 * this floating pill tracks it whenever the popup is minimized —
 * "setting up…" → "running in the background…" → "your run is ready —
 * view results". A landing run keeps going server-side while minimized, and
 * clicking the pill brings the live popup back (owner: don't lose the run,
 * and don't let them start a second one — send them here).
 *
 * If the tracked run turns out to be a phantom (a 404 — a demo-fallback run
 * that didn't persist, or a lost run), it clears itself AND releases the
 * device's one-free-run gate so the visitor isn't stuck "used" with nothing.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, getRun } from "@/lib/api";
import { clearActiveRun } from "@/lib/active-run";
import { forgetRun } from "@/lib/my-runs";

type Status = "pending" | "running" | "done" | "error" | "gone";

export function ActiveRunBanner({
  runId,
  hasFlow,
  onReopen,
  onView,
  onDismiss,
  onPhantom,
}: {
  // null until the backtest is actually created (parse/interview/spec phase)
  runId: string | null;
  // a live run flow is still mounted this session — clicking reopens it,
  // rather than opening a fresh read-only view
  hasFlow: boolean;
  onReopen: () => void;
  onView: (runId: string) => void;
  onDismiss: () => void;
  onPhantom: () => void;
}) {
  const [status, setStatus] = useState<Status>(runId ? "running" : "pending");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!runId) {
      setStatus("pending");
      return;
    }
    let alive = true;
    setStatus("running");
    const poll = async () => {
      try {
        const p = await getRun(runId);
        if (!alive) return;
        if (p.status === "done") {
          setStatus("done");
          return;
        }
        if (p.status === "error") {
          setStatus("error");
          return;
        }
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          forgetRun(runId);
          clearActiveRun();
          setStatus("gone");
          onPhantom();
          return;
        }
        // transient — reschedule
      }
      if (alive) timer.current = setTimeout(poll, 2500);
    };
    poll();
    return () => {
      alive = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [runId, onPhantom]);

  const dismiss = () => {
    clearActiveRun();
    onDismiss();
  };

  if (status === "gone") {
    return (
      <Pill onDismiss={dismiss}>
        <span className="text-ink-3">
          That run didn&apos;t stick — your free backtest is still available.
        </span>
      </Pill>
    );
  }

  if (status === "done") {
    return (
      <Pill onDismiss={dismiss}>
        <button
          onClick={() => (hasFlow ? onReopen() : runId && onView(runId))}
          className="flex items-center gap-2 text-left"
        >
          <span className="inline-block h-[7px] w-[7px] rounded-full bg-trust" />
          <span className="font-semibold text-ink">Your run is ready</span>
          <span className="text-trust">— view results →</span>
        </button>
      </Pill>
    );
  }

  if (status === "error") {
    return (
      <Pill onDismiss={dismiss}>
        <button
          onClick={() => (hasFlow ? onReopen() : runId && onView(runId))}
          className="text-left text-ink-3"
        >
          Your run hit a problem on our end — open it →
        </button>
      </Pill>
    );
  }

  // pending (setting up) or running — both open the live popup on click
  return (
    <Pill onDismiss={dismiss}>
      <button onClick={onReopen} className="flex items-center gap-2 text-left">
        <span className="inline-block h-[7px] w-[7px] animate-pin-pulse rounded-full bg-trust motion-reduce:animate-none" />
        <span className="text-ink-2">
          {status === "pending"
            ? "Your backtest is being set up…"
            : "Your backtest is running in the background…"}
        </span>
        <span className="text-trust">open →</span>
      </button>
    </Pill>
  );
}

function Pill({
  children,
  onDismiss,
}: {
  children: React.ReactNode;
  onDismiss: () => void;
}) {
  return (
    <div className="fixed inset-x-3 bottom-3 z-[70] mx-auto flex max-w-[560px] items-center justify-between gap-3 rounded-[12px] border border-line-hover bg-panel px-4 py-3 font-mono text-[12px] shadow-pop md:inset-x-auto md:right-6 md:bottom-6 md:left-auto">
      <div className="min-w-0 flex-1 truncate">{children}</div>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] text-ink-4 hover:bg-raised-2 hover:text-ink"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <line x1="2" y1="2" x2="10" y2="10" />
          <line x1="10" y1="2" x2="2" y2="10" />
        </svg>
      </button>
    </div>
  );
}
