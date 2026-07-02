import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono } from "next/font/google";

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

export const metadata: Metadata = {
  title: "Skeptic",
  description:
    "The backtester that argues with you. Research tool, not financial advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${plexMono.variable}`}>
      <body className="h-screen overflow-hidden bg-ground font-sans text-ink antialiased">
        <div className="flex h-full">
          <NavRail />
          <main className="flex-1 overflow-auto">
            <div className="mx-auto max-w-shell px-[30px] pb-10 pt-8">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
