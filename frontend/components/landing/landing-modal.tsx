"use client";

/**
 * The landing's modal shell (launch L4): the product experience opens in
 * a popup ON the landing — visitors are never redirected into the app
 * shell (owner 2026-07-17). Backdrop/ESC close, body scroll locked while
 * open, panel scrolls internally.
 */

import { useEffect } from "react";
import Link from "next/link";
import clsx from "clsx";

export function LandingModal({
  onClose,
  label,
  wide = false,
  conversionCta,
  children,
}: {
  onClose: () => void;
  label: string; // a11y name + the header kicker
  wide?: boolean; // run/results panels want near-full width
  conversionCta?: string; // header CTA text → /signup (omit to hide)
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={label}
      className="fixed inset-0 z-[90] flex items-center justify-center p-3 md:p-6"
    >
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-[2px]"
      />
      <div
        className={clsx(
          "relative flex max-h-full w-full flex-col overflow-hidden rounded-[16px] border border-line bg-ground shadow-pop",
          wide ? "max-w-[1240px]" : "max-w-[560px]",
        )}
      >
        <div className="flex flex-none items-center justify-between gap-3 border-b border-line-softer bg-navbg px-4 py-2.5 md:px-5">
          <span className="truncate font-mono text-[10.5px] font-medium tracking-[.14em] text-ink-4">
            {label.toUpperCase()}
          </span>
          <div className="flex flex-none items-center gap-2.5">
            {conversionCta && (
              <Link
                href="/signup"
                className="rounded-[9px] bg-trust px-3.5 py-1.5 text-[12.5px] font-bold text-on-accent"
              >
                {conversionCta}
              </Link>
            )}
            <button
              onClick={onClose}
              aria-label="Close"
              className="flex h-8 w-8 items-center justify-center rounded-[8px] text-ink-4 hover:bg-raised-2 hover:text-ink"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                <line x1="2" y1="2" x2="12" y2="12" />
                <line x1="12" y1="2" x2="2" y2="12" />
              </svg>
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-8 pt-2 md:px-8">{children}</div>
      </div>
    </div>
  );
}
