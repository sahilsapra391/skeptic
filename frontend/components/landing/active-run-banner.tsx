"use client";

/**
 * The background-run banner (launch L4, owner-reported bug 2026-07-17).
 *
 * A run started from the landing popup keeps running server-side even if
 * the visitor closes the popup. This floating pill tracks it independently
 * of the popup: "running in the background…" → "your run is ready — view
 * results", reachable long after the popup is gone. If the run turns out
 * to be a phantom (a 404 on view — a demo-fallback run that didn't persist
 * or a lost run), it clears itself AND releases the device's one-free-run
 * gate so the visitor isn't stuck "used" with nothing to show.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, getRun } from "@/lib/api";
import { clearActiveRun } from "@/lib/active-run";
import { forgetRun } from "@/lib/my-runs";

type Status = "running" | "done" | "error" | "gone";

export function ActiveRunBanner({
  runId,
  onView,
  onClear,
}: {
  runId: string;
  onView: (runId: string) => void;
  onClear: () => void;
}) {
  const [status, setStatus] = useState<Status>("running");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus("running");
    // self-scheduling poll: await each response before arming the next so a
    // slow backend can't stack requests (same pattern as the run flow)
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
          // phantom — release the gate and stop tracking it
          forgetRun(runId);
          clearActiveRun();
          setStatus("gone");
          return;
        }
        // transient — fall through and reschedule
      }
      if (alive) timer.current = setTimeout(poll, 2500);
    };
    poll();
    return () => {
      alive = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [runId]);

  const dismiss = () => {
    clearActiveRun();
    onClear();
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
          onClick={() => onView(runId)}
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
        <span className="text-ink-3">Your run hit a problem on our end.</span>
      </Pill>
    );
  }

  return (
    <Pill onDismiss={dismiss}>
      <span className="flex items-center gap-2 text-ink-2">
        <span className="inline-block h-[7px] w-[7px] animate-pin-pulse rounded-full bg-trust motion-reduce:animate-none" />
        Your backtest is running in the background…
      </span>
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
    <div className="fixed inset-x-3 bottom-3 z-[70] mx-auto flex max-w-[520px] items-center justify-between gap-3 rounded-[12px] border border-line-hover bg-panel px-4 py-3 font-mono text-[12px] shadow-pop md:inset-x-auto md:right-6 md:bottom-6 md:left-auto">
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
