"use client";

/**
 * App settings — persisted locally, applied at submit time. Costs are
 * stamped onto EVERY spec the client sends (parsed or dial-built), so a
 * settings edit is reflected in the next analysis with zero server state.
 * Verbiage switches the language register of results across the app.
 */

import { useSyncExternalStore } from "react";

export type Verbiage = "institutional" | "retail";

export interface AppSettings {
  /** $ per contract per side; ≥ 0 */
  commission: number;
  /** fraction of the half-spread conceded, (0, 1] — 0 (mid fills) is banned */
  slippage: number;
  verbiage: Verbiage;
}

export const DEFAULT_SETTINGS: AppSettings = {
  commission: 0.65,
  slippage: 0.5,
  verbiage: "institutional",
};

const KEY = "skeptic-settings";
const EVT = "skeptic-settings-changed";

function clampSettings(s: AppSettings): AppSettings {
  return {
    commission: Math.min(5, Math.max(0, Number.isFinite(s.commission) ? s.commission : 0.65)),
    slippage: Math.min(1, Math.max(0.05, Number.isFinite(s.slippage) ? s.slippage : 0.5)),
    verbiage: s.verbiage === "retail" ? "retail" : "institutional",
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
