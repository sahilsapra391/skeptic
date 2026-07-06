import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";

import { BootSplash } from "@/components/boot-splash";
import { NavRail } from "@/components/nav-rail";
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
  title: "Skeptic",
  description:
    "The backtester that argues with you. Research tool, not financial advice.",
  icons: {
    // owner-picked: the ink-black tile with the centered white S
    icon: [
      { url: "/favicon.ico", sizes: "48x48" },
      { url: "/favicon-dark-tile-32.png", type: "image/png", sizes: "32x32" },
      { url: "/favicon-dark-tile-512.png", type: "image/png", sizes: "512x512" },
    ],
    apple: "/favicon-dark-tile-180.png",
  },
  openGraph: {
    title: "Skeptic",
    description: "The backtester that argues with you.",
    images: ["/og-image-1200x630.png"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
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
      <body className="h-screen overflow-hidden bg-ground font-sans text-ink antialiased">
        <ThemeApplier />
        <BootSplash />
        <div className="flex h-full">
          <NavRail />
          <main className="flex-1 overflow-auto">
            <div className="mx-auto max-w-shell px-[34px] pb-10 pt-8">{children}</div>
          </main>
        </div>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
