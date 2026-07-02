"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const ITEMS: { href: string; title: string; icon: React.ReactNode }[] = [
  {
    href: "/",
    title: "New analysis",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <line x1="10" y1="4.5" x2="10" y2="15.5" />
        <line x1="4.5" y1="10" x2="15.5" y2="10" />
      </svg>
    ),
  },
  {
    href: "/library",
    title: "Library",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
        <rect x="3.5" y="3.5" width="13" height="5" rx="1.5" />
        <rect x="3.5" y="11.5" width="13" height="5" rx="1.5" />
      </svg>
    ),
  },
  {
    href: "/data",
    title: "Data observatory",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <line x1="5" y1="16" x2="5" y2="9" />
        <line x1="10" y1="16" x2="10" y2="4.5" />
        <line x1="15" y1="16" x2="15" y2="12" />
      </svg>
    ),
  },
  {
    href: "/settings",
    title: "Settings",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
        <line x1="3.5" y1="6.5" x2="16.5" y2="6.5" strokeLinecap="round" />
        <circle cx="12" cy="6.5" r="2.1" fill="#15181d" />
        <line x1="3.5" y1="13.5" x2="16.5" y2="13.5" strokeLinecap="round" />
        <circle cx="7.5" cy="13.5" r="2.1" fill="#15181d" />
      </svg>
    ),
  },
];

export function NavRail() {
  const pathname = usePathname();
  const activeFor = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/runs") : pathname.startsWith(href);

  return (
    <nav className="flex w-14 flex-none flex-col items-center gap-2 border-r border-line-softer bg-navbg py-3.5">
      <div className="mb-3.5 flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-trust-border font-mono text-[15px] font-semibold text-trust">
        S
      </div>
      {ITEMS.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          title={item.title}
          className={clsx(
            "flex h-[38px] w-[38px] items-center justify-center rounded-[10px]",
            activeFor(item.href) ? "bg-trust-dim text-trust" : "text-ink-3 hover:text-ink",
          )}
        >
          {item.icon}
        </Link>
      ))}
    </nav>
  );
}
