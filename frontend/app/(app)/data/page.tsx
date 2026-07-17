"use client";

/**
 * Data Observatory — "Data, honestly." Mission telemetry for the lake:
 * collection streak, recorder heartbeat, per-source coverage lanes, and
 * named blind spots. Everything on this screen is computed from the live
 * /api/data/coverage payload; when the lake is unreachable the screen says
 * so instead of inventing numbers.
 *
 * UX Chunk C (owner plan 2026-07-14): the former panel wall is regrouped
 * into five collapsible groups — Coverage at a glance (open by default),
 * EOD chains & history, Intraday & minute lakes, Signal sources, and Data
 * health & incidents. NO data was removed: every fact is reachable within
 * one expand. Anything flagged lifts a warn badge onto its group header so
 * problems stay visible while collapsed. Expand state persists locally.
 */

import { Fragment, useEffect, useState } from "react";
import clsx from "clsx";

import { getCoverage } from "@/lib/api";
import { daysOnRecord, oldestDataFirst } from "@/lib/coverage-facts";
import { minutesAgo, monthYear, shortDate, year } from "@/lib/format";
import type { CoveragePayload } from "@/lib/types";

// live telemetry: re-pull the coverage payload on this cadence so lake
// changes appear without a manual refresh. The backend snapshot itself
// refreshes behind a 300s TTL, so polling much faster only re-downloads
// identical JSON; hidden tabs skip the tick entirely and re-pull on
// becoming visible (review finding: an idle background tab must not
// drive nightly-scale R2 reads).
const POLL_MS = 120_000;

const PANEL = "rounded-[14px] border border-line bg-panel p-4";
const PANEL_TITLE = "font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

const TICKERS = ["SPY", "QQQ", "IWM"] as const;
// the standard lane grid, shared by every group's lane section
const LANE_GRID =
  "grid grid-cols-[150px_1fr_290px] items-center gap-2.5 font-mono text-[11.5px]";
// chain-quality warn rule — ONE definition shared by the detail rows and
// the EOD group badge, so a threshold tune can never make them disagree
const CHAIN_FIELDS = ["iv", "delta", "vega", "volume", "open_interest"];
const WEAK_FIELD_SHARE = 0.5;

// group expand state persists locally; "glance" is the only group open on
// a first visit (owner plan: coverage at a glance stays at the top,
// everything else is one click away). The union type ties each <Group id>
// to its openGroups key — a renamed/typo'd id fails the compile instead of
// silently never persisting.
type GroupId = "glance" | "eod" | "intraday" | "signals" | "health";
const GROUPS_KEY = "skeptic-observatory-groups";
const DEFAULT_OPEN: Partial<Record<GroupId, boolean>> = { glance: true };

// heartbeat waveform, edge to edge of the 260-wide viewBox (~372 path units)
const HB_POINTS =
  "0,20 40,20 48,6 56,34 64,20 110,20 118,6 126,34 134,20 180,20 188,6 196,34 204,20 260,20";

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

/** One collapsible category group. The header is the one-line summary
 * (headline numbers) + any lifted warn badge; the body holds the former
 * standalone panels as bordered sections. */
function Group({
  id,
  title,
  summary,
  badge,
  open,
  onToggle,
  children,
}: {
  id: GroupId;
  title: string;
  summary: string;
  badge?: string | null;
  open: boolean;
  onToggle: (id: GroupId) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3 rounded-[14px] border border-line bg-panel">
      <button
        onClick={() => onToggle(id)}
        aria-expanded={open}
        aria-controls={`obs-group-${id}`}
        className="flex w-full items-baseline gap-3 px-4 py-3 text-left hover:bg-raised/40"
      >
        <span
          aria-hidden="true"
          className={clsx(
            "inline-block font-mono text-[9px] text-ink-4 transition-transform",
            open && "rotate-90",
          )}
        >
          ▶
        </span>
        <span className="font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-3">
          {title}
        </span>
        {badge && (
          <span className="whitespace-nowrap font-mono text-[10.5px] text-warn">⚠ {badge}</span>
        )}
        <span className="ml-auto min-w-0 truncate font-mono text-[11px] text-ink-4">
          {summary}
        </span>
      </button>
      {open && (
        <div id={`obs-group-${id}`} className="px-4 pb-4">
          {children}
        </div>
      )}
    </div>
  );
}

