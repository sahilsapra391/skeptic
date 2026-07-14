"use client";

/** Export actions for the completed-run screen (parity Tier 1).
 *
 * Primary: Save PDF — prints the run screen itself via the browser's
 * print-to-PDF. The print stylesheet hides app chrome, the paper palette
 * is applied for the duration of the dialog, and the document title is
 * set to a filename shape (browsers default the saved PDF's name to it).
 *
 * Behind the menu: the executable .ipynb download — the run's story with
 * a pinned reproduce, for people who want to re-run the numbers. Demo
 * runs get no menu: there is no stored run to export, while Save PDF
 * still works (it prints whatever the screen honestly shows).
 *
 * The notebook fetch rides the same-origin proxy (which owns the bearer
 * token). fetch+Blob instead of a plain <a href> so a failure surfaces
 * inline in the menu instead of navigating to a JSON error body. */

import { useState } from "react";
import clsx from "clsx";

import { ApiError, fetchNotebook } from "@/lib/api";

const SEGMENT =
  "flex items-center gap-1.5 whitespace-nowrap border border-line bg-raised-2 py-2 text-[13px] font-semibold text-ink-2 hover:border-trust-border hover:bg-raised-3 hover:text-ink";

export function ExportActions({ runId, demo }: { runId: string; demo: boolean }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const printPdf = () => {
    setOpen(false);
    const root = document.documentElement;
    const prevTheme = root.dataset.theme;
    const prevTitle = document.title;
    root.dataset.theme = "light"; // paper palette for paper
    document.title = `skeptic-run-${runId}`;
    const restore = () => {
      if (prevTheme) root.dataset.theme = prevTheme;
      else delete root.dataset.theme;
      document.title = prevTitle;
      window.removeEventListener("afterprint", restore);
    };
    window.addEventListener("afterprint", restore);
    window.print();
    // print() blocks while the dialog is up in the engines that matter;
    // this covers any that skip the afterprint event (restore is idempotent)
    setTimeout(restore, 0);
  };

  const downloadNotebook = async () => {
    setBusy(true);
    setError(null);
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
      // revoking on the click tick can cancel the save in Firefox/Safari
      // before the download stream opens — give the browser a long beat
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setOpen(false);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "export failed",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex shrink-0 print:hidden">
      <button
        onClick={printPdf}
        title="save this screen as a PDF — the browser's print dialog opens with the paper palette applied"
        className={clsx(SEGMENT, "px-4", demo ? "rounded-[10px]" : "rounded-l-[10px]")}
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
          <path d="M4.5 6V2.5h7V6" />
          <path d="M4.5 11.5H3a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v3.5a1 1 0 0 1-1 1h-1.5" />
          <path d="M4.5 9.5h7v4h-7z" />
        </svg>
        Save PDF
      </button>
      {/* demo runs have no stored run to export — no menu, PDF only */}
      {!demo && (
        <button
          onClick={() => setOpen((v) => !v)}
          aria-label="more export options"
          aria-expanded={open}
          className={clsx(SEGMENT, "rounded-r-[10px] border-l-0 px-2")}
        >
          <svg
            width="11"
            height="11"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4 6.5l4 4 4-4" />
          </svg>
        </button>
      )}
      {open && !demo && (
        <>
          {/* click-away backdrop */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-[calc(100%+6px)] z-20 min-w-[240px] rounded-[10px] border border-line bg-raised-2 p-1.5 shadow-[var(--shadow-pop)]">
            <button
              onClick={downloadNotebook}
              disabled={busy}
              className="flex w-full items-center gap-2 rounded-[7px] px-2.5 py-2 text-left text-[12.5px] font-medium text-ink-2 hover:bg-raised-3 hover:text-ink disabled:cursor-wait"
              title="the run's story as an executable notebook — setup, numbers, honesty gauntlet, and a pinned reproduce"
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
              {busy ? "exporting…" : "Notebook (.ipynb)"}
            </button>
            {error && !busy && (
              <div className="px-2.5 pb-1 pt-1.5 font-mono text-[10.5px] leading-[1.5] text-warn">
                ⚠ {error}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
