"use client";

/**
 * First-boot splash: the wordmark draws itself on (brand-kit animated
 * SVG — pathLength dash, no JS), then the app fades in. Shows once per
 * browser session, and ONLY when the app itself was the entry point —
 * a visitor who arrived through the landing already watched the hero
 * draw-on, so client-navigating into the product must not replay the
 * brand moment (owner 2026-07-17).
 */

import { useEffect, useState } from "react";
import clsx from "clsx";

import { useResolvedTheme } from "@/lib/settings";

// draw-on: last stroke starts at 0.84s and draws for 0.75s
const ANIMATION_MS = 1710;
const FADE_MS = 400;

// the public landing surface — sessions that BEGIN here never splash
const LANDING_ENTRY = new Set(["/", "/signin", "/signup", "/terms", "/privacy", "/refunds"]);

// decided ONCE per page load, at module scope — StrictMode's double
// effect can't consume the session flag and strand the overlay. This
// module loads lazily with the (app) layout, which on a landing-first
// session happens mid-client-navigation — so the entry route comes from
// the DOCUMENT request (navigation timing), never the current pathname.
const shouldShow = (() => {
  if (typeof window === "undefined") return false;
  try {
    if (sessionStorage.getItem("skeptic-booted")) return false;
    // consumed either way: one splash opportunity per session, and a
    // landing-first session spends it on the hero draw-on instead
    sessionStorage.setItem("skeptic-booted", "1");
    const nav = performance.getEntriesByType("navigation")[0] as
      | PerformanceNavigationTiming
      | undefined;
    const entryPath = nav ? new URL(nav.name).pathname : window.location.pathname;
    return !LANDING_ENTRY.has(entryPath);
  } catch {
    return false; // private mode — skip rather than replay forever
  }
})();

export function BootSplash() {
  const theme = useResolvedTheme();
  const [phase, setPhase] = useState<"hidden" | "drawing" | "fading">("hidden");

  useEffect(() => {
    if (!shouldShow) return;
    setPhase("drawing");
    const fade = setTimeout(() => setPhase("fading"), ANIMATION_MS);
    const done = setTimeout(() => setPhase("hidden"), ANIMATION_MS + FADE_MS);
    return () => {
      clearTimeout(fade);
      clearTimeout(done);
    };
  }, []);

  if (phase === "hidden") return null;
  return (
    <div
      aria-hidden
      className={clsx(
        "fixed inset-0 z-[100] flex items-center justify-center bg-ground transition-opacity print:hidden",
        phase === "fading" ? "opacity-0" : "opacity-100",
      )}
      style={{ transitionDuration: `${FADE_MS}ms` }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={theme === "light" ? "/brand/skeptic-draw-black.svg" : "/brand/skeptic-draw-white.svg"}
        alt=""
        className="w-[min(420px,60vw)]"
        draggable={false}
      />
    </div>
  );
}
