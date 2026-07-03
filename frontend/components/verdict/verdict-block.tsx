/**
 * The Verdict Block — the signature element (hero size). Renders the honest
 * verdict: headline first (the uncomfortable part), trust band, attack
 * chips, evidence vs where-it-breaks, caveats. The refusal state is a
 * first-class design, not an error.
 *
 * COLOR CONTRACT: trust hue family only. P/L tokens never appear here.
 */

import Link from "next/link";

import type { VerdictPayload } from "@/lib/types";

import { TrustBandHero } from "./trust-band";

export function VerdictBlock({ verdict }: { verdict: VerdictPayload }) {
  return (
    <div
      className={`rounded-2xl px-7 py-7 ${
        verdict.refusal ? "border border-dashed border-trust-border" : "border border-trust-border"
      } bg-[linear-gradient(180deg,var(--acd),var(--ac-faint))]`}
    >
      <div className="mb-2.5 flex items-center justify-between">
        <span className="font-mono text-[11.5px] font-medium tracking-[.14em] text-trust">
          VERDICT — THE HONEST READ
        </span>
        <span className="font-mono text-[12px] font-medium text-trust">{verdict.survived}</span>
      </div>
      <div className="max-w-[860px] font-serif text-[32px] font-medium leading-[1.25]">
        {verdict.headline}
      </div>

      {!verdict.refusal && (
        <div>
          <TrustBandHero band={verdict.band} marker={verdict.marker} />
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {verdict.chips.map((txt) => (
              <span
                key={txt}
                className="rounded-full border border-trust-border px-3 py-1 font-mono text-[12px] font-medium text-trust"
              >
                {txt}
              </span>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-5">
            <div>
              <div className="mb-2 font-mono text-[11.5px] font-medium tracking-[.12em] text-trust">
                HOLDS UP
              </div>
              {verdict.evidence.map((t) => (
                <div key={t} className="flex gap-2 text-[14.5px] leading-[1.6] text-ink-2">
                  <span className="text-ink-4">·</span>
                  <span>{t}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="mb-2 font-mono text-[11.5px] font-medium tracking-[.12em] text-trust">
                WHERE IT BREAKS
              </div>
              {verdict.breaks.map((t) => (
                <div key={t} className="flex gap-2 text-[14.5px] leading-[1.6] text-ink-2">
                  <span className="text-ink-4">·</span>
                  <span>{t}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="mt-4 text-[13.5px] leading-[1.6] text-ink-3">{verdict.caveat}</div>
        </div>
      )}

      {verdict.refusal && (
        <div>
          <p className="mt-4 max-w-[760px] text-[15.5px] leading-[1.65] text-ink-2">
            {verdict.refusalBody?.split("unblessed").map((part, i, arr) =>
              i < arr.length - 1 ? (
                <span key={i}>
                  {part}
                  <b>unblessed</b>
                </span>
              ) : (
                <span key={i}>{part}</span>
              ),
            )}
          </p>
          <div className="mt-3.5 flex flex-wrap items-center gap-2.5 rounded-[10px] border border-dashed border-trust-border px-3.5 py-2.5">
            <span className="font-mono text-[13px] text-trust">{verdict.refusalUnlock}</span>
            <Link
              href="/data"
              className="ml-auto font-mono text-[12px] text-ink-3 hover:text-ink"
            >
              track progress in Data →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
