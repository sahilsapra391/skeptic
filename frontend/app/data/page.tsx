"use client";

/**
 * Data Observatory — "Data, honestly." Mission telemetry for the lake:
 * collection streak, recorder heartbeat, per-source coverage lanes, and
 * named blind spots. Everything on this screen is computed from the live
 * /api/data/coverage payload; when the lake is unreachable the screen says
 * so instead of inventing numbers.
 */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { getCoverage } from "@/lib/api";
import { minutesAgo, monthYear, shortDate, year } from "@/lib/format";
import type { CoveragePayload } from "@/lib/types";

const PANEL = "rounded-[14px] border border-line bg-panel p-4";
const PANEL_TITLE = "font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

function laneGeometry(first: string, last: string, t0: string, t1: string) {
  const ms = (d: string) => new Date(d).getTime();
  const span = ms(t1) - ms(t0) || 1;
  const left = Math.max(0, ((ms(first) - ms(t0)) / span) * 100);
  const width = Math.max(1.5, ((ms(last) - ms(first)) / span) * 100);
  return { left: `${left.toFixed(1)}%`, width: `${Math.min(width, 100 - left).toFixed(1)}%` };
}

function Lane({
  label,
  first,
  last,
  t0,
  t1,
  note,
  dim,
}: {
  label: string;
  first: string;
  last: string;
  t0: string;
  t1: string;
  note: string;
  dim?: boolean;
}) {
  const geo = laneGeometry(first, last, t0, t1);
  return (
    <>
      <span>{label}</span>
      <div className="relative h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
        <div
          className={clsx("absolute h-full rounded-[3px]", dim ? "bg-ink-4" : "bg-trust")}
          style={{ left: geo.left, width: geo.width }}
        />
      </div>
      <span className={dim ? "text-ink-4" : "text-ink-3"}>{note}</span>
    </>
  );
}

