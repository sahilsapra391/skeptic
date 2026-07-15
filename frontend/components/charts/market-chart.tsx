"use client";

/**
 * MarketChart — brokerage-grade price chart for SPY/QQQ/IWM.
 *
 * Interaction model (replicating the owner's reference recording):
 * grab-and-drag panning that tracks the pointer 1:1 with inertia on release,
 * wheel/pinch zoom anchored at the cursor, continuous y auto-scale on the
 * visible window, older bars paged in seamlessly as you pull back in time,
 * and future whitespace past the latest candle. Candles/line, indicator
 * overlays and subpanels (volume, RSI, MACD), crosshair with OHLCV readout,
 * live IEX tail when the backend has APCA keys.
 *
 * All indicator math is server-side; this component only shapes and pans
 * what it is given, and labels freshness honestly. Candle/volume up/down
 * uses the P/L color pair; the trust hue appears only on pins/status.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";

import { getBars } from "@/lib/api";
import type {
  Bar,
  BarsPayload,
  ChartInterval,
  ChartWindow,
  IndicatorSeries,
  Ticker,
} from "@/lib/types";

const W = 860;
const PRICE_H = 330;
const VOL_H = 72;
const PANEL_H = 76;
const PAD_Y = 12;
const MAX_DRAWN = 700; // display decimation threshold
const PAGE_SIZE = 1000;
const MIN_SPAN = 12;
// buffer ceiling per interval view — beyond this, a coarser interval is the
// right tool (deep 5m history at full zoom-out would page in ~120k bars)
const MAX_BUFFER = 12_000;

const UP = "var(--pl-pos)"; // pl-pos — market up/down is P/L-family data
const DOWN = "var(--pl-neg)"; // pl-neg
const LINE = "var(--chart)";
const GRID = "var(--grid)";
const FAINT = "var(--ink-5)";

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
  { id: "sma:20", label: "SMA 20", color: "rgb(var(--warn-rgb))", kind: "overlay" },
  { id: "sma:50", label: "SMA 50", color: "#8ab8ff", kind: "overlay" },
  { id: "sma:200", label: "SMA 200", color: "#e08fc7", kind: "overlay" },
  { id: "ema:9", label: "EMA 9", color: "#6fd3f2", kind: "overlay" },
  { id: "ema:21", label: "EMA 21", color: "#f0a45d", kind: "overlay" },
  { id: "vwap", label: "VWAP", color: "var(--ink)", dash: "5 4", kind: "overlay" },
  { id: "bb:20:2", label: "Bollinger 20·2", color: "var(--ink-3)", kind: "band" },
  { id: "rsi:14", label: "RSI 14", color: "rgb(var(--warn-rgb))", kind: "rsi" },
  { id: "macd:12:26:9", label: "MACD 12·26·9", color: "#6fd3f2", kind: "macd" },
];

export interface ChartPin {
  a: { t: string; c: number };
  b: { t: string; c: number } | null;
}

interface Props {
  ticker: Ticker;
  pinMode?: boolean;
  pins?: ChartPin[];
  onBarClick?: (t: string, close: number) => void;
  onViewChange?: (interval: ChartInterval, window: ChartWindow) => void;
  /** fires whenever the loaded buffer changes (initial load, paging, live) */
  onDataChange?: (bars: Bar[]) => void;
}

interface Buffer {
  bars: Bar[];
  indicators: Record<string, IndicatorSeries>;
  hasMore: boolean;
  live: boolean;
  liveLabel: string | null;
  source: string;
  asOf: string | null;
}

interface View {
  start: number; // buffer-index units, fractional while panning
  span: number;
}

function fmtTime(iso: string, intraday: boolean): string {
  const d = new Date(iso);
  const opts: Intl.DateTimeFormatOptions = intraday
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York" }
    : { month: "short", day: "numeric", year: "2-digit", timeZone: "America/New_York" };
  return new Intl.DateTimeFormat("en-US", opts).format(d);
}

function fmtVol(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
  return String(v);
}

function sampleSeries(arr: (number | null)[] | undefined, idxs: number[]): (number | null)[] {
  if (!arr) return [];
  return idxs.map((i) => arr[i] ?? null);
}

