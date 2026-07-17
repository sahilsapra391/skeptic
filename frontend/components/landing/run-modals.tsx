"use client";

/**
 * The landing's three product popups (launch L4, owner 2026-07-17):
 *
 * - RunFlowModal — the REAL run flow (parse → interview → gauntlet →
 *   results) embedded in a popup; the visitor never leaves the landing.
 * - RunViewModal — a stored run rendered start-to-finish (the showcase
 *   rows + "read the full run"); real payload, real ResultsView.
 * - DeviceGateModal — one free run per device: a second attempt is asked
 *   to create an account instead (client-remembered for now; the anon
 *   token + Turnstile armor is the accounts-chunk's server half).
 *
 * The heavy app components load on demand (next/dynamic) so the landing
 * shell stays static-fast until someone actually opens a popup.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";

import { getRun } from "@/lib/api";
import { myRunIds } from "@/lib/my-runs";
import type { RunPayload } from "@/lib/types";

import { LandingModal } from "./landing-modal";

const LoadingLine = ({ text }: { text: string }) => (
  <div className="flex items-center gap-2.5 py-16 font-mono text-[12px] text-ink-4">
    <span className="inline-block h-[8px] w-[8px] animate-pin-pulse rounded-full bg-trust motion-reduce:animate-none" />
    {text}
  </div>
);

const RunFlow = dynamic(() => import("@/components/run-flow").then((m) => m.RunFlow), {
  ssr: false,
  loading: () => <LoadingLine text="warming the composer…" />,
});
const ResultsView = dynamic(
  () => import("@/components/results/results-view").then((m) => m.ResultsView),
  { ssr: false, loading: () => <LoadingLine text="opening the run…" /> },
);

export function RunFlowModal({
  pitch,
  mode,
  onRunStarted,
  onClose,
  hidden = false,
}: {
  pitch?: string;
  mode?: "chart";
  onRunStarted?: (runId: string, demo: boolean) => void;
  onClose: () => void;
  // minimized to the banner while the run keeps going — children stay mounted
  hidden?: boolean;
}) {
  return (
    <LandingModal
      onClose={onClose}
      label="Your free backtest"
      wide
      hidden={hidden}
      conversionCta="Create an account to keep this run + get 5 free backtests"
    >
      <RunFlow embedded initialPitch={pitch} initialMode={mode} onRunStarted={onRunStarted} />
    </LandingModal>
  );
}

export function RunViewModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getRun(runId)
      .then((p) => {
        if (!alive) return;
        if (p.status !== "done") setError("this run isn't viewable right now");
        else setRun(p);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "run unavailable"));
    return () => {
      alive = false;
    };
  }, [runId]);

  return (
    <LandingModal
      onClose={onClose}
      label="The full run"
      wide
      conversionCta="Create an account — run one like this, 5 free"
    >
      {error && (
        <div className="py-16 font-mono text-[12px] text-warn">{error}</div>
      )}
      {!error && !run && <LoadingLine text="opening the run — every number recomputed from the stored payload…" />}
      {run && (
        <>
          {run.example && (
            <div className="mb-4 mt-2 flex items-center gap-3 rounded-[12px] border border-trust-border bg-trust-dim px-4 py-2.5">
              <span className="shrink-0 font-mono text-[10.5px] font-medium tracking-[.12em] text-trust">
                EXAMPLE RUN
              </span>
              <span className="text-[13px] text-ink-2">
                A real backtest from the library every account starts with.
              </span>
            </div>
          )}
          <ResultsView key={run.id} run={run} onBack={onClose} backLabel="Close" />
        </>
      )}
    </LandingModal>
  );
}

export function DeviceGateModal({ onClose }: { onClose: () => void }) {
  const [firstRun] = useState<string | null>(() => myRunIds()[0] ?? null);
  const [viewFirst, setViewFirst] = useState(false);
  if (viewFirst && firstRun) {
    return <RunViewModal runId={firstRun} onClose={onClose} />;
  }
  return (
    <LandingModal onClose={onClose} label="One free run per device">
      <div className="pb-2 pt-6">
        <h3 className="font-serif text-[26px] font-medium leading-[1.25]">
          You&apos;ve had this device&apos;s free backtest.
        </h3>
        <p className="mt-3 text-[14px] leading-[1.65] text-ink-2">
          The first one is on the house — the next ones come with a free
          account: 5 more backtests, a library that keeps your runs, and the
          run you already made comes with you.
        </p>
        <p className="mt-2.5 font-mono text-[11.5px] leading-[1.7] text-ink-4">
          a &ldquo;not enough evidence&rdquo; verdict refunds its credit — you
          only spend on graded verdicts
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Link
            href="/signup"
            className="rounded-[10px] bg-trust px-5 py-2.5 text-[14px] font-bold text-on-accent"
          >
            Create a free account — 5 backtests
          </Link>
          {firstRun && (
            <button
              onClick={() => setViewFirst(true)}
              className="rounded-[10px] border border-line px-4 py-2.5 text-[13.5px] text-ink-2 hover:border-line-hover"
            >
              revisit your run →
            </button>
          )}
        </div>
      </div>
    </LandingModal>
  );
}
