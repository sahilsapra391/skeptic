/**
 * Run-in-progress state: the gauntlet attacking the strategy stage by
 * stage. Results stay hidden until the whole gauntlet finishes — no
 * previews, no dopamine.
 */

import clsx from "clsx";

import { GAUNTLET_STAGES } from "@/lib/types";

export function GauntletProgress({ stage, name }: { stage: number; name: string }) {
  return (
    <div className="mx-auto mt-[60px] max-w-[560px]">
      <h2 className="mb-1 text-2xl font-[650]">Attacking your strategy…</h2>
      <p className="mb-[22px] text-[13.5px] text-ink-3">{name}</p>
      <div className="mb-5 h-[5px] overflow-hidden rounded-[3px] bg-line-softer">
        <div
          className="h-full rounded-[3px] bg-trust transition-[width] duration-500"
          style={{ width: `${Math.min(100, (stage / GAUNTLET_STAGES.length) * 100)}%` }}
        />
      </div>
      <div className="flex flex-col gap-[11px]">
        {GAUNTLET_STAGES.map((st, i) => (
          <div
            key={st.t}
            className={clsx(
              "flex items-baseline gap-2.5 font-mono text-[13px]",
              i < stage ? "text-ink-3" : i === stage ? "text-ink" : "text-ink-5",
            )}
          >
            <span className="inline-block w-[18px]">{i < stage ? "✓" : i === stage ? "▶" : "○"}</span>
            <span className="flex-1">{st.t}</span>
            <span className="text-[11px] text-ink-4">{st.n}</span>
          </div>
        ))}
      </div>
      <p className="mt-6 text-[12.5px] text-ink-4">
        Results stay hidden until the whole gauntlet finishes — no previews, no dopamine.
      </p>
    </div>
  );
}
