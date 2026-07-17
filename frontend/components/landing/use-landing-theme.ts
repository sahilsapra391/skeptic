"use client";

/**
 * Landing-only theme (component-specs §8): market-hours default, persisted
 * under its OWN key — the landing's footer control must never rewrite the
 * app's settings, and vice versa. Stamps <html data-theme> (tokens are
 * :root-scoped — a wrapper div can't re-derive --ac/--acd/--acb, see reader
 * C's notes); the root head script pre-paints the same value on `/`, so
 * there is no flash. ThemeApplier stands down on the landing (pathname
 * gate) — without that it rewrites data-theme from app settings every 60s.
 */

import { useEffect, useLayoutEffect, useState } from "react";

import { resolveTheme, type Theme } from "@/lib/settings";

const KEY = "sk-landing-theme";

export type LandingTheme = {
  pref: Theme;
  resolved: "light" | "dark";
  set: (t: Theme) => void;
};

function load(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "market";
  } catch {
    return "market";
  }
}

export function useLandingTheme(): LandingTheme {
  const [pref, setPref] = useState<Theme>("market"); // SSR-safe default
  // seed from the pre-paint head script's stamp, not a hardcoded "dark" —
  // a daytime (market-hours-light) first load otherwise renders the white
  // brand assets on paper until the mount effects run (review finding)
  const [resolved, setResolved] = useState<"light" | "dark">(() =>
    typeof document !== "undefined" && document.documentElement.dataset.theme === "light"
      ? "light"
      : "dark",
  );

  useLayoutEffect(() => {
    setPref(load()); // the real preference, before first paint post-hydration
  }, []);

  useEffect(() => {
    let printing = false;
    const apply = () => {
      if (printing) return;
      const r = resolveTheme(pref);
      setResolved(r);
      document.documentElement.dataset.theme = r;
    };
    apply();
    // printing always gets the paper palette (matches ThemeApplier) — a
    // dark-mode visitor printing a legal page shouldn't get an ink page
    const before = () => {
      printing = true;
      document.documentElement.dataset.theme = "light";
    };
    const after = () => {
      printing = false;
      apply();
    };
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    // market hours flips live at 8am/6pm ET — same cadence as ThemeApplier
    const id = pref === "market" ? window.setInterval(apply, 60_000) : undefined;
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
      if (id !== undefined) window.clearInterval(id);
    };
  }, [pref]);

  const set = (t: Theme) => {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* private mode */
    }
    setPref(t);
  };

  return { pref, resolved, set };
}
