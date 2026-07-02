"use client";

/** Settings — cost assumptions, model wiring status, the standing disclaimer. */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { getHealth } from "@/lib/api";

const PANEL = "mb-3 rounded-[14px] border border-line bg-panel p-4";
const PANEL_TITLE = "mb-3 font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span>{label}</span>
      <span className={clsx("font-mono", dim ? "text-ink-4" : "text-ink")}>{value}</span>
    </div>
  );
}

export default function SettingsPage() {
  const [health, setHealth] = useState<{
    status: string;
    r2_configured: boolean;
    engine: string;
    parser: string;
  } | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setDown(true));
  }, []);

  return (
    <div className="max-w-[640px]">
      <h1 className="mb-[22px] text-[22px] font-[650]">Settings</h1>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>COST ASSUMPTIONS</div>
        <div className="flex flex-col gap-2.5 text-[13.5px]">
          <Row label="Commission" value="$0.65 / contract / side" />
          <Row label="Slippage" value="mid + 0.5 × half-spread" />
          <div className="text-[12px] text-ink-4">
            Mid fills are banned by design — slippage is a floor, not a knob you can zero out.
          </div>
        </div>
      </div>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>API STATUS</div>
        <div className="flex flex-col gap-2 text-[13.5px]">
          <Row
            label="Backend"
            value={down ? "unreachable" : health ? "ok ✓" : "checking…"}
            dim={down}
          />
          <Row
            label="Data lake (R2)"
            value={health ? (health.r2_configured ? "configured ✓" : "creds missing") : "—"}
            dim={!health?.r2_configured}
          />
          <Row label="Backtest engine" value={health?.engine ?? "pending (M2)"} dim />
          <Row label="NL parser" value={health?.parser ?? "pending (M4)"} dim />
        </div>
      </div>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>MODEL</div>
        <div className="flex flex-col gap-2 text-[13.5px]">
          <Row label="Parser / verdict LLM" value="openrouter — wired at M4" dim />
          <Row label="Numeric validation" value="on — no un-computed numbers" />
          <Row label="Seeds" value="fixed & logged per run" />
        </div>
      </div>

      <div className="rounded-[14px] border border-dashed border-line-hover p-4 text-[12.5px] leading-[1.6] text-ink-3">
        Skeptic is a research instrument. It produces no recommendations to buy or sell any
        security. Backtests are computed on approximate, self-collected data and systematically
        overstate real-world results. Past performance does not predict future results.
      </div>
    </div>
  );
}
