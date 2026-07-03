"use client";

/** Syncs the Appearance settings onto <html> as data attributes. The
 * inline script in layout.tsx sets them pre-hydration (no flash); this
 * keeps them live when the user changes settings. */

import { useEffect } from "react";

import { useSettings } from "@/lib/settings";

export function ThemeApplier() {
  const { theme, accent } = useSettings();
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.accent = accent;
  }, [theme, accent]);
  return null;
}
