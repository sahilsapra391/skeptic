import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";

import { ThemeApplier } from "@/components/theme-applier";

import "./globals.css";

const archivo = Archivo({
  subsets: ["latin"],
  variable: "--font-archivo",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
});

// display serif — headings and important moments ONLY (owner directive
// 2026-07-03): body stays Archivo, data stays Plex Mono
const newsreader = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
});

export const metadata: Metadata = {
  // SEO (launch L4): absolute URLs for OG/canonical resolve against this.
  // Every claim in titles/descriptions must stay literally true — the
  // audience detects fakery for a living (design brief), and so do raters.
  metadataBase: new URL("https://skeptic.fyi"),
  title: {
    default: "Skeptic — The Options Backtester That Argues With You",
    template: "%s — Skeptic",
  },
  description:
    "Backtest options strategies in plain English. Skeptic interviews you, " +
    "runs the strategy on real intraday options data — fills at bid/ask, " +
    "never mid — then attacks its own result and refuses verdicts the " +
    "evidence can't support. Research tool, not financial advice.",
  applicationName: "Skeptic",
  category: "finance",
  keywords: [
    "options backtesting",
    "backtest options strategies",
    "options strategy backtest",
    "0DTE backtest",
    "short put backtest",
    "iron condor backtest",
    "options trading research",
    "walk-forward analysis",
    "monte carlo backtest",
  ],
  // no root canonical: Next metadata inherits into child segments, which
  // would declare every subpage a duplicate of `/` — each page carries its
  // own (the landing's lives in app/page.tsx)
  icons: {
    // owner-picked: the ink-black tile with the centered white S.
    // ?v=2 = brand kit v2 (2026-07-16): same filenames, new bytes — without
    // it, favicon caches and OG scrapers keep serving the old mark. Bump on
    // every future kit import.
    icon: [
      { url: "/favicon.ico?v=2", sizes: "48x48" },
      { url: "/favicon-dark-tile-32.png?v=2", type: "image/png", sizes: "32x32" },
      { url: "/favicon-dark-tile-512.png?v=2", type: "image/png", sizes: "512x512" },
    ],
    apple: "/favicon-dark-tile-180.png?v=2",
  },
  openGraph: {
    title: "Skeptic — The Options Backtester That Argues With You",
    description:
      "Pitch a trade in plain English. Get the honest read — refusals included.",
    url: "https://skeptic.fyi",
    siteName: "Skeptic",
    type: "website",
    locale: "en_US",
    images: ["/og-image-1200x630.png?v=2"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Skeptic — The Options Backtester That Argues With You",
    description:
      "Pitch a trade in plain English. Get the honest read — refusals included.",
    images: ["/og-image-1200x630.png?v=2"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      // scroll-smooth: the landing's in-page anchors (pricing CTA → composer,
      // scroll cue → how-it-argues) glide; the app shell never window-scrolls
      // so it's inert there
      className={`${archivo.variable} ${plexMono.variable} ${newsreader.variable} scroll-smooth`}
      suppressHydrationWarning
    >
      <head>
        {/* apply Appearance before first paint — no theme flash. Mirrors
            resolveTheme() in lib/settings.ts: Market Hours (default/unset) is
            light 8am–6pm New York, dark otherwise. The landing at `/` keeps
            its own preference key (sk-landing-theme, launch L4) so its footer
            control never rewrites app settings; accent stays shared. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var s=JSON.parse(localStorage.getItem('skeptic-settings')||'{}');" +
              // public surface (landing + auth + legal) follows sk-landing-theme;
              // mirrors isPublicSurface() in lib/public-surface.ts
              "var pub=location.pathname==='/'||/^\\/(signin|signup|verify|terms|privacy|refunds)(\\/|$)/.test(location.pathname);" +
              "var t=pub?(localStorage.getItem('sk-landing-theme')||'market'):s.theme;" +
              "var eff;" +
              "if(t==='light'||t==='dark'){eff=t;}else{" +
              "var h=Number(new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York'," +
              "hour12:false,hour:'2-digit'}).format(new Date()))%24;" +
              "eff=(h>=8&&h<18)?'light':'dark';}" +
              "document.documentElement.dataset.theme=eff;" +
              "document.documentElement.dataset.accent=['sage','lavender','rose'].includes(s.accent)?s.accent:'cyan';}catch(e){}",
          }}
        />
      </head>
      {/* the viewport-locked app shell (nav rail + scroll-caged main) lives
          in app/(app)/layout.tsx — the landing at `/` scrolls the window */}
      <body className="bg-ground font-sans text-ink antialiased">
        <ThemeApplier />
        {children}
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
