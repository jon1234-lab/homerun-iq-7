import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "HomerunIQ — MLB Home Run Intelligence",
  description: "Real-time MLB home run probability analytics powered by live Statcast data.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-diamond-950 text-white">
        <header className="sticky top-0 z-10 border-b border-white/10 bg-diamond-950/85 backdrop-blur">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3.5">
            <Link href="/" className="flex items-center gap-2 text-lg font-bold">
              <span className="text-emerald-400">⚾</span> HomerunIQ
            </Link>
            <nav className="flex items-center gap-4 text-sm text-gray-300 sm:gap-6">
              <Link href="/" className="transition-colors hover:text-white">
                Board
              </Link>
              <Link href="/live" className="transition-colors hover:text-white">
                Live
              </Link>
              <Link href="/games" className="hidden transition-colors hover:text-white sm:inline">
                Games
              </Link>
              <Link
                href="/upgrade"
                className="rounded-full bg-emerald-500 px-3.5 py-1.5 font-semibold text-black transition-colors hover:bg-emerald-400"
              >
                Upgrade
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6 sm:py-8">{children}</main>
        <footer className="mx-auto max-w-5xl px-4 pb-10 pt-4 text-center text-[11px] leading-relaxed text-gray-600">
          Model estimates for entertainment and analysis. Data from the MLB Stats API, Baseball
          Savant, and Open-Meteo. Not affiliated with MLB.
        </footer>
      </body>
    </html>
  );
}
