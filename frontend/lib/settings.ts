"use client";

/**
 * App settings — persisted locally, applied at submit time. Costs are
 * stamped onto EVERY spec the client sends (parsed or dial-built), so a
 * settings edit is reflected in the next analysis with zero server state.
 * Verbiage switches the language register of results across the app.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

export type Verbiage = "institutional" | "retail";
/** "market" follows the clock (see resolveTheme); light/dark are pinned. */
export type Theme = "market" | "dark" | "light";
/** the concrete palette actually applied — what data-theme is ever set to. */
export type ResolvedTheme = "dark" | "light";
export type Accent = "cyan" | "sage" | "lavender" | "rose";

export const ACCENTS: Accent[] = ["cyan", "sage", "lavender", "rose"];

export interface AppSettings {
  /** $ per contract per side; ≥ 0 */
  commission: number;
  /** fraction of the half-spread conceded, (0, 1] — 0 (mid fills) is banned */
  slippage: number;
  verbiage: Verbiage;
  theme: Theme;
  accent: Accent;
}

export const DEFAULT_SETTINGS: AppSettings = {
  commission: 0.65,
  slippage: 0.5,
  verbiage: "institutional",
  // Market Hours is the default for everyone: light through the trading day,
  // dark after the close (owner directive 2026-07-04).
  theme: "market",
  accent: "cyan",
};

const KEY = "skeptic-settings";
const EVT = "skeptic-settings-changed";

function clampSettings(s: AppSettings): AppSettings {
  return {
    commission: Math.min(5, Math.max(0, Number.isFinite(s.commission) ? s.commission : 0.65)),
    slippage: Math.min(1, Math.max(0.05, Number.isFinite(s.slippage) ? s.slippage : 0.5)),
    verbiage: s.verbiage === "retail" ? "retail" : "institutional",
    theme: s.theme === "light" ? "light" : s.theme === "dark" ? "dark" : "market",
    accent: ACCENTS.includes(s.accent) ? s.accent : "cyan",
  };
}

function load(): AppSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    return clampSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(raw) });
  } catch {
    return DEFAULT_SETTINGS;
  }
}

let cache: AppSettings = load();

export function getSettings(): AppSettings {
  return cache;
}

export function updateSettings(patch: Partial<AppSettings>): AppSettings {
  cache = clampSettings({ ...cache, ...patch });
  try {
    localStorage.setItem(KEY, JSON.stringify(cache));
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(EVT));
  return cache;
}

function subscribe(cb: () => void): () => void {
  const handler = () => {
    cache = load();
    cb();
  };
  window.addEventListener(EVT, handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(EVT, handler);
    window.removeEventListener("storage", handler);
  };
}

export function useSettings(): AppSettings {
  return useSyncExternalStore(subscribe, getSettings, () => DEFAULT_SETTINGS);
}

// ---------------------------------------------------------- theme resolution

/** Market Hours: light during the US trading day (8am–6pm New York, which
 * tracks EST/EDT automatically), dark outside it. Pinned light/dark are
 * returned unchanged. This is the ONLY place the clock rule lives — the
 * inline no-flash script in layout.tsx mirrors it in plain JS. */
export function resolveTheme(theme: Theme, now: Date = new Date()): ResolvedTheme {
  if (theme === "light" || theme === "dark") return theme;
  let hour: number;
  try {
    hour =
      Number(
        new Intl.DateTimeFormat("en-US", {
          timeZone: "America/New_York",
          hour12: false,
          hour: "2-digit",
        }).format(now),
      ) % 24; // "24" at midnight in some engines → 0
  } catch {
    hour = now.getHours(); // environment without tz data → local clock
  }
  return hour >= 8 && hour < 18 ? "light" : "dark";
}

/** The applied light/dark, re-evaluated each minute while on Market Hours so
 * the palette flips at the 8am / 6pm boundaries without a reload. Returns
 * "dark" on the server and first paint (matches the SSR default) to avoid a
 * hydration mismatch; the effect corrects it immediately on mount. */
export function useResolvedTheme(): ResolvedTheme {
  const { theme } = useSettings();
  const [resolved, setResolved] = useState<ResolvedTheme>("dark");
  useEffect(() => {
    const apply = () => setResolved(resolveTheme(getSettings().theme));
    apply();
    if (theme !== "market") return;
    const id = window.setInterval(apply, 60_000);
    return () => window.clearInterval(id);
  }, [theme]);
  return resolved;
}
