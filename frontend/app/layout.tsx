import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Newsreader } from "next/font/google";

import { NavRail } from "@/components/nav-rail";

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
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${plexMono.variable} ${newsreader.variable}`}>
      <body className="h-screen overflow-hidden bg-ground font-sans text-ink antialiased">
        <div className="flex h-full">
          <NavRail />
          <main className="flex-1 overflow-auto">
            <div className="mx-auto max-w-shell px-[34px] pb-10 pt-8">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
