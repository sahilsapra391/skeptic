"use client";

/**
 * Product Hunt "featured" badge (owner directive 2026-09-07: Skeptic
 * launched on Product Hunt; the embed goes on the main site in two places).
 *
 *  - ProductHuntBadge: the embed itself. The landing footer renders it
 *    under the day counter, permanently (nothing to dismiss).
 *  - ProductHuntBubble: the same badge floating bottom-right with a cross.
 *    useProductHuntBubble owns whether it shows: dismissal is per browsing
 *    session (sessionStorage), so closing it keeps it gone for this tab and
 *    a fresh visit brings it back. The landing page holds that state (the
 *    same way it holds theme) because the footer needs it too. It reserves
 *    room at its bottom while the bubble is up, so the bubble never sits on
 *    the theme control when the visitor scrolls to the end.
 *
 * Size: Product Hunt's embed is 250×54; both placements render it at 85%
 * (213×46, owner 2026-09-07: the full size read a little big). The SVG
 * scales cleanly, and the intrinsic ratio is kept to the pixel.
 *
 * Theme: Product Hunt ships the badge as two SVGs (dark / neutral). Both
 * are rendered and swapped off the painted <html data-theme>, the same
 * pure-CSS pattern the footer wordmark uses, so the pre-paint head script
 * settles which one shows and a light first load never flashes the dark
 * badge. The link carries its own aria-label because display:none drops
 * the hidden image (and its alt) from the accessibility tree.
 */

import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";

const PH_URL =
  "https://www.producthunt.com/products/skeptic?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-skeptic";
const PH_ALT =
  "Skeptic - An easy to use options backtester honest enough to say no | Product Hunt";
// the two variants Product Hunt generated; `t` is their cache-buster
const PH_IMG_DARK =
  "https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1244302&theme=dark&t=1788833922412";
const PH_IMG_LIGHT =
  "https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1244302&theme=neutral&t=1788833733661";

const DISMISS_KEY = "sk-ph-bubble-dismissed";

export function ProductHuntBadge({ className }: { className?: string }) {
  return (
    <a
      href={PH_URL}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={PH_ALT}
      className={clsx("block h-[46px] w-[213px]", className)}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={PH_IMG_DARK}
        alt={PH_ALT}
        width={213}
        height={46}
        className="block [[data-theme=light]_&]:hidden"
        draggable={false}
      />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={PH_IMG_LIGHT}
        alt=""
        aria-hidden
        width={213}
        height={46}
        className="hidden [[data-theme=light]_&]:block"
        draggable={false}
      />
    </a>
  );
}

export function useProductHuntBubble(): { shown: boolean; dismiss: () => void } {
  // false on the server and at hydration; the effect decides from
  // sessionStorage, so SSR markup and the first client render agree
  const [shown, setShown] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(DISMISS_KEY) === "1") return;
    } catch {
      /* private mode: nothing to remember, so show it */
    }
    setShown(true);
  }, []);

  const dismiss = useCallback(() => {
    setShown(false);
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* private mode */
    }
  }, []);

  return { shown, dismiss };
}

export function ProductHuntBubble({
  lifted = false,
  onDismiss,
}: {
  // the background-run pill owns the bottom-right corner while a run is in
  // flight, so the bubble sits above it instead of underneath it
  lifted?: boolean;
  onDismiss: () => void;
}) {
  return (
    <aside
      aria-label="Skeptic on Product Hunt"
      className={clsx(
        "fixed right-3 z-[60] flex items-center gap-1.5 rounded-[12px] border border-line-hover bg-panel p-2 shadow-pop transition-[bottom] duration-200 motion-safe:animate-fade-rise print:hidden md:right-6",
        lifted ? "bottom-[68px] md:bottom-[80px]" : "bottom-3 md:bottom-6",
      )}
    >
      <ProductHuntBadge />
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss the Product Hunt badge"
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] text-ink-4 hover:bg-raised-2 hover:text-ink"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <line x1="2" y1="2" x2="10" y2="10" />
          <line x1="10" y1="2" x2="2" y2="10" />
        </svg>
      </button>
    </aside>
  );
}
