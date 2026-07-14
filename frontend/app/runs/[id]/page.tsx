"use client";

/** Saved-run view — the same Results surface, loaded by id from the library.
 * A run that's still in the gauntlet shows the live progress screen and
 * polls until the verdict lands — navigating away never loses a run. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getRun } from "@/lib/api";
import type { RunPayload } from "@/lib/types";

import { GauntletProgress } from "@/components/gauntlet-progress";
import { ResultsView } from "@/components/results/results-view";

export default function RunPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const payload = await getRun(params.id);
        if (cancelled) return;
        setRun(payload);
        if (payload.status === "error") {
          setError(payload.error ?? "run failed");
        } else if (payload.status !== "done") {
          timer = setTimeout(tick, 1200);
        } else if (payload.narrationPending) {
          // numbers are final; the narration upgrade is being written off
          // the critical path — poll slowly until the wording lands
          timer = setTimeout(tick, 3000);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "run not found");
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [params.id]);

  if (error) {
    return (
      <div className="mt-16 text-center">
        <div className="font-mono text-[13px] text-ink-3">{error}</div>
        <button
          onClick={() => router.push("/library")}
          className="mt-4 rounded-[9px] border border-line px-[13px] py-[7px] text-[12.5px] text-ink-3 hover:text-ink"
        >
          ‹ back to library
        </button>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="mt-16 text-center font-mono text-[12px] text-ink-4 animate-pin-pulse">
        loading run…
      </div>
    );
  }

  if (run.status !== "done") {
    return (
      <GauntletProgress
        stage={run.stage ?? 0}
        name={run.name ?? ""}
        previews={run.previews}
      />
    );
  }

  return <ResultsView run={run} onNew={() => router.push("/")} onBack={() => router.push("/library")} backLabel="Library" />;
}
