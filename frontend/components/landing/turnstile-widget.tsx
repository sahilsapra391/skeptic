"use client";

/**
 * The Cloudflare Turnstile widget (launch L4 anon armor). Loads Cloudflare's
 * script once and renders the challenge; callers mint a token on demand via
 * the imperative `refresh()` handle. Renders nothing (and refresh() resolves
 * null) when NEXT_PUBLIC_TURNSTILE_SITE_KEY is unset, so dev / pre-launch
 * runs proceed without Cloudflare keys — the backend skips verification in
 * the same case.
 *
 * Why refresh() and not a token-at-mount ref: a Turnstile token is single-use
 * and time-bounded. Minting it at mount and sending that same token on the
 * user's FIRST click meant the first submit rode a stale/consumed token that
 * siteverify rejected ("the human check didn't pass — please try again"),
 * while an immediate retry — which re-solved the widget — passed. Solving at
 * SUBMIT makes every attempt carry a freshly minted, never-yet-redeemed token,
 * so the first click behaves exactly like the (previously working) second.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

import { TURNSTILE_SITE_KEY, turnstileConfigured } from "@/lib/turnstile";

const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js";
// refresh() never hangs a submit: if a fresh token doesn't arrive within this
// window (script slow to load, a visible challenge the user hasn't finished),
// it resolves null — reset() has already invalidated any earlier token, so
// sending that would just 403. The caller surfaces null as a gentle "try
// again in a second" nudge rather than a scary rejection.
const REFRESH_TIMEOUT_MS = 8000;

type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  remove: (id: string) => void;
  reset: (id?: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    __skTurnstileLoading?: Promise<void>;
  }
}

/** What callers grab via a ref to mint a token at submit time. */
export type TurnstileHandle = {
  // Mint a FRESH, single-use token for THIS submit. Resolves with the token,
  // or null when Turnstile isn't configured (dev / pre-launch) or the widget
  // couldn't produce one in time — the caller then nudges the user to retry
  // instead of sending an empty/stale token.
  refresh: () => Promise<string | null>;
};

function loadScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (!window.__skTurnstileLoading) {
    window.__skTurnstileLoading = new Promise<void>((resolve, reject) => {
      const s = document.createElement("script");
      s.src = SCRIPT_SRC;
      s.async = true;
      s.defer = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("turnstile failed to load"));
      document.head.appendChild(s);
    });
  }
  return window.__skTurnstileLoading;
}

export const TurnstileWidget = forwardRef<
  TurnstileHandle,
  {
    // optional notification on every token the widget produces (mount solve,
    // a refresh, or null on expiry/error) — the send path uses refresh()
    onVerify?: (token: string | null) => void;
  }
>(function TurnstileWidget({ onVerify }, handleRef) {
  const ref = useRef<HTMLDivElement | null>(null);
  const widgetId = useRef<string | null>(null);
  // a one-shot resolver awaiting the NEXT token: set by refresh(), fulfilled
  // by the render callback below the instant Cloudflare delivers a token
  const pending = useRef<((token: string | null) => void) | null>(null);

  // route every token Cloudflare hands us: notify the caller and settle any
  // refresh() that's waiting. Kept in a ref so the render effect (deps [])
  // always calls the latest onVerify.
  const deliverRef = useRef<(token: string | null) => void>(() => {});
  deliverRef.current = (token: string | null) => {
    onVerify?.(token);
    const resolve = pending.current;
    if (resolve) {
      pending.current = null;
      resolve(token);
    }
  };

  useEffect(() => {
    if (!turnstileConfigured()) return;
    let cancelled = false;
    loadScript()
      .then(() => {
        if (cancelled || !ref.current || !window.turnstile) return;
        widgetId.current = window.turnstile.render(ref.current, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: (token: string) => deliverRef.current(token),
          "expired-callback": () => deliverRef.current(null),
          "error-callback": () => deliverRef.current(null),
          appearance: "interaction-only", // invisible unless a challenge is needed
          // integration-attribution label (the render-API equivalent of a
          // cf-turnstile div's data-action) — a static tag in our own
          // Cloudflare analytics, no per-user data
          action: "turnstile-spin-v2",
        });
      })
      .catch(() => deliverRef.current(null));
    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetId.current);
        } catch {
          /* already gone */
        }
        widgetId.current = null;
      }
    };
    // deliverRef is stable; onVerify changes are picked up via the ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(
    handleRef,
    () => ({
      refresh: () =>
        new Promise<string | null>((resolve) => {
          // no keys → no human check; the backend skips verification too
          if (!turnstileConfigured()) return resolve(null);
          pending.current = resolve;
          const wt = window.turnstile;
          if (wt && widgetId.current) {
            // force a brand-new single-use token for THIS submit; the render
            // callback settles `pending` when Cloudflare delivers it
            try {
              wt.reset(widgetId.current);
            } catch {
              // widget vanished — no fresh token to give
              pending.current = null;
              return resolve(null);
            }
          }
          // if the widget is still loading (an instant submit), the in-flight
          // mount solve will settle `pending` with its freshly minted token.
          // On timeout resolve null — reset() has already invalidated any
          // earlier token, so the caller nudges instead of sending a dud.
          setTimeout(() => {
            if (pending.current === resolve) {
              pending.current = null;
              resolve(null);
            }
          }, REFRESH_TIMEOUT_MS);
        }),
    }),
    [],
  );

  if (!turnstileConfigured()) return null;
  return <div ref={ref} className="flex justify-center" />;
});
