"use client";

/**
 * Stamps <html data-theme> from the landing theme preference on the
 * signed-out satellite pages (auth + legal), so they follow the same
 * light/dark/market-hours mode as the landing. ThemeApplier stands down on
 * these paths (app settings don't own them); the pre-paint head script
 * handles first paint, and this keeps the market-hours flip live. Renders
 * nothing.
 */

import { useLandingTheme } from "@/components/landing/use-landing-theme";

export function PublicTheme() {
  useLandingTheme(); // effect stamps document.documentElement.dataset.theme
  return null;
}
