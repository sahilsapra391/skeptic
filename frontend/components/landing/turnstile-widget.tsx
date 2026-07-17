"use client";

/**
 * The Cloudflare Turnstile widget (launch L4 anon armor). Loads Cloudflare's
 * script once and renders the challenge; on solve it hands the token up.
 * Renders nothing (and no-ops) when NEXT_PUBLIC_TURNSTILE_SITE_KEY is unset,
 * so dev / pre-launch anon runs proceed without Cloudflare keys — the
 * backend skips verification in the same case.
 */

import { useEffect, useRef } from "react";

import { TURNSTILE_SITE_KEY, turnstileConfigured } from "@/lib/turnstile";

const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js";

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

export function TurnstileWidget({
  onVerify,
  resetKey = 0,
}: {
  // token string on solve; null on expiry/error (RUN gets re-blocked)
  onVerify: (token: string | null) => void;
  // bump to force a fresh challenge — a token is single-use, so after the
  // backend consumes one (e.g. a 403) the caller resets to mint a new one
  // instead of waiting ~5min for the old one to expire
  resetKey?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const widgetId = useRef<string | null>(null);

  useEffect(() => {
    if (!turnstileConfigured()) return;
    let cancelled = false;
    loadScript()
      .then(() => {
        if (cancelled || !ref.current || !window.turnstile) return;
        widgetId.current = window.turnstile.render(ref.current, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: (token: string) => onVerify(token),
          "expired-callback": () => onVerify(null),
          "error-callback": () => onVerify(null),
          appearance: "interaction-only", // invisible unless a challenge is needed
        });
      })
      .catch(() => onVerify(null));
    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetId.current);
        } catch {
          /* already gone */
        }
      }
    };
  }, [onVerify]);

  // a consumed/expired token → reset the widget so it re-solves
  useEffect(() => {
    if (resetKey === 0 || !widgetId.current || !window.turnstile) return;
    try {
      window.turnstile.reset(widgetId.current);
    } catch {
      /* widget gone — the next mount will render fresh */
    }
  }, [resetKey]);

  if (!turnstileConfigured()) return null;
  return <div ref={ref} className="flex justify-center" />;
}