function pathFrom(xs: number[], ys: (number | null)[], yFor: (v: number) => number): string {
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

function mergeIndicators(
  page: Record<string, IndicatorSeries>,
  existing: Record<string, IndicatorSeries>,
): Record<string, IndicatorSeries> {
  const out: Record<string, IndicatorSeries> = {};
  const keys = new Set([...Object.keys(page), ...Object.keys(existing)]);
  keys.forEach((key) => {
    const p = page[key];
    const e = existing[key];
    if (p == null) {
      out[key] = e;
      return;
    }
    if (e == null) {
      out[key] = p;
      return;
    }
    if (Array.isArray(p) && Array.isArray(e)) {
      out[key] = [...p, ...e];
    } else if (!Array.isArray(p) && !Array.isArray(e)) {
      const sub: Record<string, (number | null)[]> = {};
      Object.keys(p).forEach((k) => {
        sub[k] = [...(p[k] ?? []), ...(e[k] ?? [])];
      });
      out[key] = sub;
    } else {
      out[key] = e;
    }
  });
  return out;
}

export function MarketChart({ ticker, pinMode, pins, onBarClick, onViewChange, onDataChange }: Props) {
  const [interval, setIntervalState] = useState<ChartInterval>("5m");
  const [window_, setWindow] = useState<ChartWindow>("1w");
  const [chartType, setChartType] = useState<"candles" | "line">("candles");
  const [active, setActive] = useState<Set<string>>(new Set(["volume"]));
  const [buffer, setBuffer] = useState<Buffer | null>(null);
  const [view, setView] = useState<View>({ start: 0, span: 1 });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<{ i: number; ySvg: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const chartRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef(view);
  const bufferRef = useRef(buffer);
  const dragRef = useRef<{
    x0: number;
    lastX: number;
    lastT: number;
    vel: number;
    moved: boolean;
  } | null>(null);
  const inertiaRef = useRef<number | null>(null);
  const pagingRef = useRef(false);
  const lastPageAt = useRef(0);
  const lastWidthRef = useRef(W);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  viewRef.current = view;
  bufferRef.current = buffer;

  /** Rect reads are transiently 0 during render/hydration thrash — a
   * poisoned divisor turns one pointer step into thousands of bars. Only
   * trust plausible measurements; otherwise reuse the last good one. */
  const measureWidth = useCallback(() => {
    const w = chartRef.current?.clientWidth ?? 0;
    if (w > 100) lastWidthRef.current = w;
    return lastWidthRef.current;
  }, []);

  const clampView = useCallback((v: View, n: number): View => {
    const span = Math.min(Math.max(v.span, MIN_SPAN), Math.max(MIN_SPAN, n * 1.3));
    // left: slight overscroll; right: latest bar may sit at 70% (future space)
    const minStart = -span * 0.05;
    const maxStart = Math.max(minStart, n - span * 0.7);
    return { start: Math.min(Math.max(v.start, minStart), maxStart), span };
  }, []);

  /** The viewport lives in a ref and is mutated imperatively by gestures;
   * React receives throttled plain-object snapshots. Functional setState
   * updaters are banned here: under continuous paging React rebases and
   * REPLAYS queued updaters, which livelocks the render loop. */
  const viewStateRef = useRef<View>({ start: 0, span: 1 });
  const commitPending = useRef(false);
  const commitView = useCallback(() => {
    if (commitPending.current) return;
    commitPending.current = true;
    const flush = () => {
      if (!commitPending.current) return;
      commitPending.current = false;
      setView({ ...viewStateRef.current });
    };
    // rAF for frame-sync; timeout fallback because backgrounded tabs
    // starve rAF entirely
    requestAnimationFrame(flush);
    setTimeout(flush, 32);
  }, []);

  // set the moment the user pans/zooms — the phase-2 tail swap must never
  // reset a view the user has touched (an explicit flag, not view
  // arithmetic: "panned back to the origin" still counts as touched)
  const userTouchedView = useRef(false);

  const mutateView = useCallback(
    (fn: (v: View) => View) => {
      userTouchedView.current = true;
      viewStateRef.current = clampView(
        fn(viewStateRef.current),
        bufferRef.current?.bars.length ?? 0,
      );
      commitView();
    },
    [clampView, commitView],
  );

  const serverSpecs = useMemo(
    () => INDICATOR_DEFS.filter((d) => d.kind !== "volume" && active.has(d.id)).map((d) => d.id),
    [active],
  );
  const specsKey = serverSpecs.join(",");

  const stopInertia = useCallback(() => {
    if (inertiaRef.current != null) cancelAnimationFrame(inertiaRef.current);
    inertiaRef.current = null;
  }, []);

  // ---------------------------------------------------------------- loading
  // Two-phase (2026-07-15): phase 1 is lake-only (`tail: false` — the exact
  // URL prefetchBars warmed, and the backend skips its blocking live-tail
  // fetch), so "reading bars…" ends at the cached lake. Phase 2 fetches the
  // tail-carrying view behind the paint and swaps it in — the delayed badge
  // appears when it lands. loadSeq guards against a stale swap after the
  // ticker/interval changed mid-flight.
  const loadSeq = useRef(0);
  const hardLoad = useCallback(async () => {
    stopInertia();
    setLoading(true);
    setError(null);
    const seq = ++loadSeq.current;
    const apply = (p: BarsPayload, resetView: boolean) => {
      setBuffer({
        bars: p.bars,
        indicators: p.indicators,
        hasMore: p.has_more,
        live: p.live,
        liveLabel: p.live_label ?? null,
        source: p.source,
        asOf: p.as_of,
      });
      if (resetView) {
        userTouchedView.current = false; // a programmatic reset re-arms it
        viewStateRef.current = { start: 0, span: Math.max(p.bars.length, MIN_SPAN) };
        setView({ ...viewStateRef.current });
        setHover(null);
      }
    };
    try {
      const p: BarsPayload = await getBars(ticker, interval, window_, serverSpecs, {
        tail: false,
      });
      if (seq !== loadSeq.current) return;
      apply(p, true);
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setBuffer(null);
      setError(e instanceof Error ? e.message : "bars unavailable");
      setLoading(false);
      return;
    }
    setLoading(false);
    if (interval === "1d" || interval === "1w") return; // dailies carry no tail
    try {
      const full: BarsPayload = await getBars(ticker, interval, window_, serverSpecs);
      // never yank a view the user has touched (they're exploring the
      // phase-1 paint — the tail arrives on their next hard load), and
      // never swap mid-page (loadOlder's in-flight merge would clobber the
      // swapped buffer and drop the tail — review finding 2026-07-15)
      if (seq !== loadSeq.current || userTouchedView.current || pagingRef.current) return;
      apply(full, true);
    } catch {
      // the lake-only paint stands; the next hard load retries the tail
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, interval, window_, specsKey, stopInertia]);

  useEffect(() => {
    hardLoad();
  }, [hardLoad]);

  useEffect(() => {
    if (buffer) onDataChange?.(buffer.bars);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer]);

  const loadOlder = useCallback(async () => {
    const buf = bufferRef.current;
    if (!buf || !buf.hasMore || pagingRef.current || !buf.bars.length) return;
    if (buf.bars.length >= MAX_BUFFER) return;
    // cooldown between pages: a rail against pathological re-trigger loops
    // and against outpacing the user's actual pull-back speed
    if (performance.now() - lastPageAt.current < 200) return;
    pagingRef.current = true;
    lastPageAt.current = performance.now();
    try {
      const p = await getBars(ticker, interval, window_, serverSpecs, {
        before: buf.bars[0].t,
        limit: PAGE_SIZE,
      });
      if (!p.bars.length) {
        setBuffer((b) => (b ? { ...b, hasMore: false } : b));
        return;
      }
      const cur = bufferRef.current;
      if (!cur) return;
      const merged: Buffer = {
        ...cur,
        bars: [...p.bars, ...cur.bars],
        indicators: mergeIndicators(p.indicators, cur.indicators),
        hasMore: p.has_more,
      };
      // Advance the view by the prepended count in the SAME commit as the
      // buffer. If the (longer) buffer landed a render before the view moved,
      // React would paint one frame of the new bars against the old start≈0
      // view — the just-prepended OLD bars flashing at the left edge with a
      // y-axis jump — and, because start is still < 150 there, the paging
      // effect would re-fire on that frame: the flicker loop. Batching buffer
      // and view removes both the flash and the re-trigger. (Deferred commit
      // is only right for high-frequency GESTURE updates; here it's a race.)
      const added = p.bars.length;
      const next = clampView(
        { start: viewStateRef.current.start + added, span: viewStateRef.current.span },
        merged.bars.length,
      );
      bufferRef.current = merged;
      viewStateRef.current = next;
      setBuffer(merged);
      setView(next);
    } catch {
      // transient — the next pan retriggers
    } finally {
      pagingRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, interval, window_, specsKey, clampView]);

  const applyView = useCallback(
    (v: View) => {
      mutateView(() => v);
    },
    [mutateView],
  );

  const applyDeltaPx = useCallback(
    (px: number, widthPx: number) => {
      mutateView((v) => {
        const raw = px * (v.span / Math.max(widthPx, 1));
        // a single event can never legitimately move more than one screenful
        const deltaBars = Math.max(-v.span, Math.min(v.span, raw));
        return { start: v.start + deltaBars, span: v.span };
      });
    },
    [mutateView],
  );

  const zoomAt = useCallback(
    (frac: number, factor: number) => {
      mutateView((v) => {
        const span = v.span * factor;
        const anchor = v.start + frac * v.span;
        return { start: anchor - frac * span, span };
      });
    },
    [mutateView],
  );

  // page in older bars when the view approaches the buffer's left edge
  useEffect(() => {
    if (view.start < 150 && buffer?.hasMore && buffer.bars.length < MAX_BUFFER) loadOlder();
  }, [view, buffer, loadOlder]);

  // live tail: poll a small recent slice and splice it onto the buffer tail
  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    const buf = buffer;
    if (!buf?.live || interval === "1d" || interval === "1w") return;
    pollRef.current = setTimeout(async () => {
      try {
        // fresh: the poll must bypass the client cache (its TTL is now 5min
        // for paint reuse — a cached poll would freeze the live tail)
        const p = await getBars(ticker, interval, window_, serverSpecs, {
          limit: 120,
          fresh: true,
        });
        const cur = bufferRef.current;
        if (!cur || !p.bars.length) return;
        const splice = cur.bars.findIndex((b) => b.t === p.bars[0].t);
        if (splice < 0) return; // gap — next hard load reconciles
        const nOld = cur.bars.length;
        const bars = [...cur.bars.slice(0, splice), ...p.bars];
        const sliceInd = (e: IndicatorSeries, pv: IndicatorSeries): IndicatorSeries => {
          if (Array.isArray(e) && Array.isArray(pv)) return [...e.slice(0, splice), ...pv];
          if (!Array.isArray(e) && !Array.isArray(pv)) {
            const sub: Record<string, (number | null)[]> = {};
            Object.keys(pv).forEach((k) => {
              sub[k] = [...(e[k] ?? []).slice(0, splice), ...(pv[k] ?? [])];
            });
            return sub;
          }
          return pv;
        };
        const indicators: Record<string, IndicatorSeries> = {};
        Object.keys(p.indicators).forEach((k) => {
          indicators[k] = cur.indicators[k]
            ? sliceInd(cur.indicators[k], p.indicators[k])
            : p.indicators[k];
        });
        setBuffer({ ...cur, bars, indicators, live: p.live, liveLabel: p.live_label ?? cur.liveLabel, asOf: p.as_of });
        const v = viewRef.current;
        if (v.start + v.span >= nOld - 1.5) {
          // following the live edge — stay pinned to it
          applyView({ start: bars.length - v.span, span: v.span });
        }
      } catch {
        // best-effort
      }
      // 60s: the poll bypasses the client cache (fresh) and each hit pays
      // the backend's tail fetch — the old 15s cadence was silently
      // throttled to ~1/min by the cache TTL anyway, and the recorder
      // snapshots land ~2min apart, so a faster poll bought nothing
      // (review finding 2026-07-15)
    }, 60_000);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [buffer, ticker, interval, window_, serverSpecs, applyView]);

  // ------------------------------------------------------------ interaction
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!bufferRef.current?.bars.length) return;
    stopInertia();
    try {
      (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    } catch {
      // synthetic events (tests) have no active pointer to capture
    }
    dragRef.current = {
      x0: e.clientX,
      lastX: e.clientX,
      lastT: performance.now(),
      vel: 0,
      moved: false,
    };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const n = bufferRef.current?.bars.length ?? 0;
    if (!n) return;
    const drag = dragRef.current;
    if (drag) {
      if (Math.abs(e.clientX - drag.x0) > 3) drag.moved = true;
      if (drag.moved) {
        if (!dragging) setDragging(true);
        setHover(null);
        const now = performance.now();
        const dt = now - drag.lastT;
        const stepPx = e.clientX - drag.lastX;
        if (dt > 0) drag.vel = 0.8 * drag.vel + 0.2 * (stepPx / dt);
        drag.lastX = e.clientX;
        drag.lastT = now;
        // delta-based: composes safely with page-prepend index shifts —
        // an absolute write from a stale base would stomp them
        applyDeltaPx(-stepPx, measureWidth());
      }
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const abs = viewRef.current.start + frac * viewRef.current.span;
    setHover({ i: Math.min(n - 1, Math.max(0, Math.round(abs))), ySvg: ((e.clientY - rect.top) / rect.height) * PRICE_H });
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (!drag) return;
    if (!drag.moved) {
      if (pinMode && onBarClick && bufferRef.current?.bars.length) {
        const rect = e.currentTarget.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
        const abs = Math.round(viewRef.current.start + frac * viewRef.current.span);
        const bar = bufferRef.current.bars[Math.min(bufferRef.current.bars.length - 1, Math.max(0, abs))];
        if (bar) onBarClick(bar.t, bar.c);
      }
      return;
    }
    // inertia: keep gliding in the drag direction, decaying
    let vel = drag.vel; // px/ms
    if (Math.abs(vel) < 0.05) return;
    const widthPx = measureWidth();
    let last = performance.now();
    const glide = (now: number) => {
      const dt = Math.min(now - last, 48);
      last = now;
      vel *= Math.pow(0.94, dt / 16);
      applyDeltaPx(-vel * dt, widthPx);
      if (Math.abs(vel) > 0.02) {
        inertiaRef.current = requestAnimationFrame(glide);
      } else {
        inertiaRef.current = null;
      }
    };
    inertiaRef.current = requestAnimationFrame(glide);
  };

  // wheel/pinch: native listener so preventDefault works
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      const n = bufferRef.current?.bars.length ?? 0;
      if (!n) return;
      ev.preventDefault();
      stopInertia();
      const rect = el.getBoundingClientRect();
      const width = measureWidth();
      if (!ev.ctrlKey && Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {
        applyDeltaPx(ev.deltaX, width);
      } else {
        const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / Math.max(rect.width, 1)));
        zoomAt(frac, Math.exp(ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0022)));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [applyDeltaPx, zoomAt, stopInertia, measureWidth]);

  useEffect(() => () => stopInertia(), [stopInertia]);

  const setViewPreset = (iv: ChartInterval, win: ChartWindow) => {
    setIntervalState(iv);
    setWindow(win);
    onViewChange?.(iv, win);
  };

  // ------------------------------------------------------------- rendering
  const bars = useMemo(() => buffer?.bars ?? [], [buffer]);
  const n = bars.length;
  const intraday = !["1d", "1w"].includes(interval);

  const visible = useMemo(() => {
    if (!n) return null;
    const i0 = Math.max(0, Math.floor(view.start));
    const i1 = Math.min(n, Math.ceil(view.start + view.span));
    if (i1 <= i0) return null;
    const k = Math.max(1, Math.ceil((i1 - i0) / MAX_DRAWN));
    const groups: { iX: number; iEnd: number; o: number; h: number; l: number; c: number; v: number }[] = [];
    for (let g = Math.floor(i0 / k) * k; g < i1; g += k) {
      const end = Math.min(g + k, n);
      if (end <= g) continue;
      let h = -Infinity;
      let l = Infinity;
      let v = 0;
      for (let j = g; j < end; j++) {
        const b = bars[j];
        if (b.h > h) h = b.h;
        if (b.l < l) l = b.l;
        v += b.v;
      }
      groups.push({ iX: g + (end - 1 - g) / 2, iEnd: end - 1, o: bars[g].o, h, l, c: bars[end - 1].c, v });
    }
    return { i0, i1, k, groups };
  }, [bars, n, view]);

  const geom = useMemo(() => {
    if (!visible) return null;
    const xForAbs = (i: number) => ((i - view.start) / view.span) * W;
    let lo = Infinity;
    let hi = -Infinity;
    for (const g of visible.groups) {
      if (g.l < lo) lo = g.l;
      if (g.h > hi) hi = g.h;
    }
    const sampleIdxs = visible.groups.map((g) => g.iEnd);
    for (const def of INDICATOR_DEFS) {
      if ((def.kind !== "overlay" && def.kind !== "band") || !active.has(def.id)) continue;
      const series = buffer?.indicators[def.id];
      if (!series) continue;
      const lists = Array.isArray(series) ? [series] : Object.values(series);
      for (const list of lists) {
        for (const i of sampleIdxs) {
          const v = list[i];
          if (v == null) continue;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!(hi > lo)) {
      hi += 1;
      lo -= 1;
    }
    const pad = (hi - lo) * 0.04;
    hi += pad;
    lo -= pad;
    const yFor = (v: number) => PAD_Y + (1 - (v - lo) / (hi - lo)) * (PRICE_H - 2 * PAD_Y);
    const priceAt = (ySvg: number) => hi - ((ySvg - PAD_Y) / (PRICE_H - 2 * PAD_Y)) * (hi - lo);
    const bw = Math.max(1, Math.min(13, (W / view.span) * visible.k * 0.72));
    const maxVol = Math.max(1, ...visible.groups.map((g) => g.v));
    return { xForAbs, yFor, priceAt, lo, hi, bw, maxVol, sampleIdxs };
  }, [visible, view, active, buffer]);

  const candlePaths = useMemo(() => {
    if (!visible || !geom) return null;
    let wickUp = "";
    let wickDn = "";
    let bodyUp = "";
    let bodyDn = "";
    let volUp = "";
    let volDn = "";
    for (const g of visible.groups) {
      const up = g.c >= g.o;
      const x = geom.xForAbs(g.iX);
      const wick = `M${x.toFixed(1)},${geom.yFor(g.h).toFixed(1)}L${x.toFixed(1)},${geom.yFor(g.l).toFixed(1)}`;
      const top = geom.yFor(Math.max(g.o, g.c));
      const hBody = Math.max(1, Math.abs(geom.yFor(g.o) - geom.yFor(g.c)));
      const body = `M${(x - geom.bw / 2).toFixed(1)},${top.toFixed(1)}h${geom.bw.toFixed(1)}v${hBody.toFixed(1)}h${(-geom.bw).toFixed(1)}Z`;
      const vh = Math.max(1, (g.v / geom.maxVol) * (VOL_H - 8));
      const vol = `M${(x - geom.bw / 2).toFixed(1)},${(VOL_H - vh).toFixed(1)}h${geom.bw.toFixed(1)}v${vh.toFixed(1)}h${(-geom.bw).toFixed(1)}Z`;
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
  }, [visible, geom]);

  const overlayPaths = useMemo(() => {
    if (!visible || !geom || !buffer) return [];
    const xs = visible.groups.map((g) => geom.xForAbs(g.iX));
    const out: { key: string; color: string; dash?: string; d: string; thin?: boolean }[] = [];
    for (const def of INDICATOR_DEFS) {
      if (!active.has(def.id)) continue;
      const series = buffer.indicators[def.id];
      if (!series) continue;
      if (def.kind === "overlay" && Array.isArray(series)) {
        out.push({ key: def.id, color: def.color, dash: def.dash, d: pathFrom(xs, sampleSeries(series, geom.sampleIdxs), geom.yFor) });
      } else if (def.kind === "band" && !Array.isArray(series)) {
        (["upper", "mid", "lower"] as const).forEach((k) => {
          out.push({
            key: `${def.id}:${k}`,
            color: def.color,
            dash: k === "mid" ? "3 3" : undefined,
            thin: k !== "mid",
            d: pathFrom(xs, sampleSeries(series[k], geom.sampleIdxs), geom.yFor),
          });
        });
      }
    }
    return out;
  }, [visible, geom, buffer, active]);

  const priceTicks = useMemo(() => {
    if (!geom) return [];
    return Array.from({ length: 5 }, (_, i) => geom.lo + ((i + 0.5) / 5) * (geom.hi - geom.lo));
  }, [geom]);

  const timeTicks = useMemo(() => {
    if (!visible || !n) return [];
    const count = 6;
    const out: { frac: number; label: string }[] = [];
    for (let i = 0; i < count; i++) {
      const abs = view.start + ((i + 0.5) / count) * view.span;
      const idx = Math.round(abs);
      if (idx < 0 || idx >= n) continue;
      out.push({ frac: (i + 0.5) / count, label: fmtTime(bars[idx].t, intraday) });
    }
    return out;
  }, [visible, view, bars, n, intraday]);

  const hoverBar: Bar | null = hover && n ? bars[Math.min(hover.i, n - 1)] : null;
  const readout = hoverBar ?? (n ? bars[n - 1] : null);
  const readoutUp = readout ? readout.c >= readout.o : true;
  const atLatest = view.start + view.span >= n - 1.5;

  const pinPoints = useMemo(() => {
    if (!pins?.length || !geom || !n) return [];
    const byT = new Map(bars.map((b, i) => [b.t, i]));
    const out: { xa: number; ya: number; xb?: number; yb?: number }[] = [];
    for (const pin of pins) {
      const ia = byT.get(pin.a.t);
      if (ia == null) continue;
      const point: { xa: number; ya: number; xb?: number; yb?: number } = {
        xa: geom.xForAbs(ia),
        ya: geom.yFor(pin.a.c),
      };
      if (pin.b) {
        const ib = byT.get(pin.b.t);
        if (ib != null) {
          point.xb = geom.xForAbs(ib);
          point.yb = geom.yFor(pin.b.c);
        }
      }
      out.push(point);
    }
    return out;
  }, [pins, geom, bars, n]);

  const rsiSeries = active.has("rsi:14")
    ? (buffer?.indicators["rsi:14"] as (number | null)[] | undefined)
    : undefined;
  const macdSeries = active.has("macd:12:26:9")
    ? (buffer?.indicators["macd:12:26:9"] as Record<string, (number | null)[]> | undefined)
    : undefined;

  const chipCls = (on: boolean) =>
    clsx(
      "rounded-[8px] px-2.5 py-1 font-mono text-[12.5px]",
      on ? "bg-raised-3 text-ink" : "text-ink-4 hover:text-ink-3",
    );

  return (
    <div>
      {/* readout + freshness */}
      <div className="mb-2 flex items-center gap-3 font-mono text-[13px]">
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
        <span className="ml-auto flex items-center gap-1.5 text-[12px] text-ink-4">
          {buffer?.live && <span className="inline-block h-[7px] w-[7px] animate-pin-pulse rounded-full bg-trust" />}
          {buffer
            ? buffer.live
              ? (buffer.liveLabel ?? "live")
              : `through ${buffer.asOf ? fmtTime(buffer.asOf, intraday) : "—"} · ${buffer.liveLabel ?? "nightly lake"}`
            : ""}
        </span>
      </div>

      {/* price chart */}
      <div
        ref={chartRef}
        className={clsx(
          "relative touch-none select-none rounded-lg bg-panel-chart",
          dragging ? "cursor-grabbing" : pinMode ? "cursor-crosshair" : "cursor-grab",
        )}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => {
          if (!dragRef.current) setHover(null);
        }}
      >
        {error && (
          <div className="flex h-[330px] items-center justify-center px-6 text-center font-mono text-[12px] text-warn">
            {error}
          </div>
        )}
        {!error && !n && !loading && (
          <div className="flex h-[330px] items-center justify-center font-mono text-[12px] text-ink-4">
            no bars in the lake for this view
          </div>
        )}
        {!error && n > 0 && geom && visible && (
          <svg width="100%" viewBox={`0 0 ${W} ${PRICE_H}`} className="block">
            {priceTicks.map((p) => (
              <g key={p}>
                <line x1="0" y1={geom.yFor(p)} x2={W} y2={geom.yFor(p)} stroke={GRID} strokeWidth="1" />
                <text x={W - 4} y={geom.yFor(p) - 3} textAnchor="end" fontSize="11" fill={FAINT} fontFamily="var(--font-plex-mono)">
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
                d={pathFrom(
                  visible.groups.map((g) => geom.xForAbs(g.iX)),
                  visible.groups.map((g) => g.c),
                  geom.yFor,
                )}
                stroke={LINE}
                strokeWidth="1.6"
                fill="none"
              />
            )}

            {overlayPaths.map((o) => (
              <path
                key={o.key}
                d={o.d}
                stroke={o.color}
                strokeWidth={o.thin ? 0.7 : 1.1}
                strokeDasharray={o.dash}
                fill="none"
                opacity={o.thin ? 0.8 : 1}
              />
            ))}

            {pinPoints.map((p, i) => (
              <g key={i}>
                {p.xb != null && p.yb != null && (
                  <line x1={p.xa} y1={p.ya} x2={p.xb} y2={p.yb} stroke="var(--ac)" strokeWidth="1.4" strokeDasharray="5 4" />
                )}
                <circle cx={p.xa} cy={p.ya} r="5.5" fill="var(--ac)" />
                {p.xb != null && p.yb != null && (
                  <circle cx={p.xb} cy={p.yb} r="5.5" fill="var(--panel-chart)" stroke="var(--ac)" strokeWidth="2" />
                )}
                {p.xb == null && <circle cx={p.xa} cy={p.ya} r="6" fill="var(--ac)" className="animate-pin-pulse" />}
              </g>
            ))}

            {hover && hoverBar && !dragging && (
              <g>
                <line
                  x1={geom.xForAbs(hover.i)}
                  y1="0"
                  x2={geom.xForAbs(hover.i)}
                  y2={PRICE_H}
                  stroke={FAINT}
                  strokeWidth="0.7"
                  strokeDasharray="4 4"
                />
                <line x1="0" y1={hover.ySvg} x2={W} y2={hover.ySvg} stroke={FAINT} strokeWidth="0.7" strokeDasharray="4 4" />
                <rect x={W - 62} y={hover.ySvg - 9} width="58" height="15" rx="3" fill="var(--line)" />
                <text x={W - 33} y={hover.ySvg + 2.5} textAnchor="middle" fontSize="11" fill="var(--ink)" fontFamily="var(--font-plex-mono)">
                  {geom.priceAt(hover.ySvg).toFixed(2)}
                </text>
              </g>
            )}
          </svg>
        )}

        {!atLatest && n > 0 && (
          <button
            onClick={() => applyView({ start: n - view.span, span: view.span })}
            className="absolute bottom-2 right-2 rounded-full border border-line bg-panel px-2.5 py-1 font-mono text-[10.5px] text-ink-3 hover:text-ink"
          >
            → latest
          </button>
        )}
        {pagingRef.current && (
          <span className="absolute left-2 top-2 font-mono text-[10px] text-ink-4 animate-pin-pulse">
            loading older…
          </span>
        )}
      </div>

      {/* volume */}
      {active.has("volume") && candlePaths && (
        <svg width="100%" viewBox={`0 0 ${W} ${VOL_H}`} className="mt-1 block rounded-lg bg-panel-chart">
          <path d={candlePaths.volUp} fill={UP} opacity="0.55" />
          <path d={candlePaths.volDn} fill={DOWN} opacity="0.55" />
        </svg>
      )}

      {/* RSI */}
      {rsiSeries && geom && visible && (
        <svg width="100%" viewBox={`0 0 ${W} ${PANEL_H}`} className="mt-1 block rounded-lg bg-panel-chart">
          {[30, 70].map((g) => (
            <line key={g} x1="0" y1={PANEL_H - (g / 100) * PANEL_H} x2={W} y2={PANEL_H - (g / 100) * PANEL_H} stroke={GRID} strokeDasharray="4 4" />
          ))}
          <path
            d={pathFrom(
              visible.groups.map((g) => geom.xForAbs(g.iX)),
              sampleSeries(rsiSeries, geom.sampleIdxs),
              (v) => PANEL_H - (v / 100) * PANEL_H,
            )}
            stroke="rgb(var(--warn-rgb))"
            strokeWidth="1.1"
            fill="none"
          />
          <text x="6" y="12" fontSize="11" fill={FAINT} fontFamily="var(--font-plex-mono)">
            RSI 14
          </text>
        </svg>
      )}

      {/* MACD */}
      {macdSeries && geom && visible && (
        <MacdPanel
          xs={visible.groups.map((g) => geom.xForAbs(g.iX))}
          idxs={geom.sampleIdxs}
          series={macdSeries}
          bw={geom.bw}
        />
      )}

      {/* time axis */}
      <div className="relative mt-1 h-5 overflow-hidden">
        {timeTicks.map((t, i) => (
          <span
            key={`${t.label}-${i}`}
            className="absolute -translate-x-1/2 whitespace-nowrap font-mono text-[11.5px] text-ink-4"
            style={{ left: `${t.frac * 100}%` }}
          >
            {t.label}
          </span>
        ))}
        {hover && hoverBar && !dragging && (
          <span
            className="absolute -translate-x-1/2 whitespace-nowrap rounded bg-line px-1.5 font-mono text-[11.5px] text-ink"
            style={{ left: `${Math.min(0.96, Math.max(0.04, (hover.i - view.start) / view.span)) * 100}%` }}
          >
            {fmtTime(hoverBar.t, intraday)}
          </span>
        )}
      </div>

      {/* controls */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => setViewPreset(p.interval, p.window)}
            className={chipCls(window_ === p.window && interval === p.interval)}
          >
            {p.label}
          </button>
        ))}
        <span className="ml-2 font-mono text-[12px] text-ink-4">interval:</span>
        {INTERVALS.map((iv) => (
          <button key={iv} onClick={() => setViewPreset(iv, window_)} className={chipCls(interval === iv)}>
            {iv}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-1">
          <div className="relative">
            <button onClick={() => setMenuOpen((v) => !v)} className={chipCls(menuOpen || active.size > 1)}>
              ƒ indicators{active.size > 1 ? ` · ${active.size - 1}` : ""}
            </button>
            {menuOpen && (
              <div className="absolute bottom-9 right-0 z-10 w-[215px] rounded-[10px] border border-line bg-panel p-2 shadow-xl">
                <button
                  onClick={() => setChartType(chartType === "line" ? "candles" : "line")}
                  className={clsx(
                    "flex w-full items-center gap-2 rounded-md border-b border-line px-2 pb-2 pt-[5px] text-left font-mono text-[12.5px]",
                    chartType === "line" ? "text-ink" : "text-ink-4 hover:text-ink-3",
                  )}
                >
                  <span className="inline-block w-[8px] text-center">╱</span>
                  Line chart
                  <span className="ml-auto">{chartType === "line" ? "✓" : ""}</span>
                </button>
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
                        "flex w-full items-center gap-2 rounded-md px-2 py-[5px] text-left font-mono text-[12.5px]",
                        on ? "text-ink" : "text-ink-4 hover:text-ink-3",
                      )}
                    >
                      <span className="inline-block h-[8px] w-[8px] rounded-sm" style={{ background: on ? def.color : "var(--line)" }} />
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
  idxs,
  series,
  bw,
}: {
  xs: number[];
  idxs: number[];
  series: Record<string, (number | null)[]>;
  bw: number;
}) {
  const macdPts = sampleSeries(series.macd, idxs);
  const sigPts = sampleSeries(series.signal, idxs);
  const histPts = sampleSeries(series.hist, idxs);
  const values = [...macdPts, ...sigPts, ...histPts].filter((v): v is number => v != null);
  if (!values.length) return null;
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 0);
  const yFor = (v: number) => 6 + (1 - (v - lo) / (hi - lo || 1)) * (PANEL_H - 12);
  let histUp = "";
  let histDn = "";
  const zero = yFor(0);
  histPts.forEach((v, i) => {
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
      <path d={pathFrom(xs, macdPts, yFor)} stroke="#6fd3f2" strokeWidth="1.1" fill="none" />
      <path d={pathFrom(xs, sigPts, yFor)} stroke="rgb(var(--warn-rgb))" strokeWidth="1.1" fill="none" />
      <text x="6" y="12" fontSize="11" fill={FAINT} fontFamily="var(--font-plex-mono)">
        MACD 12·26·9
      </text>
    </svg>
  );
}
