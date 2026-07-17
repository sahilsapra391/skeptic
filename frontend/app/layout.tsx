import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";

import { ThemeApplier } from "@/components/theme-applier";
// launch L1: accounts exist only when the Clerk keys do — without them the
// provider is skipped entirely and the app is the single-user build
import { CLERK_ENABLED } from "@/lib/clerk";

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
  title: "Skeptic",
  description:
    "The backtester that argues with you. Research tool, not financial advice.",
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
    title: "Skeptic",
    description: "The backtester that argues with you.",
    images: ["/og-image-1200x630.png?v=2"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const shell = (
    <html
      lang="en"
      className={`${archivo.variable} ${plexMono.variable} ${newsreader.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* apply Appearance before first paint — no theme flash. Mirrors
            resolveTheme() in lib/settings.ts: Market Hours (default/unset) is
            light 8am–6pm New York, dark otherwise. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var s=JSON.parse(localStorage.getItem('skeptic-settings')||'{}');" +
              "var t=s.theme,eff;" +
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
  return CLERK_ENABLED ? <ClerkProvider>{shell}</ClerkProvider> : shell;
}
