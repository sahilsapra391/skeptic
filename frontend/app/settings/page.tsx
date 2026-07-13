"use client";

/** Settings — editable cost assumptions (applied to every new run), the
 * verbiage-complexity register, live system status, the standing
 * disclaimer. */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { getHealth } from "@/lib/api";
import type { Accent, Theme } from "@/lib/settings";
import {
  ACCENTS,
  DEFAULT_SETTINGS,
  updateSettings,
  useResolvedTheme,
  useSettings,
} from "@/lib/settings";

const THEME_LABEL: Record<Theme, string> = {
  market: "market hours",
  light: "light",
  dark: "dark",
};

/** Swatch preview colors per accent — the value each theme actually uses. */
const ACCENT_PREVIEW: Record<Accent, { dark: string; light: string; label: string }> = {
  cyan: { dark: "rgb(63 193 207)", light: "rgb(13 125 138)", label: "Cyan" },
  sage: { dark: "rgb(156 204 163)", light: "rgb(58 122 72)", label: "Sage" },
  lavender: { dark: "rgb(178 164 235)", light: "rgb(100 84 200)", label: "Lavender" },
  rose: { dark: "rgb(232 166 180)", light: "rgb(176 71 96)", label: "Rose" },
};

const PANEL = "mb-3.5 rounded-[14px] border border-line bg-panel p-5";
const PANEL_TITLE = "mb-3.5 font-mono text-[11px] font-medium tracking-[.12em] text-ink-4";

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span>{label}</span>
      <span className={clsx("text-right font-mono text-[13px]", dim ? "text-ink-4" : "text-ink")}>
        {value}
      </span>
    </div>
  );
}

function NumberField({
  label,
  suffix,
  value,
  min,
  max,
  step,
  onCommit,
}: {
  label: string;
  suffix: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text);
    if (Number.isFinite(v)) onCommit(Math.min(max, Math.max(min, v)));
    else setText(String(value));
  };
  return (
    <div className="flex items-center justify-between gap-4">
      <span>{label}</span>
      <span className="flex items-center gap-2">
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
          }}
          className="w-[92px] rounded-[9px] border border-line bg-panel-deep px-3 py-1.5 text-right font-mono text-[14px] text-ink focus:border-trust-border focus:outline-none"
        />
        <span className="w-[120px] font-mono text-[12px] text-ink-4">{suffix}</span>
      </span>
    </div>
  );
}

