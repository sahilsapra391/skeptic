"use client";

/**
 * Spec confirmation — every dial the engine will use, adjustable in place,
 * with the user's words alongside. A missing exit becomes a focused
 * question (never a default), and nothing runs on an unconfirmed spec.
 */

import clsx from "clsx";

import type { SpecDraft } from "@/lib/types";
import { STRUCTURE_LABEL } from "@/lib/types";

const TILE = "rounded-xl border border-line bg-panel px-3 py-2.5";
const TILE_LABEL = "mb-[5px] font-mono text-[10px] font-medium tracking-[.1em] text-ink-4";

function Stepper({
  value,
  render,
  onDec,
  onInc,
}: {
  value: number;
  render: (v: number) => string;
  onDec: () => void;
  onInc: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button onClick={onDec} className="text-[15px] text-ink-4 hover:text-ink">
        ‹
      </button>
      <span className="font-mono text-[17px] font-semibold">{render(value)}</span>
      <button onClick={onInc} className="text-[15px] text-ink-4 hover:text-ink">
        ›
      </button>
    </div>
  );
}

export function SpecScreen({
  draft,
  onChange,
  onBack,
  onRun,
  earliestYear,
}: {
  draft: SpecDraft;
  onChange: (d: SpecDraft) => void;
  onBack: () => void;
  onRun: () => void;
  earliestYear: string;
}) {
  const exitSet = !!draft.exit;
  const set = (patch: Partial<SpecDraft>) => onChange({ ...draft, ...patch });

  return (
    <div>
      <button onClick={onBack} className="mb-[18px] text-[12.5px] text-ink-4 hover:text-ink-3">
        ‹ edit input
      </button>

      <div className="mb-4 flex justify-end">
        <div className="max-w-[70%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-3.5 py-2.5 font-mono text-[13px] leading-[1.55] text-ink-2">
          {draft.fromChart ? `◉ ${draft.quote}` : `“${draft.quote}”`}
        </div>
      </div>
      <p className="mb-3 text-[14.5px] text-ink-3">Here's what I heard — every dial is adjustable:</p>

      <div className="grid grid-cols-4 gap-2.5">
        <div className={TILE}>
          <div className={TILE_LABEL}>TICKER</div>
          <div className="font-mono text-[17px] font-semibold">{draft.ticker}</div>
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>STRUCTURE</div>
          <div className="pt-0.5 font-mono text-[14px] font-semibold">
            {STRUCTURE_LABEL[draft.structure]}
          </div>
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>STRIKE</div>
          <Stepper
            value={draft.strikeDelta}
            render={(v) => `.${v}Δ`}
            onDec={() => set({ strikeDelta: Math.max(10, draft.strikeDelta - 5) })}
            onInc={() => set({ strikeDelta: Math.min(50, draft.strikeDelta + 5) })}
          />
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>DTE</div>
          <Stepper
            value={draft.dte}
            render={(v) => String(v)}
            onDec={() => set({ dte: Math.max(7, draft.dte - 5) })}
            onInc={() => set({ dte: Math.min(90, draft.dte + 5) })}
          />
        </div>

        {draft.fromChart ? (
          <>
            <div className={clsx(TILE, "!border-trust-border")}>
              <div className={clsx(TILE_LABEL, "!text-trust")}>ANCHOR ⌖</div>
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.anchor}</div>
            </div>
            <div className={TILE}>
              <div className={TILE_LABEL}>TRIGGER</div>
              <div className="pt-[3px] font-mono text-[12px] font-semibold">{draft.trigger}</div>
            </div>
          </>
        ) : (
          <>
            <div className={TILE}>
              <div className={TILE_LABEL}>CADENCE</div>
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.cadence}</div>
            </div>
            <div className={TILE}>
              <div className={TILE_LABEL}>SIZE</div>
              <div className="pt-0.5 font-mono text-[13px] font-semibold">{draft.size}</div>
            </div>
          </>
        )}

        <div className={clsx(TILE, !exitSet && "!border-trust-border !bg-trust-dim")}>
          <div className={clsx(TILE_LABEL, !exitSet && "!text-trust")}>EXIT</div>
          <div className="pt-0.5 font-mono text-[13px] font-semibold">
            {draft.exit ?? "not set"}
          </div>
        </div>
        <div className={TILE}>
          <div className={TILE_LABEL}>FILLS</div>
          <div className="pt-[3px] font-mono text-[12px] font-semibold">bid/ask + slip 0.5</div>
        </div>
      </div>

      {!exitSet && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-trust-border bg-trust-dim px-3.5 py-3">
          <span className="text-[13.5px] text-ink">
            <b className="text-trust">One question</b> — you gave no exit. Close at:
          </span>
          {["50% profit", "21 DTE", "hold to expiry"].map((label) => (
            <button
              key={label}
              onClick={() => set({ exit: label })}
              className="rounded-full border border-trust-border px-[13px] py-[5px] text-[12.5px] text-trust hover:bg-trust-dim"
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {draft.fromChart && (
        <div className="mt-3 rounded-xl border border-trust-border bg-trust-dim px-3.5 py-3 text-[13.5px] leading-[1.5] text-ink">
          You showed me winners — that's what eyes do. I'll test{" "}
          <b>every look-alike since {earliestYear}</b>, losers included. If the edge lives only in
          your examples, the verdict will say exactly that.
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <span className="text-[12.5px] text-ink-4">Nothing runs on an unconfirmed spec.</span>
        <button
          onClick={onRun}
          disabled={!exitSet}
          className={clsx(
            "rounded-[10px] px-5 py-2.5 text-[14px]",
            exitSet ? "bg-trust font-bold text-[#0d1216]" : "cursor-not-allowed bg-raised-2 text-ink-4",
          )}
        >
          Run the gauntlet →
        </button>
      </div>
    </div>
  );
}
