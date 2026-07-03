"use client";

/**
 * First-boot splash: the wordmark draws itself on (brand-kit animated
 * SVG — pathLength dash, no JS), then the app fades in. Shows once per
 * browser session; navigations never replay it.
 */

import { useEffect, useState } from "react";
import clsx from "clsx";

// draw-on: last stroke starts at 0.78s and draws for 0.75s
const ANIMATION_MS = 1650;
const FADE_MS = 400;

// decided ONCE per page load, at module scope — StrictMode's double
// effect can't consume the session flag and strand the overlay
const shouldShow = (() => {
  if (typeof window === "undefined") return false;
  try {
    if (sessionStorage.getItem("skeptic-booted")) return false;
    sessionStorage.setItem("skeptic-booted", "1");
    return true;
  } catch {
    return false; // private mode — skip rather than replay forever
  }
})();

export function BootSplash() {
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
        "fixed inset-0 z-[100] flex items-center justify-center bg-ground transition-opacity",
        phase === "fading" ? "opacity-0" : "opacity-100",
      )}
      style={{ transitionDuration: `${FADE_MS}ms` }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/brand/skeptic-draw-white.svg"
        alt=""
        className="w-[min(420px,60vw)]"
        draggable={false}
      />
    </div>
  );
}