export default function SettingsPage() {
  const settings = useSettings();
  const resolved = useResolvedTheme();
  const [health, setHealth] = useState<Awaited<ReturnType<typeof getHealth>> | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setDown(true));
  }, []);

  const costsDefault =
    settings.commission === DEFAULT_SETTINGS.commission &&
    settings.slippage === DEFAULT_SETTINGS.slippage &&
    settings.slippageSell === DEFAULT_SETTINGS.slippageSell;

  return (
    <div>
      <h1 className="mb-[26px] font-serif text-[32px] font-medium">Settings</h1>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>COSTS — APPLIED TO EVERY NEW RUN</div>
        <div className="flex flex-col gap-3.5 text-[14.5px]">
          <NumberField
            label="Commission"
            suffix="$ / contract / side"
            value={settings.commission}
            min={0}
            max={5}
            step={0.05}
            onCommit={(v) => updateSettings({ commission: v })}
          />
          <NumberField
            label="Slippage — buys"
            suffix="× half-spread"
            value={settings.slippage}
            min={0.05}
            max={1}
            step={0.05}
            onCommit={(v) => updateSettings({ slippage: v })}
          />
          <NumberField
            label="Slippage — sells"
            suffix="× half-spread"
            value={settings.slippageSell}
            min={0.05}
            max={1}
            step={0.05}
            onCommit={(v) => updateSettings({ slippageSell: v })}
          />
          <div className="flex items-center justify-between">
            <span className="text-[12.5px] leading-[1.55] text-ink-4">
              Buys fill toward the ask, sells toward the bid, plus these fractions of the
              half-spread. The defaults are measured, not assumed — 233M real prints put
              the median concession near 0.87, harsher on sells. Mid fills (0) are banned
              by design; only 0.1% of real prints ever fill at mid or better.
            </span>
            {!costsDefault && (
              <button
                onClick={() =>
                  updateSettings({
                    commission: DEFAULT_SETTINGS.commission,
                    slippage: DEFAULT_SETTINGS.slippage,
                  })
                }
                className="shrink-0 rounded-[9px] border border-line px-3 py-1.5 text-[12.5px] text-ink-3 hover:border-line-hover hover:text-ink"
              >
                reset defaults
              </button>
            )}
          </div>
        </div>
      </div>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>APPEARANCE</div>
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <span className="text-[14.5px]">Mode</span>
            <div className="inline-flex gap-[2px] rounded-[11px] border border-line-soft p-[3px]">
              {(["market", "light", "dark"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => updateSettings({ theme: t })}
                  className={clsx(
                    "rounded-[9px] px-3.5 py-1.5 text-[13px] font-semibold capitalize",
                    settings.theme === t ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-2",
                  )}
                >
                  {THEME_LABEL[t]}
                </button>
              ))}
            </div>
          </div>
          {settings.theme === "market" && (
            <p className="text-[12.5px] leading-[1.55] text-ink-4">
              Market Hours follows the clock — light from 8am to 6pm New York time, dark
              after the close. Right now it’s showing{" "}
              <span className="font-mono text-ink-2">{resolved}</span>.
            </p>
          )}
          <div className="flex items-center justify-between">
            <span className="text-[14.5px]">Accent</span>
            <div className="flex items-center gap-2.5">
              {ACCENTS.map((a) => (
                <button
                  key={a}
                  onClick={() => updateSettings({ accent: a })}
                  title={ACCENT_PREVIEW[a].label}
                  aria-label={`Accent: ${ACCENT_PREVIEW[a].label}`}
                  className={clsx(
                    "flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px]",
                    settings.accent === a
                      ? "border-trust-border bg-trust-dim text-ink"
                      : "border-line text-ink-4 hover:border-line-hover hover:text-ink-2",
                  )}
                >
                  <span
                    className="inline-block h-[14px] w-[14px] rounded-full"
                    style={{ background: ACCENT_PREVIEW[a][resolved] }}
                  />
                  {ACCENT_PREVIEW[a].label}
                </button>
              ))}
            </div>
          </div>
          <p className="text-[12.5px] leading-[1.55] text-ink-4">
            The accent is the trust hue — verdicts, trust bands and controls. It never
            colors profit or loss, in either mode.
          </p>
        </div>
      </div>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>VERBIAGE COMPLEXITY</div>
        <div className="mb-3 inline-flex gap-[2px] rounded-[11px] border border-line-soft p-[3px]">
          {(["institutional", "retail"] as const).map((v) => (
            <button
              key={v}
              onClick={() => updateSettings({ verbiage: v })}
              className={clsx(
                "rounded-[9px] px-5 py-2 text-[14px] font-semibold capitalize",
                settings.verbiage === v ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-2",
              )}
            >
              {v}
            </button>
          ))}
        </div>
        <p className="text-[13.5px] leading-[1.6] text-ink-3">
          {settings.verbiage === "institutional"
            ? "Full quantitative language — Sharpe ratios, percentiles, out-of-sample splits, deflated statistics. For readers who live in this vocabulary."
            : "Plain English everywhere — verdicts, findings and recommendations rewritten for an everyday trader. Same numbers, same honesty, no jargon."}
        </p>
      </div>

      <div className={PANEL}>
        <div className={PANEL_TITLE}>SYSTEM STATUS</div>
        <div className="flex flex-col gap-2.5 text-[14.5px]">
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
          <Row
            label="Runs database"
            value={health?.db ?? "—"}
            dim={!!health?.db?.includes("fallback")}
          />
          <Row label="Backtest engine + gauntlet" value={health?.engine ?? "—"} />
          <Row label="NL parser" value={health?.parser ?? "—"} />
          <Row label="Verdict narration" value={health?.verdict_llm ?? "—"} />
          <Row label="Grounded Q&A" value={health?.ask ?? "—"} />
          <Row label="Model" value={health?.model ?? "—"} dim />
          <Row
            label="Minimum trades for a verdict"
            value={health?.min_trades != null ? String(health.min_trades) : "—"}
            dim
          />
          <Row label="Numeric validation" value="on — no un-computed numbers" />
          <Row label="Seeds" value="fixed & logged per run" />
        </div>
      </div>

      <div className="rounded-[14px] border border-dashed border-line-hover p-5 text-[13px] leading-[1.65] text-ink-3">
        Skeptic is a research instrument. It produces no recommendations to buy or sell any
        security. Backtests are computed on approximate, self-collected data and systematically
        overstate real-world results. Past performance does not predict future results.
      </div>
    </div>
  );
}
