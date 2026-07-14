"use client";

/**
 * Question-mark tooltip: plain-English explanations for stats and dials.
 * Pure CSS hover — no portal, no state. `align` keeps tooltips near the
 * viewport edges from clipping.
 */

import clsx from "clsx";

export function Hint({ text, align = "center" }: { text: string; align?: "center" | "right" }) {
  return (
    <span className="group/hint relative inline-flex shrink-0 print:hidden">
      <span
        className="flex h-[15px] w-[15px] cursor-help select-none items-center justify-center rounded-full border border-line text-[9.5px] font-semibold leading-none text-ink-4 group-hover/hint:border-line-hover group-hover/hint:text-ink-2"
        aria-label={text}
      >
        ?
      </span>
      <span
        className={clsx(
          "pointer-events-none absolute top-[calc(100%+7px)] z-30 w-[230px] rounded-[9px] border border-line bg-raised px-3 py-2 text-left font-sans text-[11.5px] font-normal normal-case leading-[1.55] tracking-normal text-ink-2 opacity-0 shadow-[var(--shadow-pop)] transition-opacity duration-100 group-hover/hint:opacity-100",
          align === "center" ? "left-1/2 -translate-x-1/2" : "right-0",
        )}
      >
        {text}
      </span>
    </span>
  );
}