export default function DataPage() {
  const [coverage, setCoverage] = useState<CoveragePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCoverage()
      .then(setCoverage)
      .catch((e) => setError(e instanceof Error ? e.message : "coverage unavailable"));
  }, []);

  if (error) {
    return (
      <div>
        <h1 className="mb-1 font-serif text-[32px] font-medium">Data, honestly</h1>
        <p className="mb-[22px] text-[15px] text-ink-3">
          Every verdict is bounded by this record. It grows nightly.
        </p>
        <div className="rounded-[14px] border border-warn/50 px-4 py-4">
          <div className="font-mono text-[12px] text-warn">telemetry unavailable</div>
          <div className="mt-2 text-[13px] leading-relaxed text-ink-3">{error}</div>
          <div className="mt-2 font-mono text-[11.5px] text-ink-4">
            This screen never shows cached or invented coverage — no lake, no numbers.
          </div>
        </div>
      </div>
    );
  }

  if (!coverage) {
    return (
      <div className="mt-16 text-center font-mono text-[12px] text-ink-4 animate-pin-pulse">
        reading the lake…
      </div>
    );
  }

  const today = coverage.generated_at.slice(0, 10);
  const recorder = coverage.intraday.cboe_delayed?.SPY;
  const recorderTs = recorder?.last_snapshot_ts;
  const recorderMins = recorderTs ? minutesAgo(recorderTs) : null;
  const recorderFresh = recorderMins != null && recorderMins <= 10;
  const recordFirst = coverage.eod.yahoo?.SPY?.first;
  const recordStaleDays = coverage.record_latest
    ? Math.floor(
        (new Date(today).getTime() - new Date(coverage.record_latest).getTime()) / 86_400_000,
      )
    : null;
  const dolthub = coverage.eod.dolthub?.SPY;
  const minute = coverage.minute_bars.SPY;
  const und = coverage.underlying.SPY;
  const vix = coverage.underlying.VIX;
  const t0 = "2020-01-01";

  const qualityFlags = Object.entries(coverage.quality?.tickers ?? {}).flatMap(([t, entry]) =>
    Object.entries(entry as Record<string, unknown>)
      .filter(([k, v]) => k.startsWith("flag_") && v === true)
      .map(([k]) => `${t} ${k.replace("flag_", "").replace(/_/g, " ")}`),
  );

  return (
    <div>
      <h1 className="mb-1 font-serif text-[32px] font-medium">Data, honestly</h1>
      <p className="mb-[22px] text-[15px] text-ink-3">
        Every verdict is bounded by this record. It grows nightly.
      </p>

      {recordStaleDays != null && recordStaleDays > 4 && (
        <div className="mb-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          ⚠ collector may be down — last EOD record {shortDate(coverage.record_latest!)} (
          {recordStaleDays} days ago)
        </div>
      )}

      <div className="grid grid-cols-[180px_1fr_1fr] gap-3">
        <div className={clsx(PANEL, "text-center")}>
          <div className="font-mono text-[40px] font-semibold tracking-[.06em]">
            {String(coverage.record_days).padStart(3, "0")}
          </div>
          <div className="mt-1 font-mono text-[9.5px] font-medium tracking-[.14em] text-ink-4">
            DAYS ON RECORD
          </div>
          <div className="mt-2 text-[11.5px] text-ink-3">
            {recordFirst ? `since ${shortDate(recordFirst)}` : "collection starting"}
          </div>
        </div>

        <div className={PANEL}>
          <div className={clsx(PANEL_TITLE, "mb-2")}>COLLECTOR HEARTBEAT</div>
          <svg width="100%" viewBox="0 0 260 40" className="block">
            <polyline
              points="0,20 40,20 48,6 56,34 64,20 110,20 118,6 126,34 134,20 180,20 188,6 196,34 204,20 260,20"
              fill="none"
              stroke={recorderFresh ? "var(--ac)" : "var(--ink-4)"}
              strokeWidth="1.6"
              // the dash is the traveling-pulse animation; when stalled it must
              // NOT clip the trace — a 240-unit dash on a ~372-unit path would
              // hide the right third and read as a half-drawn (buggy) line
              strokeDasharray={recorderFresh ? "240" : undefined}
              className={recorderFresh ? "animate-heartbeat" : undefined}
            />
          </svg>
          <div className="mt-2 font-mono text-[11px] text-ink-3">
            {recorderMins != null
              ? `recorder: last snapshot ${recorderMins} min ago ${recorderFresh ? "✓" : "· stalled"}`
              : "recorder: no snapshots yet"}
            {coverage.record_latest ? ` · EOD record ${shortDate(coverage.record_latest)}` : ""}
          </div>
        </div>

        <div className={PANEL}>
          <div className={clsx(PANEL_TITLE, "mb-2.5")}>SOURCES</div>
          <div className="flex flex-wrap gap-[7px]">
            {[
              ["yahoo eod", coverage.sources_status.yahoo_eod === true],
              ["dolthub archive", coverage.sources_status.dolthub_backfill === true],
              ["alpaca minute · frozen", coverage.sources_status.alpaca_minute === true],
              ["cboe recorder", coverage.sources_status.intraday_recorder === true],
            ].map(([label, ok]) => (
              <span
                key={String(label)}
                className={clsx(
                  "rounded-full border border-line px-[11px] py-1 font-mono text-[11px]",
                  ok ? "text-ink-3" : "text-ink-4",
                )}
              >
                {String(label)} {ok ? "✓" : "—"}
              </span>
            ))}
            <span className="rounded-full border border-line px-[11px] py-1 font-mono text-[11px] text-ink-4">
              alpha vantage · dormant
            </span>
          </div>
          <div className="mt-2.5 text-[11.5px] text-ink-3">
            {qualityFlags.length
              ? `quality: ${qualityFlags.join(" · ")} `
              : "quality: no open flags "}
            {qualityFlags.length > 0 && <span className="text-warn">⚠</span>}
          </div>
        </div>
      </div>

      <div className={clsx(PANEL, "mt-3")}>
        <div className={clsx(PANEL_TITLE, "mb-3")}>COVERAGE LANES — PER SOURCE, 2020 → NOW</div>
        <div className="grid grid-cols-[150px_1fr_290px] items-center gap-2.5 font-mono text-[11.5px]">
          {dolthub && (
            <Lane
              label="SPY EOD archive"
              first={dolthub.first}
              last={dolthub.last}
              t0={t0}
              t1={today}
              note={`${dolthub.sessions.toLocaleString()} verified · ${coverage.dolthub.quarantined} quarantined`}
            />
          )}
          {coverage.eod.yahoo?.SPY && (
            <Lane
              label="nightly record"
              first={coverage.eod.yahoo.SPY.first}
              last={today}
              t0={t0}
              t1={today}
              note={`source of record · since ${monthYear(coverage.eod.yahoo.SPY.first)}`}
            />
          )}
          {minute && (
            <Lane
              label="alpaca minute"
              first={minute.first}
              last={minute.last}
              t0={t0}
              t1={today}
              note={`${minute.sessions} sessions · frozen (OPRA entitlement)`}
              dim
            />
          )}
          {coverage.intraday.ivolatility?.SPY && (
            <Lane
              label="SPY 5-min NBBO"
              first={coverage.intraday.ivolatility.SPY.first}
              last={coverage.intraday.ivolatility.SPY.last}
              t0={t0}
              t1={today}
              note={`${coverage.intraday.ivolatility.SPY.sessions.toLocaleString()} sessions · short-DTE ATM slice`}
            />
          )}
          {recorder && (
            <Lane
              label="quote recorder"
              first={recorder.first}
              last={today}
              t0={t0}
              t1={today}
              note="minute quotes · best-effort uptime"
            />
          )}
        </div>
      </div>

      <div className={clsx(PANEL, "mt-3")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>COVERAGE</div>
        <div className="flex flex-col gap-1.5 font-mono text-[12px] text-ink-3">
          {und && (
            <div className="flex justify-between">
              <span>underlying daily (SPY/QQQ/IWM{vix ? " + VIX" : ""})</span>
              <span className="text-ink">
                {year(und.first)} → now · {und.rows.toLocaleString()} rows
              </span>
            </div>
          )}
          {(["SPY", "QQQ", "IWM"] as const).map((t) => {
            const c = coverage.chains[t];
            return (
              <div key={t} className="flex justify-between">
                <span>{t} eod option chains</span>
                {c ? (
                  <span className={t === "SPY" ? "text-trust" : "text-ink"}>
                    {monthYear(c.first)} → now · {c.sessions.toLocaleString()} sessions
                  </span>
                ) : (
                  <span className="text-ink-4">none yet</span>
                )}
              </div>
            );
          })}
          {minute && (
            <div className="flex justify-between">
              <span>minute option bars (alpaca)</span>
              <span className="text-ink">
                {monthYear(minute.first)} → {monthYear(minute.last)} · frozen
              </span>
            </div>
          )}
          {recorder && (
            <div className="flex justify-between">
              <span>intraday quotes (cboe recorder, best-effort)</span>
              <span className="text-ink">{monthYear(recorder.first)} → now · gaps shown</span>
            </div>
          )}
        </div>
      </div>

      {coverage.chain_quality?.SPY && (
        <div className={clsx(PANEL, "mt-3")}>
          <div className={clsx(PANEL_TITLE, "mb-2.5")}>
            CHAIN QUALITY — FIELD COMPLETENESS PER SOURCE
          </div>
          <div className="flex flex-col gap-2.5 font-mono text-[11.5px]">
            {(["SPY", "QQQ", "IWM"] as const).map((t) => {
              const q = coverage.chain_quality?.[t];
              if (!q) return null;
              return Object.entries(q.sources).map(([source, s]) => (
                <div key={`${t}-${source}`} className="grid grid-cols-[130px_1fr] gap-2.5">
                  <span className="text-ink-3">
                    {t} · {source}
                  </span>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-ink-4">
                    <span className="text-ink-3">{s.rows.toLocaleString()} rows</span>
                    {Object.entries(s.fields)
                      .filter(([f]) =>
                        ["iv", "delta", "vega", "volume", "open_interest"].includes(f),
                      )
                      .map(([f, share]) => (
                        <span key={f} className={share < 0.5 ? "text-warn" : undefined}>
                          {f.replace("open_interest", "oi")} {Math.round(share * 100)}%
                        </span>
                      ))}
                  </div>
                </div>
              ));
            })}
          </div>
          {coverage.chain_quality.SPY.monthly_median_spread_pct && (
            <div className="mt-3 border-t border-line-softer pt-2.5">
              <div className={clsx(PANEL_TITLE, "mb-1.5")}>
                SPY MEDIAN SPREAD BY MONTH — % OF MID
              </div>
              <svg width="100%" viewBox="0 0 860 46" className="block" preserveAspectRatio="none">
                {(() => {
                  const months = coverage.chain_quality.SPY.monthly_median_spread_pct!;
                  const hi = Math.max(...months.map((m) => m.v), 1);
                  const w = 860 / months.length;
                  return months.map((m, i) => (
                    <rect
                      key={m.month}
                      x={(i * w + 0.5).toFixed(1)}
                      y={(44 - (m.v / hi) * 40).toFixed(1)}
                      width={Math.max(w - 1, 0.8).toFixed(1)}
                      height={((m.v / hi) * 40 + 2).toFixed(1)}
                      fill="var(--ink-4)"
                      opacity="0.75"
                    >
                      <title>{`${m.month} · ${m.v.toFixed(1)}%`}</title>
                    </rect>
                  ));
                })()}
              </svg>
              <div className="mt-1 font-mono text-[10px] text-ink-4">
                computed from the engine&apos;s local chain cache — appears after the first
                lake load
              </div>
            </div>
          )}
        </div>
      )}

      {coverage.ivol_analytics?.SPY?.ivx && (
        <div className={clsx(PANEL, "mt-3")}>
          <div className={clsx(PANEL_TITLE, "mb-3")}>
            IV ANALYTICS (IVOLATILITY) — IVX / HV, BANKED YEARS
          </div>
          <div className="grid grid-cols-[150px_1fr_290px] items-center gap-2.5 font-mono text-[11.5px]">
            {(["SPY", "QQQ", "IWM"] as const).map((t) => {
              const ivx = coverage.ivol_analytics?.[t]?.ivx;
              if (!ivx) return null;
              return (
                <Lane
                  key={t}
                  label={`${t} IVX 30d`}
                  first={`${ivx.first}-01-01`}
                  last={`${ivx.last}-12-31`}
                  t0="2005-01-01"
                  t1={today}
                  note={`${ivx.years} years · powers ivx_rank / hv-iv spread filters`}
                />
              );
            })}
          </div>
        </div>
      )}

      <div className={clsx(PANEL, "mt-3")}>
        <div className={clsx(PANEL_TITLE, "mb-2.5")}>NAMED BLIND SPOTS</div>
        <div className="flex flex-col gap-1.5">
          {coverage.blind_spots.map((b) => (
            <div key={b.id} className="flex gap-2 text-[12.5px] leading-[1.55] text-ink-2">
              <span className="text-ink-4">·</span>
              <span>{b.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
