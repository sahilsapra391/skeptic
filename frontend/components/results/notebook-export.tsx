"use client";

/** Download the completed run as an executable .ipynb (parity Tier 1).
 * The endpoint sits behind the token-gated same-origin proxy, so this is
 * a fetch + Blob save — a plain <a href> would arrive without the bearer
 * token. Demo runs never render it: there is no stored run to export. */

import { useState } from "react";
import clsx from "clsx";

import { ApiError, fetchNotebook } from "@/lib/api";

export function NotebookExport({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const text = await fetchNotebook(runId);
      const url = URL.createObjectURL(
        new Blob([text], { type: "application/x-ipynb+json" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = `skeptic-run-${runId}.ipynb`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setError(null);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "export failed",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={download}
      disabled={busy}
      title={
        error ??
        "download this run as an executable notebook — the setup story, the numbers, the honesty gauntlet, and a pinned reproduce"
      }
      className={clsx(
        "flex items-center gap-1.5 whitespace-nowrap rounded-[10px] border border-line bg-raised-2 px-4 py-2 text-[13px] font-semibold hover:border-trust-border hover:bg-raised-3",
        error ? "text-warn" : "text-ink-2 hover:text-ink",
        busy && "cursor-wait",
      )}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M8 2.5v8" />
        <path d="M5 7.5l3 3 3-3" />
        <path d="M3 13.5h10" />
      </svg>
      {busy ? "exporting…" : error ? "retry export" : "Notebook"}
    </button>
  );
}
