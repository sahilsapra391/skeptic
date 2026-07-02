"use client";

/** Saved-run view — the same Results surface, loaded by id from the library. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getRun } from "@/lib/api";
import type { RunPayload } from "@/lib/types";

import { ResultsView } from "@/components/results/results-view";

export default function RunPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const [run, setRun] = useState<RunPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRun(params.id)
      .then(setRun)
      .catch((e) => setError(e instanceof Error ? e.message : "run not found"));
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

  return <ResultsView run={run} onNew={() => router.push("/")} />;
}
