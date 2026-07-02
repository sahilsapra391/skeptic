"use client";

/**
 * MarketChart — the full price chart for SPY/QQQ/IWM: candles or line, any
 * interval 1m → 1W, range presets, crosshair with OHLCV readout, indicator
 * overlays (SMA/EMA/VWAP/Bollinger) and subpanels (volume, RSI, MACD), and
 * a live IEX tail when the backend has APCA keys.
 *
 * All indicator math is computed server-side (/api/data/bars) — this
 * component only shapes what it is given, and it labels freshness honestly
 * ("live" vs "through <last close>"). Candle/volume up/down use the P/L
 * color pair (market up/down is P/L-family data); the trust hue never
 * appears here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";

import { getBars } from "@/lib/api";
import type { Bar, BarsPayload, ChartInterval, ChartWindow, Ticker } from "@/lib/types";

const W = 860;
const PRICE_H = 300;
const VOL_H = 56;
const PANEL_H = 76;
const PAD_Y = 12;

const UP = "#43c987"; // pl-pos — market up/down is P/L-family data
const DOWN = "#e0604f"; // pl-neg
const LINE = "#cdd6df";
const GRID = "#20242c";
const FAINT = "#4a545f";

export const INTERVALS: ChartInterval[] = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

const PRESETS: { label: string; window: ChartWindow; interval: ChartInterval }[] = [
  { label: "1D", window: "1d", interval: "5m" },
  { label: "1W", window: "1w", interval: "5m" },
  { label: "1M", window: "1mo", interval: "30m" },
  { label: "3M", window: "3mo", interval: "1h" },
  { label: "YTD", window: "ytd", interval: "1h" },
  { label: "1Y", window: "1y", interval: "1d" },
  { label: "5Y", window: "5y", interval: "1d" },
  { label: "All", window: "all", interval: "1w" },
];

interface OverlayDef {
  id: string;
  label: string;
  color: string;
  dash?: string;
  kind: "overlay" | "band" | "rsi" | "macd" | "volume";
}

const INDICATOR_DEFS: OverlayDef[] = [
  { id: "volume", label: "Volume", color: FAINT, kind: "volume" },
  { id: "sma:20", label: "SMA 20", color: "#d9a441", kind: "overlay" },
  { id: "sma:50", label: "SMA 50", color: "#8ab8ff", kind: "overlay" },
  { id: "sma:200", label: "SMA 200", color: "#e08fc7", kind: "overlay" },
  { id: "ema:9", label: "EMA 9", color: "#6fd3f2", kind: "overlay" },
  { id: "ema:21", label: "EMA 21", color: "#f0a45d", kind: "overlay" },
  { id: "vwap", label: "VWAP", color: "#e9edf1", dash: "5 4", kind: "overlay" },
  { id: "bb:20:2", label: "Bollinger 20·2", color: "#98a2ad", kind: "band" },
  { id: "rsi:14", label: "RSI 14", color: "#d9a441", kind: "rsi" },
  { id: "macd:12:26:9", label: "MACD 12·26·9", color: "#6fd3f2", kind: "macd" },
];

export interface ChartPin {
  a: { t: string; c: number };
  b: { t: string; c: number } | null; // null while awaiting the exit click
}

interface Props {
  ticker: Ticker;
  pinMode?: boolean;
  pins?: ChartPin[];
  onBarClick?: (t: string, close: number) => void;
  onViewChange?: (interval: ChartInterval, window: ChartWindow) => void;
}

function fmtTime(iso: string, intraday: boolean, withDate = true): string {
  const d = new Date(iso);
  const opts: Intl.DateTimeFormatOptions = intraday
    ? withDate
      ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York" }
      : { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York" }
    : withDate
      ? { month: "short", day: "numeric", year: "2-digit", timeZone: "America/New_York" }
      : { month: "short", year: "2-digit", timeZone: "America/New_York" };
  return new Intl.DateTimeFormat("en-US", opts).format(d);
}

function fmtVol(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
  return String(v);
}

function seriesPath(xs: number[], ys: (number | null)[], yFor: (v: number) => number): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < ys.length; i++) {
    const v = ys[i];
    if (v == null || Number.isNaN(v)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${xs[i].toFixed(1)},${yFor(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}

export function MarketChart({ ticker, pinMode, pins, onBarClick, onViewChange }: Props) {
  const [interval, setIntervalState] = useState<ChartInterval>("5m");
  const [window_, setWindow] = useState<ChartWindow>("1w");
  const [chartType, setChartType] = useState<"candles" | "line">("candles");
  const [active, setActive] = useState<Set<string>>(new Set(["volume"]));
  const [payload, setPayload] = useState<BarsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<{ i: number; ySvg: number } | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const serverSpecs = useMemo(
    () => INDICATOR_DEFS.filter((d) => d.kind !== "volume" && active.has(d.id)).map((d) => d.id),
    [active],
  );

  const fetchBars = useCallback(
    async (silent: boolean) => {
      if (!silent) setLoading(true);
      try {
        const p = await getBars(ticker, interval, window_, serverSpecs);
        setPayload(p);
        setError(null);
      } catch (e) {
        if (!silent) {
          setPayload(null);
          setError(e instanceof Error ? e.message : "bars unavailable");
        }
      } finally {
        setLoading(false);
      }
    },
    [ticker, interval, window_, serverSpecs],
  );

  useEffect(() => {
    fetchBars(false);
  }, [fetchBars]);

  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (payload?.live && interval !== "1d" && interval !== "1w") {
      pollRef.current = setTimeout(() => fetchBars(true), 15_000);
    }
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [payload, interval, fetchBars]);

  const bars = useMemo(() => payload?.bars ?? [], [payload]);
  const intraday = !["1d", "1w"].includes(interval);
  const n = bars.length;

  const setView = (iv: ChartInterval, win: ChartWindow) => {
    setIntervalState(iv);
    setWindow(win);
    setHover(null);
    onViewChange?.(iv, win);
  };

  // ------------------------------------------------------------- geometry
  const geom = useMemo(() => {
    if (!n) return null;
    const xs = bars.map((_, i) => (n === 1 ? W / 2 : (i / (n - 1)) * (W - 16) + 8));
    const step = n > 1 ? (W - 16) / (n - 1) : W;
    const bw = Math.max(1, Math.min(13, step * 0.7));
    let lo = Infinity;
    let hi = -Infinity;
    for (const b of bars) {
      if (b.l < lo) lo = b.l;
      if (b.h > hi) hi = b.h;
    }
    for (const def of INDICATOR_DEFS) {
      if (def.kind !== "overlay" && def.kind !== "band") continue;
      if (!active.has(def.id)) continue;
      const series = payload?.indicators[def.id];
      if (!series) continue;
      const lists = Array.isArray(series) ? [series] : Object.values(series);
      for (const list of lists) {
        for (const v of list) {
          if (v == null) continue;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!(hi > lo)) {
      hi = hi + 1;
      lo = lo - 1;
    }
    const yFor = (v: number) => PAD_Y + (1 - (v - lo) / (hi - lo)) * (PRICE_H - 2 * PAD_Y);
    const priceAt = (ySvg: number) => hi - ((ySvg - PAD_Y) / (PRICE_H - 2 * PAD_Y)) * (hi - lo);
    const maxVol = Math.max(1, ...bars.map((b) => b.v));
    return { xs, bw, lo, hi, yFor, priceAt, maxVol };
  }, [bars, n, active, payload]);

  const candlePaths = useMemo(() => {
    if (!geom || !n) return null;
    let wickUp = "";
    let wickDn = "";
    let bodyUp = "";
    let bodyDn = "";
    let volUp = "";
    let volDn = "";
    const { xs, bw, yFor, maxVol } = geom;
    for (let i = 0; i < n; i++) {
      const b = bars[i];
      const up = b.c >= b.o;
      const x = xs[i];
      const wick = `M${x.toFixed(1)},${yFor(b.h).toFixed(1)}L${x.toFixed(1)},${yFor(b.l).toFixed(1)}`;
      const top = yFor(Math.max(b.o, b.c));
      const hBody = Math.max(1, Math.abs(yFor(b.o) - yFor(b.c)));
      const body = `M${(x - bw / 2).toFixed(1)},${top.toFixed(1)}h${bw.toFixed(1)}v${hBody.toFixed(1)}h${(-bw).toFixed(1)}Z`;
      const vh = Math.max(1, (b.v / maxVol) * (VOL_H - 8));
      const vol = `M${(x - bw / 2).toFixed(1)},${(VOL_H - vh).toFixed(1)}h${bw.toFixed(1)}v${vh.toFixed(1)}h${(-bw).toFixed(1)}Z`;
      if (up) {
        wickUp += wick;
        bodyUp += body;
        volUp += vol;
      } else {
        wickDn += wick;
        bodyDn += body;
        volDn += vol;
      }
    }
    return { wickUp, wickDn, bodyUp, bodyDn, volUp, volDn };
  }, [geom, bars, n]);

  const priceTicks = useMemo(() => {
    if (!geom) return [];
    const { lo, hi } = geom;
    return Array.from({ length: 5 }, (_, i) => lo + ((i + 0.5) / 5) * (hi - lo));
  }, [geom]);

  const timeTicks = useMemo(() => {
    if (!n) return [];
    const count = Math.min(6, n);
    return Array.from({ length: count }, (_, i) => {
      const idx = Math.round((i / Math.max(count - 1, 1)) * (n - 1));
      return { idx, label: fmtTime(bars[idx].t, intraday, !intraday || n * 1 > 400 ? true : true) };
    });
  }, [bars, n, intraday]);

  // ------------------------------------------------------------ crosshair
  const idxFromEvent = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const fx = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const ySvg = ((e.clientY - rect.top) / rect.height) * PRICE_H;
    return { i: Math.round(fx * (n - 1)), ySvg };
  };

  const hoverBar: Bar | null = hover && n ? bars[Math.min(hover.i, n - 1)] : null;
  const readout = hoverBar ?? (n ? bars[n - 1] : null);
  const readoutUp = readout ? readout.c >= readout.o : true;

  const pinPoints = useMemo(() => {
    if (!pins?.length || !geom || !n) return [];
    const byT = new Map(bars.map((b, i) => [b.t, i]));
    const out: { xa: number; ya: number; xb?: number; yb?: number }[] = [];
    for (const pin of pins) {
      const ia = byT.get(pin.a.t);
      if (ia == null) continue;
      const point: { xa: number; ya: number; xb?: number; yb?: number } = {
        xa: geom.xs[ia],
        ya: geom.yFor(pin.a.c),
      };
      if (pin.b) {
        const ib = byT.get(pin.b.t);
        if (ib != null) {
          point.xb = geom.xs[ib];
          point.yb = geom.yFor(pin.b.c);
        }
      }
      out.push(point);
    }
    return out;
  }, [pins, geom, bars, n]);

  const overlayDefs = INDICATOR_DEFS.filter((d) => active.has(d.id));
  const rsiSeries = active.has("rsi:14") ? (payload?.indicators["rsi:14"] as (number | null)[] | undefined) : undefined;
  const macdSeries = active.has("macd:12:26:9")
    ? (payload?.indicators["macd:12:26:9"] as Record<string, (number | null)[]> | undefined)
    : undefined;

  const chipCls = (on: boolean) =>
    clsx(
      "rounded-[7px] px-2 py-[3px] font-mono text-[11px]",
      on ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-3",
    );

  return (
    <div>
      {/* readout + freshness */}
      <div className="mb-1.5 flex items-center gap-3 font-mono text-[11.5px]">
        {readout ? (
          <span className="text-ink-4">
            O <b style={{ color: readoutUp ? UP : DOWN }}>{readout.o.toFixed(2)}</b> H{" "}
            <b style={{ color: readoutUp ? UP : DOWN }}>{readout.h.toFixed(2)}</b> L{" "}
            <b style={{ color: readoutUp ? UP : DOWN }}>{readout.l.toFixed(2)}</b> C{" "}
            <b style={{ color: readoutUp ? UP : DOWN }}>{readout.c.toFixed(2)}</b> V{" "}
            <b style={{ color: readoutUp ? UP : DOWN }}>{fmtVol(readout.v)}</b>
          </span>
        ) : (
          <span className="text-ink-4">{loading ? "reading bars…" : "—"}</span>
        )}
        <span className="ml-auto flex items-center gap-1.5 text-[10.5px] text-ink-4">
          {payload?.live && <span className="inline-block h-[7px] w-[7px] animate-pin-pulse rounded-full bg-trust" />}
          {payload
            ? payload.live
              ? "live · iex tail"
              : `through ${payload.as_of ? fmtTime(payload.as_of, intraday) : "—"} · nightly lake`
            : ""}
        </span>
      </div>

      {/* price chart */}
      <div
        className={clsx("relative rounded-lg bg-panel-chart", pinMode ? "cursor-crosshair" : "cursor-default")}
        onMouseMove={(e) => n && setHover(idxFromEvent(e))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          if (!pinMode || !onBarClick || !n) return;
          const { i } = idxFromEvent(e);
          const b = bars[Math.min(i, n - 1)];
          onBarClick(b.t, b.c);
        }}
      >
        {error && (
          <div className="flex h-[300px] items-center justify-center px-6 text-center font-mono text-[12px] text-warn">
            {error}
          </div>
        )}
        {!error && !n && !loading && (
          <div className="flex h-[300px] items-center justify-center font-mono text-[12px] text-ink-4">
            no bars in the lake for this view
          </div>
        )}
        {!error && n > 0 && geom && (
          <svg width="100%" viewBox={`0 0 ${W} ${PRICE_H}`} className="block">
            {priceTicks.map((p) => (
              <g key={p}>
                <line x1="0" y1={geom.yFor(p)} x2={W} y2={geom.yFor(p)} stroke={GRID} strokeWidth="1" />
                <text x={W - 4} y={geom.yFor(p) - 3} textAnchor="end" fontSize="9.5" fill={FAINT} fontFamily="monospace">
                  {p.toFixed(2)}
                </text>
              </g>
            ))}

            {chartType === "candles" && candlePaths ? (
              <>
                <path d={candlePaths.wickUp} stroke={UP} strokeWidth="1" fill="none" />
                <path d={candlePaths.wickDn} stroke={DOWN} strokeWidth="1" fill="none" />
                <path d={candlePaths.bodyUp} fill={UP} />
                <path d={candlePaths.bodyDn} fill={DOWN} />
              </>
            ) : (
              <path
                d={seriesPath(geom.xs, bars.map((b) => b.c), geom.yFor)}
                stroke={LINE}
                strokeWidth="1.6"
                fill="none"
              />
            )}

            {overlayDefs.map((def) => {
              const series = payload?.indicators[def.id];
              if (!series) return null;
              if (def.kind === "overlay" && Array.isArray(series)) {
                return (
                  <path
                    key={def.id}
                    d={seriesPath(geom.xs, series, geom.yFor)}
                    stroke={def.color}
                    strokeWidth="1.1"
                    strokeDasharray={def.dash}
                    fill="none"
                  />
                );
              }
              if (def.kind === "band" && !Array.isArray(series)) {
                return (
                  <g key={def.id} opacity="0.8">
                    {(["upper", "mid", "lower"] as const).map((k) => (
                      <path
                        key={k}
                        d={seriesPath(geom.xs, series[k] ?? [], geom.yFor)}
                        stroke={def.color}
                        strokeWidth={k === "mid" ? 0.9 : 0.7}
                        strokeDasharray={k === "mid" ? "3 3" : undefined}
                        fill="none"
                      />
                    ))}
                  </g>
                );
              }
              return null;
            })}

            {/* pins (chart-teach) */}
            {pinPoints.map((p, i) => (
              <g key={i}>
                {p.xb != null && p.yb != null && (
                  <line x1={p.xa} y1={p.ya} x2={p.xb} y2={p.yb} stroke="var(--ac)" strokeWidth="1.4" strokeDasharray="5 4" />
                )}
                <circle cx={p.xa} cy={p.ya} r="5.5" fill="var(--ac)" />
                {p.xb != null && p.yb != null && (
                  <circle cx={p.xb} cy={p.yb} r="5.5" fill="#171a20" stroke="var(--ac)" strokeWidth="2" />
                )}
                {p.xb == null && <circle cx={p.xa} cy={p.ya} r="6" fill="var(--ac)" className="animate-pin-pulse" />}
              </g>
            ))}

            {/* crosshair */}
            {hover && hoverBar && (
              <g>
                <line x1={geom.xs[Math.min(hover.i, n - 1)]} y1="0" x2={geom.xs[Math.min(hover.i, n - 1)]} y2={PRICE_H} stroke={FAINT} strokeWidth="0.7" strokeDasharray="4 4" />
                <line x1="0" y1={hover.ySvg} x2={W} y2={hover.ySvg} stroke={FAINT} strokeWidth="0.7" strokeDasharray="4 4" />
                <rect x={W - 62} y={hover.ySvg - 9} width="58" height="15" rx="3" fill="#2b303a" />
                <text x={W - 33} y={hover.ySvg + 2.5} textAnchor="middle" fontSize="9.5" fill="#e9edf1" fontFamily="monospace">
                  {geom.priceAt(hover.ySvg).toFixed(2)}
                </text>
              </g>
            )}
          </svg>
        )}
      </div>

      {/* volume */}
      {active.has("volume") && n > 0 && candlePaths && (
        <svg width="100%" viewBox={`0 0 ${W} ${VOL_H}`} className="mt-1 block rounded-lg bg-panel-chart">
          <path d={candlePaths.volUp} fill={UP} opacity="0.55" />
          <path d={candlePaths.volDn} fill={DOWN} opacity="0.55" />
        </svg>
      )}

      {/* RSI */}
      {rsiSeries && geom && n > 0 && (
        <svg width="100%" viewBox={`0 0 ${W} ${PANEL_H}`} className="mt-1 block rounded-lg bg-panel-chart">
          {[30, 70].map((g) => (
            <line key={g} x1="0" y1={PANEL_H - (g / 100) * PANEL_H} x2={W} y2={PANEL_H - (g / 100) * PANEL_H} stroke={GRID} strokeDasharray="4 4" />
          ))}
          <path
            d={seriesPath(geom.xs, rsiSeries, (v) => PANEL_H - (v / 100) * PANEL_H)}
            stroke="#d9a441"
            strokeWidth="1.1"
            fill="none"
          />
          <text x="6" y="12" fontSize="9.5" fill={FAINT} fontFamily="monospace">
            RSI 14{(() => {
              const last = [...rsiSeries].reverse().find((v) => v != null);
              return last != null ? ` · ${last.toFixed(1)}` : "";
            })()}
          </text>
        </svg>
      )}

      {/* MACD */}
      {macdSeries && geom && n > 0 && (
        <MacdPanel xs={geom.xs} series={macdSeries} bw={geom.bw} />
      )}

      {/* time axis */}
      <div className="relative mt-0.5 h-4">
        {timeTicks.map((t, i) => (
          <span
            key={t.idx}
            className={clsx(
              "absolute whitespace-nowrap font-mono text-[10px] text-ink-4",
              i === 0 ? "" : i === timeTicks.length - 1 ? "-translate-x-full" : "-translate-x-1/2",
            )}
            style={{ left: `${(t.idx / Math.max(n - 1, 1)) * 100}%` }}
          >
            {t.label}
          </span>
        ))}
        {hover && hoverBar && (
          <span
            className="absolute -translate-x-1/2 rounded bg-line px-1.5 font-mono text-[10px] text-ink"
            style={{ left: `${(Math.min(hover.i, n - 1) / Math.max(n - 1, 1)) * 100}%` }}
          >
            {fmtTime(hoverBar.t, intraday)}
          </span>
        )}
      </div>

      {/* controls */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {PRESETS.map((p) => (
          <button key={p.label} onClick={() => setView(p.interval, p.window)} className={chipCls(window_ === p.window && interval === p.interval)}>
            {p.label}
          </button>
        ))}
        <span className="ml-2 font-mono text-[10.5px] text-ink-4">interval:</span>
        {INTERVALS.map((iv) => (
          <button key={iv} onClick={() => setView(iv, window_)} className={chipCls(interval === iv)}>
            {iv}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => setChartType("candles")} className={chipCls(chartType === "candles")} title="Candles">
            ▮
          </button>
          <button onClick={() => setChartType("line")} className={chipCls(chartType === "line")} title="Line">
            ╱
          </button>
          <div className="relative">
            <button onClick={() => setMenuOpen((v) => !v)} className={chipCls(menuOpen || active.size > 1)}>
              ƒ indicators{active.size > 1 ? ` · ${active.size - 1}` : ""}
            </button>
            {menuOpen && (
              <div className="absolute bottom-8 right-0 z-10 w-[190px] rounded-[10px] border border-line bg-panel p-2 shadow-xl">
                {INDICATOR_DEFS.map((def) => {
                  const on = active.has(def.id);
                  return (
                    <button
                      key={def.id}
                      onClick={() => {
                        const next = new Set(active);
                        if (on) next.delete(def.id);
                        else next.add(def.id);
                        setActive(next);
                      }}
                      className={clsx(
                        "flex w-full items-center gap-2 rounded-md px-2 py-[5px] text-left font-mono text-[11.5px]",
                        on ? "text-ink" : "text-ink-4 hover:text-ink-3",
                      )}
                    >
                      <span
                        className="inline-block h-[8px] w-[8px] rounded-sm"
                        style={{ background: on ? def.color : "#2b303a" }}
                      />
                      {def.label}
                      <span className="ml-auto">{on ? "✓" : ""}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MacdPanel({
  xs,
  series,
  bw,
}: {
  xs: number[];
  series: Record<string, (number | null)[]>;
  bw: number;
}) {
  const values = [...(series.macd ?? []), ...(series.signal ?? []), ...(series.hist ?? [])].filter(
    (v): v is number => v != null,
  );
  if (!values.length) return null;
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 0);
  const yFor = (v: number) => 6 + (1 - (v - lo) / (hi - lo || 1)) * (PANEL_H - 12);
  let histUp = "";
  let histDn = "";
  const zero = yFor(0);
  (series.hist ?? []).forEach((v, i) => {
    if (v == null) return;
    const y = yFor(v);
    const seg = `M${(xs[i] - bw / 2).toFixed(1)},${Math.min(y, zero).toFixed(1)}h${bw.toFixed(1)}v${Math.max(1, Math.abs(zero - y)).toFixed(1)}h${(-bw).toFixed(1)}Z`;
    if (v >= 0) histUp += seg;
    else histDn += seg;
  });
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${PANEL_H}`} className="mt-1 block rounded-lg bg-panel-chart">
      <line x1="0" y1={zero} x2={W} y2={zero} stroke={GRID} />
      <path d={histUp} fill={UP} opacity="0.5" />
      <path d={histDn} fill={DOWN} opacity="0.5" />
      <path d={seriesPath(xs, series.macd ?? [], yFor)} stroke="#6fd3f2" strokeWidth="1.1" fill="none" />
      <path d={seriesPath(xs, series.signal ?? [], yFor)} stroke="#d9a441" strokeWidth="1.1" fill="none" />
      <text x="6" y="12" fontSize="9.5" fill={FAINT} fontFamily="monospace">
        MACD 12·26·9
      </text>
    </svg>
  );
}
