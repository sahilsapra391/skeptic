"use client";

/** Syncs the Appearance settings onto <html> as data attributes. The
 * inline script in layout.tsx sets them pre-hydration (no flash); this
 * keeps them live when the user changes settings. Market Hours resolves to
 * light/dark here and re-checks each minute so the palette flips at the
 * 8am / 6pm ET boundaries on its own. data-theme is only ever light|dark. */

import { useEffect } from "react";

import { getSettings, resolveTheme, useSettings } from "@/lib/settings";

export function ThemeApplier() {
  const { theme, accent } = useSettings();
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    const apply = () => {
      document.documentElement.dataset.theme = resolveTheme(getSettings().theme);
    };
    apply();
    if (theme !== "market") return;
    const id = window.setInterval(apply, 60_000);
    return () => window.clearInterval(id);
  }, [theme, accent]);
  return null;
}