/** A former standalone panel, now a bordered section inside its group. */
function Section({
  title,
  note,
  children,
}: {
  title?: string;
  note?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3 border-t border-line-softer pt-3 first:mt-0 first:border-t-0 first:pt-0">
      {title && <div className={clsx(PANEL_TITLE, "mb-2.5")}>{title}</div>}
      {note && <div className="mb-3 text-[11.5px] leading-[1.5] text-ink-4">{note}</div>}
      {children}
    </div>
  );
}

export default function DataPage() {
  const [coverage, setCoverage] = useState<CoveragePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openGroups, setOpenGroups] =
    useState<Partial<Record<GroupId, boolean>>>(DEFAULT_OPEN);

  useEffect(() => {
    // hydrate the remembered expand state (client-only — localStorage in
    // render would mismatch the server HTML, same pattern as the hero).
    // Keep only boolean values from a plain object: corrupt/legacy state
    // (arrays, truthy non-booleans) would otherwise swallow the first
    // toggle click and persist garbage keys back (review finding).
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(GROUPS_KEY) ?? "null");
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        const clean = Object.fromEntries(
          Object.entries(saved).filter(([, v]) => typeof v === "boolean"),
        );
        setOpenGroups({ ...DEFAULT_OPEN, ...clean });
      }
    } catch {
      /* private mode — keep defaults */
    }
  }, []);

  const toggleGroup = (id: GroupId) =>
    setOpenGroups((g) => {
      const next = { ...g, [id]: !g[id] };
      try {
        localStorage.setItem(GROUPS_KEY, JSON.stringify(next));
      } catch {
        /* private mode — state lives for the session only */
      }
      return next;
    });

  useEffect(() => {
    let alive = true;
    const pull = (fresh: boolean) =>
      getCoverage(fresh)
        .then((c) => {
          if (!alive) return;
          setCoverage(c);
          setError(null);
        })
        .catch((e) => {
          if (alive) setError(e instanceof Error ? e.message : "coverage unavailable");
        });
    pull(false);
    const tick = () => {
      if (!document.hidden) pull(true);
    };
    const id = setInterval(tick, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) pull(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  // a failed POLL keeps showing the last real payload (its generated_at
  // discloses the age); only a data-less screen shows the error state
  if (error && !coverage) {
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
  const closeChain = coverage.eod.cboe_eod?.SPY;
  const recordFirst =
    coverage.record_first ?? closeChain?.first ?? coverage.eod.yahoo?.SPY?.first;
  // frozen vs accruing is the BACKEND's one verdict (it also writes the
  // blind-spot text) — re-deriving it here could contradict that panel
  const alpacaAccruing = coverage.sources_status.alpaca_minute_accruing === true;
  const inhouse = coverage.inhouse_signals?.SPY;
  const hvAgreement =
    coverage.cross_validation?.hv_inhouse_vs_ivol?.SPY?.agreement_rate ?? null;
  const telemetryMins = minutesAgo(coverage.generated_at);
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

  // ---- group summaries (headline numbers) + lifted badges --------------
  const spyChain = coverage.chains.SPY;
  const laterStart = coverage.chains.QQQ?.first ?? coverage.chains.IWM?.first;
  const eodSummary = spyChain
    ? `SPY ${spyChain.sessions.toLocaleString()} sessions since ${monthYear(spyChain.first)}` +
      (laterStart ? ` · QQQ/IWM since ${monthYear(laterStart)}` : "")
    : "no chains banked yet";
  // any chain source with a load-bearing field under the shared threshold
  // lifts a badge — the SAME constants the detail rows paint warn with, so
  // the badge and the rows can never disagree. Null-safe: this runs in the
  // component body on every payload, warn-worthy or not (review finding).
  const weakChainFields = TICKERS.flatMap((t) =>
    Object.values(coverage.chain_quality?.[t]?.sources ?? {}).flatMap((s) =>
      Object.entries(s?.fields ?? {}).filter(
        ([f, share]) => CHAIN_FIELDS.includes(f) && share < WEAK_FIELD_SHARE,
      ),
    ),
  );

  const ivol5m = coverage.intraday.ivolatility?.SPY;
  const intradaySummary = [
    ivol5m ? `5-min NBBO ${ivol5m.sessions.toLocaleString()} sessions` : null,
    `alpaca ${alpacaAccruing ? "accruing" : "frozen"}`,
    recorderMins != null ? `recorder ${recorderFresh ? "live" : "stalled"}` : "recorder off",
  ]
    .filter(Boolean)
    .join(" · ");

  const uwFamilies = Object.keys(coverage.new_sources?.uw_daily ?? {}).length;
  const signalsSummary = [
    coverage.ivol_analytics?.SPY?.ivx ? `IVX ${coverage.ivol_analytics.SPY.ivx.years}y` : null,
    coverage.ivs_signals?.SPY ? "vol surfaces" : null,
    uwFamilies ? `${uwFamilies} UW families` : null,
    inhouse ? "in-house continuations" : null,
  ]
    .filter(Boolean)
    .join(" · ") || "banking begins nightly";

  const xvalPairs = Object.keys(coverage.cross_validation ?? {}).length;
  const healthSummary = [
    xvalPairs ? `${xvalPairs} x-val pairs` : null,
    coverage.dolthub ? `${coverage.dolthub.quarantined} quarantined` : null,
    qualityFlags.length ? `${qualityFlags.length} open flags` : "no open flags",
    `${coverage.blind_spots.length} blind spots`,
  ]
    .filter(Boolean)
    .join(" · ");
  const healthBadge =
    recordStaleDays != null && recordStaleDays > 4
      ? "collector stale"
      : qualityFlags.length
        ? `${qualityFlags.length} flag${qualityFlags.length === 1 ? "" : "s"}`
        : null;

  const glanceSummary = `${coverage.record_days} days on record · recorder ${
    recorderFresh ? "live" : recorderMins != null ? "stalled" : "off"
  }`;

  return (
    <div>
      <h1 className="mb-1 font-serif text-[32px] font-medium">Data, honestly</h1>
      <p className="mb-[22px] text-[15px] text-ink-3">
        Every verdict is bounded by this record. It grows nightly.
        <span className="ml-2 font-mono text-[11px] text-ink-4">
          telemetry {telemetryMins != null && telemetryMins > 0
            ? `${telemetryMins} min old`
            : "live"}{" "}
          · auto-refreshes
        </span>
      </p>

      {recordStaleDays != null && recordStaleDays > 4 && (
        <div className="mb-3 rounded-xl border border-warn/50 px-3.5 py-3 font-mono text-[12px] text-warn">
          ⚠ collector may be down — last EOD record {shortDate(coverage.record_latest!)} (
          {recordStaleDays} days ago)
        </div>
      )}

      {/* ------------------------------------------- 1 · coverage at a glance */}
      <Group
        id="glance"
        title="COVERAGE AT A GLANCE"
        summary={glanceSummary}
        open={openGroups.glance === true}
        onToggle={toggleGroup}
      >
        <div className="grid grid-cols-[180px_1fr_1fr] gap-3">
          <div className={clsx(PANEL, "text-center")}>
            {/* owner 2026-07-17: counts from the OLDEST banked session
                (chains reach Oct '09), not the young nightly streak */}
            <div className="font-mono text-[40px] font-semibold tracking-[.06em]">
              {daysOnRecord(coverage)?.toLocaleString("en-US") ?? "—"}
            </div>
            <div className="mt-1 font-mono text-[9.5px] font-medium tracking-[.14em] text-ink-4">
              DAYS ON RECORD
            </div>
            <div className="mt-2 text-[11.5px] text-ink-3">
              {oldestDataFirst(coverage)
                ? `since ${shortDate(oldestDataFirst(coverage)!)}`
                : "collection starting"}
            </div>
          </div>

          <div className={PANEL}>
            <div className={clsx(PANEL_TITLE, "mb-2")}>COLLECTOR HEARTBEAT</div>
            <svg width="100%" viewBox="0 0 260 40" className="block">
              {/* the full waveform, always drawn edge to edge so the trace fills
                  the panel; a clipping dash here would leave a blank third and
                  read as a half-drawn (buggy) line */}
              <polyline
                points={HB_POINTS}
                fill="none"
                stroke={recorderFresh ? "var(--ac)" : "var(--ink-4)"}
                strokeWidth="1.6"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {/* live: a bright pulse sweeps the full line (the "beat") — a short
                  dash traveling the whole path, over the fully-drawn base */}
              {recorderFresh && (
                <polyline
                  points={HB_POINTS}
                  fill="none"
                  stroke="var(--ac)"
                  strokeWidth="3.2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  strokeDasharray="26 346"
                  className="animate-heartbeat"
                />
              )}
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
                ["cboe close chain", coverage.sources_status.cboe_eod === true],
                ["yahoo eod", coverage.sources_status.yahoo_eod === true],
                ["dolthub archive", coverage.sources_status.dolthub_backfill === true],
                [
                  `alpaca minute · ${alpacaAccruing ? "accruing" : "frozen"}`,
                  coverage.sources_status.alpaca_minute === true,
                ],
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

        {(coverage.resolution_mix?.SPY ||
          coverage.resolution_mix?.QQQ ||
          coverage.resolution_mix?.IWM) && (
          <Section
            title="RESOLUTION MIX — FINEST HONEST DECISION CLOCK PER SESSION"
            note="rebuilt nightly from the lake; minute bars upgrade the clock only —
            fills always quote from real NBBO (5-min iVol / recorder)"
          >
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
                const mix = coverage.resolution_mix?.[t];
                if (!mix) return null;
                const clockNote = [
                  `minute ${(mix.clock.minute ?? 0).toLocaleString()}`,
                  `5-min ${(mix.clock.five_min ?? 0).toLocaleString()}`,
                  `daily ${(mix.clock.none ?? 0).toLocaleString()}`,
                ].join(" · ");
                return (
                  <Fragment key={t}>
                    <span>{t}</span>
                    <div className="relative h-[9px] overflow-hidden rounded-[3px] bg-line-softer">
                      {mix.timeline.map((run, i) => {
                        const geo = laneGeometry(run.first, run.last, "2007-01-01", today);
                        const tone =
                          run.clock === "minute"
                            ? "bg-trust"
                            : run.clock === "five_min"
                              ? "bg-trust/40"
                              : "bg-line-hover";
                        return (
                          <div
                            key={i}
                            className={clsx("absolute h-full", tone)}
                            style={{ left: geo.left, width: geo.width }}
                            title={`${run.first} → ${run.last} · ${run.sessions} sessions · clock ${run.clock} · quote ${run.quote}`}
                          />
                        );
                      })}
                    </div>
                    <span className="text-ink-3">{clockNote}</span>
                  </Fragment>
                );
              })}
            </div>
            <div className="mt-2.5 flex items-center gap-4 font-mono text-[10.5px] text-ink-4">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-[7px] w-[14px] rounded-[2px] bg-trust" />
                minute clock
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-[7px] w-[14px] rounded-[2px] bg-trust/40" />
                5-min clock
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-[7px] w-[14px] rounded-[2px] bg-line-hover" />
                daily only
              </span>
            </div>
          </Section>
        )}
      </Group>

      {/* --------------------------------------------- 2 · EOD chains & history */}
      <Group
        id="eod"
        title="EOD CHAINS & HISTORY"
        summary={eodSummary}
        badge={weakChainFields.length ? "chain fields" : null}
        open={openGroups.eod === true}
        onToggle={toggleGroup}
      >
        <Section title="COVERAGE LANES — PER SOURCE, 2020 → NOW">
          <div className={LANE_GRID}>
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
                note={`yahoo snapshot · since ${monthYear(coverage.eod.yahoo.SPY.first)}`}
              />
            )}
            {closeChain && (
              <Lane
                label="close chain (cboe)"
                first={closeChain.first}
                last={closeChain.last}
                t0={t0}
                t1={today}
                note={`${closeChain.sessions} sessions · latest ${shortDate(closeChain.last)} · full chain + greeks, ~15-min delayed feed`}
              />
            )}
          </div>
        </Section>

        <Section title="COVERAGE">
          <div className="flex flex-col gap-1.5 font-mono text-[12px] text-ink-3">
            {und && (
              <div className="flex justify-between">
                <span>underlying daily (SPY/QQQ/IWM{vix ? " + VIX" : ""})</span>
                <span className="text-ink">
                  {year(und.first)} → now · {und.rows.toLocaleString()} rows
                </span>
              </div>
            )}
            {TICKERS.map((t) => {
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
          </div>
        </Section>

        {/* gated on ANY ticker's quality — the badge above scans all three,
            so it must never point at a section that then fails to render
            (review finding: SPY-only gate vs all-ticker badge) */}
        {(coverage.chain_quality?.SPY ||
          coverage.chain_quality?.QQQ ||
          coverage.chain_quality?.IWM) && (
          <Section title="CHAIN QUALITY — FIELD COMPLETENESS PER SOURCE">
            <div className="flex flex-col gap-2.5 font-mono text-[11.5px]">
              {TICKERS.map((t) => {
                const q = coverage.chain_quality?.[t];
                if (!q) return null;
                return Object.entries(q.sources).map(([source, s]) => (
                  <div key={`${t}-${source}`} className="grid grid-cols-[130px_1fr] gap-2.5">
                    <span className="text-ink-3">
                      {t} · {source}
                    </span>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-ink-4">
                      <span className="text-ink-3">{s.rows.toLocaleString()} rows</span>
                      {Object.entries(s?.fields ?? {})
                        .filter(([f]) => CHAIN_FIELDS.includes(f))
                        .map(([f, share]) => (
                          <span
                            key={f}
                            className={share < WEAK_FIELD_SHARE ? "text-warn" : undefined}
                          >
                            {f.replace("open_interest", "oi")} {Math.round(share * 100)}%
                          </span>
                        ))}
                    </div>
                  </div>
                ));
              })}
            </div>
            {coverage.chain_quality?.SPY?.monthly_median_spread_pct && (
              <div className="mt-3 border-t border-line-softer pt-2.5">
                <div className={clsx(PANEL_TITLE, "mb-1.5")}>
                  SPY MEDIAN SPREAD BY MONTH — % OF MID
                </div>
                <svg width="100%" viewBox="0 0 860 46" className="block" preserveAspectRatio="none">
                  {(() => {
                    const months = coverage.chain_quality?.SPY?.monthly_median_spread_pct ?? [];
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
          </Section>
        )}
      </Group>

      {/* ------------------------------------------ 3 · intraday & minute lakes */}
      <Group
        id="intraday"
        title="INTRADAY & MINUTE LAKES"
        summary={intradaySummary}
        badge={recorderMins != null && !recorderFresh ? "recorder stalled" : null}
        open={openGroups.intraday === true}
        onToggle={toggleGroup}
      >
        <Section title="COVERAGE LANES — PER SOURCE, 2020 → NOW">
          <div className={LANE_GRID}>
            {minute && (
              <Lane
                label="alpaca minute"
                first={minute.first}
                last={minute.last}
                t0={t0}
                t1={today}
                note={`${minute.sessions} sessions · ${
                  alpacaAccruing ? "accruing nightly" : "frozen (OPRA entitlement)"
                }`}
                dim
              />
            )}
            {ivol5m && (
              <Lane
                label="SPY 5-min NBBO"
                first={ivol5m.first}
                last={ivol5m.last}
                t0={t0}
                t1={today}
                note={`${ivol5m.sessions.toLocaleString()} sessions · short-DTE ATM slice`}
              />
            )}
            {recorder && (
              <Lane
                label="quote recorder"
                first={recorder.first}
                last={today}
                t0={t0}
                t1={today}
                note={`minute quotes · best-effort uptime${
                  recorderMins != null && !recorderFresh
                    ? ` · last snapshot ${recorderMins} min ago — stalled`
                    : ""
                }`}
              />
            )}
          </div>
        </Section>

        <Section title="COVERAGE">
          <div className="flex flex-col gap-1.5 font-mono text-[12px] text-ink-3">
            {minute && (
              <div className="flex justify-between">
                <span>minute option bars (alpaca)</span>
                <span className="text-ink">
                  {monthYear(minute.first)} → {monthYear(minute.last)} ·{" "}
                  {alpacaAccruing ? "accruing" : "frozen"}
                </span>
              </div>
            )}
            {recorder && (
              <div className="flex justify-between">
                <span>intraday quotes (cboe recorder, best-effort)</span>
                <span className="text-ink">{monthYear(recorder.first)} → now · gaps shown</span>
              </div>
            )}
            {/* gated like the old new-sources bullet: whenever new_sources
                exists — a per-ticker "pending" is itself a fact worth
                showing (review finding: the tighter uw_minute gate silently
                dropped the whole line in the not-yet-flowing state) */}
            {coverage.new_sources && (
              <div className="flex justify-between">
                <span>UW 1-min contract bars (trade candles, clock/validation only)</span>
                <span className="text-ink">
                  {TICKERS.map((t) => {
                    const w = coverage.new_sources?.uw_minute?.[t];
                    return `${t} ${w ? `${w.sessions.toLocaleString()} sessions` : "pending"}`;
                  }).join(" · ")}
                </span>
              </div>
            )}
          </div>
        </Section>
      </Group>

      {/* --------------------------------------------------- 4 · signal sources */}
      <Group
        id="signals"
        title="SIGNAL SOURCES"
        summary={signalsSummary}
        open={openGroups.signals === true}
        onToggle={toggleGroup}
      >
        {coverage.ivol_analytics?.SPY?.ivx && (
          <Section title="IV ANALYTICS (IVOLATILITY) — IVX / HV, BANKED YEARS">
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
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
          </Section>
        )}

        {(coverage.ivs_signals?.SPY ||
          coverage.ivs_signals?.QQQ ||
          coverage.ivs_signals?.IWM) && (
          <Section
            title="VOL-SURFACE SIGNALS — 25Δ SKEW / 30v90 TERM SLOPE, DERIVED NIGHTLY"
            note="derived once per session from the fitted IVS surface; sessions
            missing a tenor or delta bracket carry no value for that signal —
            filters read them as unavailable, never interpolated"
          >
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
                const sig = coverage.ivs_signals?.[t];
                if (!sig) return null;
                return (
                  <Lane
                    key={t}
                    label={`${t} surface`}
                    first={sig.first}
                    last={sig.last}
                    t0="2007-01-01"
                    t1={today}
                    note={`skew ${sig.skew_sessions.toLocaleString()} · term ${sig.term_sessions.toLocaleString()} of ${sig.sessions.toLocaleString()} sessions`}
                  />
                );
              })}
            </div>
          </Section>
        )}

        {(coverage.dealer_positioning?.SPY ||
          coverage.dealer_positioning?.QQQ ||
          coverage.dealer_positioning?.IWM) && (
          <Section
            title="DEALER POSITIONING (UW) — NET GEX / DEX, SIGN + RANK VOCABULARY"
            note="vendor units are opaque — filters read the sign (long/short
            gamma) and the trailing-year percentile only; runs conditioned
            on this family refuse windows starting before the first banked
            session"
          >
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
                const dp = coverage.dealer_positioning?.[t];
                if (!dp) return null;
                return (
                  <Lane
                    key={t}
                    label={`${t} exposure`}
                    first={dp.first}
                    last={dp.last}
                    t0="2025-01-01"
                    t1={today}
                    note={`gex ${dp.gex_sessions.toLocaleString()} · dex ${dp.dex_sessions.toLocaleString()} of ${dp.sessions.toLocaleString()} sessions`}
                  />
                );
              })}
            </div>
          </Section>
        )}

        {(coverage.flow_signals?.SPY ||
          coverage.flow_signals?.QQQ ||
          coverage.flow_signals?.IWM) && (
          <Section
            title="FLOW / SENTIMENT / PIN (UW) — EOD REDUCTIONS, SPEC v7"
            note="net premium & NOPE read as sign/rank only; put/call ratio and
            max-pain distance are unit-free; market tide is market-wide.
            runs conditioned on this family refuse windows starting before
            the first derived session"
          >
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
                const fs = coverage.flow_signals?.[t];
                if (!fs) return null;
                return (
                  <Lane
                    key={t}
                    label={`${t} flow`}
                    first={fs.first}
                    last={fs.last}
                    t0="2026-01-01"
                    t1={today}
                    note={`flow ${fs.net_premium_sessions.toLocaleString()} · nope ${fs.nope_sessions.toLocaleString()} · max-pain ${fs.max_pain_sessions.toLocaleString()} of ${fs.sessions.toLocaleString()}`}
                  />
                );
              })}
              {coverage.market_tide && (
                <Lane
                  label="MARKET tide"
                  first={coverage.market_tide.first}
                  last={coverage.market_tide.last}
                  t0="2026-01-01"
                  t1={today}
                  note={`market-wide · ${coverage.market_tide.tide_sessions.toLocaleString()} sessions`}
                />
              )}
            </div>
          </Section>
        )}

        {(coverage.inhouse_signals?.SPY ||
          coverage.inhouse_signals?.QQQ ||
          coverage.inhouse_signals?.IWM) && (
          <Section
            title="IN-HOUSE CONTINUATIONS — FORWARD RECORD, NO VENDOR SUBSCRIPTIONS"
            note={
              <>
                derived nightly from the CBOE close chain and our own dailies
                {coverage.vendor_lasts?.ivs
                  ? ` — iVol series last observed ${coverage.vendor_lasts.ivs}`
                  : ""}
                {coverage.vendor_lasts?.uw
                  ? `, UW families ${coverage.vendor_lasts.uw}`
                  : ""}
                . vendor history stays untouched; runs crossing a seam disclose
                it, and the cross-validation pairs (Data health group) measure
                every continuation on its overlap. GEX/DEX are banked +
                sign-checked only — never spliced into the UW series.
              </>
            }
          >
            <div className={LANE_GRID}>
              {TICKERS.map((t) => {
                const ih = coverage.inhouse_signals?.[t];
                if (!ih?.first || !ih.last || !ih.sessions) return null;
                return (
                  <Lane
                    key={t}
                    label={`${t} chain signals`}
                    first={ih.first}
                    last={ih.last}
                    t0="2026-01-01"
                    t1={today}
                    note={`skew ${ih.skew_sessions ?? 0} · atm-iv ${ih.atm_sessions ?? 0} · gex ${ih.gex_sessions ?? 0} · pcr ${ih.pcr_sessions ?? 0} of ${ih.sessions}`}
                  />
                );
              })}
              {inhouse?.hv && (
                <Lane
                  label="SPY HV 30d"
                  first={inhouse.hv.first}
                  last={inhouse.hv.last}
                  t0="2005-01-01"
                  t1={today}
                  note={`${inhouse.hv.sessions.toLocaleString()} sessions from our own dailies${
                    hvAgreement != null
                      ? ` · ${(hvAgreement * 100).toFixed(1)}% vendor-overlap agreement`
                      : ""
                  }`}
                />
              )}
            </div>
          </Section>
        )}

        {coverage.new_sources && (
          <Section
            title={`NEW SIGNAL SOURCES (ENGINE-V4) · banked, not yet consumed · ${(coverage.new_sources.generated_at ?? "").slice(0, 10)}`}
          >
            <div className="flex flex-col gap-1.5 text-[12.5px] leading-[1.55] text-ink-2">
              <div className="flex gap-2">
                <span className="text-ink-4">·</span>
                <span>
                  Unusual Whales signal families:{" "}
                  <span className="font-mono">{uwFamilies}</span> banked per session
                  {coverage.new_sources.uw_daily?.market_tide?.market && (
                    <span className="text-ink-4">
                      {" "}
                      — {coverage.new_sources.uw_daily.market_tide.market.first} →{" "}
                      {coverage.new_sources.uw_daily.market_tide.market.last}
                    </span>
                  )}
                </span>
              </div>
              <div className="flex gap-2">
                <span className="text-ink-4">·</span>
                <span>
                  iVol IVS vol surfaces:{" "}
                  {coverage.new_sources.ivs?.SPY ? (
                    <span className="font-mono">
                      {coverage.new_sources.ivs.SPY.sessions.toLocaleString()} sessions ·{" "}
                      {coverage.new_sources.ivs.SPY.first} → {coverage.new_sources.ivs.SPY.last}
                    </span>
                  ) : (
                    "pending"
                  )}
                </span>
              </div>
              <div className="flex gap-2">
                <span className="text-ink-4">·</span>
                <span>
                  Massive OHLCV aggregates (coverage cross-check, never fills):{" "}
                  {TICKERS.map((t, i) => (
                    <span key={t}>
                      {i > 0 && " · "}
                      {t}{" "}
                      <span className="font-mono">
                        {(coverage.new_sources?.massive?.[t]?.agg_symbols ?? 0).toLocaleString()}{" "}
                        contracts
                      </span>
                    </span>
                  ))}
                </span>
              </div>
            </div>
          </Section>
        )}
      </Group>

      {/* -------------------------------------------- 5 · data health & incidents */}
      <Group
        id="health"
        title="DATA HEALTH & INCIDENTS"
        summary={healthSummary}
        badge={healthBadge}
        open={openGroups.health === true}
        onToggle={toggleGroup}
      >
        {coverage.cross_validation &&
          Object.keys(coverage.cross_validation).length > 0 && (
            <Section
              title="CROSS-SOURCE VALIDATION — INDEPENDENT VENDORS, PER-PAIR AGREEMENT"
              note="agreement rates travel with their audited denominators — reported,
              never scored; thresholds are earned from this history, not invented"
            >
              <div className="grid grid-cols-[200px_1fr_290px] items-center gap-2.5 font-mono text-[11.5px]">
                {Object.entries(coverage.cross_validation).flatMap(([pair, byT]) =>
                  Object.entries(byT).map(([t, r]) => (
                    <Lane
                      key={`${pair}-${t}`}
                      label={`${t} ${pair.replace(/_/g, " ")}`}
                      first={r.first}
                      last={r.last}
                      t0="2024-01-01"
                      t1={today}
                      note={`${r.agreement_rate != null ? (r.agreement_rate * 100).toFixed(1) + "% of " : ""}${r.checked.toLocaleString()} checked · ${r.sessions.toLocaleString()} sessions`}
                    />
                  )),
                )}
              </div>
            </Section>
          )}

        <Section title="OPEN QUALITY FLAGS">
          <div className="font-mono text-[12px] text-ink-3">
            {qualityFlags.length ? (
              <span>
                {qualityFlags.join(" · ")} <span className="text-warn">⚠</span>
              </span>
            ) : (
              "no open flags"
            )}
            {coverage.dolthub && (
              <span className="text-ink-4">
                {" "}
                · {coverage.dolthub.quarantined} archive sessions quarantined (excluded from
                every backtest)
              </span>
            )}
          </div>
        </Section>

        <Section title="NAMED BLIND SPOTS">
          <div className="flex flex-col gap-1.5">
            {coverage.blind_spots.map((b) => (
              <div key={b.id} className="flex gap-2 text-[12.5px] leading-[1.55] text-ink-2">
                <span className="text-ink-4">·</span>
                <span>{b.text}</span>
              </div>
            ))}
          </div>
        </Section>

        {coverage.collection_priorities &&
          coverage.collection_priorities.priorities.length > 0 && (
            <Section
              title={`COLLECTION WANTS · ranked weekly · ${coverage.collection_priorities.generated_at.slice(0, 10)}`}
            >
              <div className="flex flex-col gap-1.5">
                {coverage.collection_priorities.priorities.map((p) => (
                  <div key={p.rank} className="flex gap-2 text-[12.5px] leading-[1.55] text-ink-2">
                    <span className="font-mono text-ink-4">{p.rank}.</span>
                    <span>
                      {p.want}
                      <span className="text-ink-4"> — {p.why}</span>
                    </span>
                  </div>
                ))}
              </div>
            </Section>
          )}
      </Group>
    </div>
  );
}
