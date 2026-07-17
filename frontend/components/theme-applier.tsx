"use client";

/** Syncs the Appearance settings onto <html> as data attributes. The
 * inline script in layout.tsx sets them pre-hydration (no flash); this
 * keeps them live when the user changes settings. Market Hours resolves to
 * light/dark here and re-checks each minute so the palette flips at the
 * 8am / 6pm ET boundaries on its own. data-theme is only ever light|dark.
 *
 * Printing always gets the paper palette: beforeprint flips to light —
 * covering Save PDF and Cmd+P alike — and afterprint re-resolves from
 * settings. The flag stops the Market Hours interval from wrestling the
 * flip back mid-dialog in engines where print() doesn't block. */

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";

import { isPublicSurface } from "@/lib/public-surface";
import { getSettings, resolveTheme, useSettings } from "@/lib/settings";

export function ThemeApplier() {
  const { theme, accent } = useSettings();
  const pathname = usePathname();
  const printing = useRef(false);
  // the public surface (landing + auth + legal) owns data-theme via the
  // landing preference — without this gate the app-settings writer here
  // would win a 60s tug-of-war those pages can't see. Accent stays shared.
  const onPublic = isPublicSurface(pathname);
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    if (onPublic) return;
    const apply = () => {
      if (printing.current) return;
      document.documentElement.dataset.theme = resolveTheme(getSettings().theme);
    };
    apply();
    const before = () => {
      printing.current = true;
      document.documentElement.dataset.theme = "light";
    };
    const after = () => {
      printing.current = false;
      apply();
    };
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    const id = theme === "market" ? window.setInterval(apply, 60_000) : undefined;
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
      if (id !== undefined) window.clearInterval(id);
    };
  }, [theme, accent, onPublic]);
  return null;
}
