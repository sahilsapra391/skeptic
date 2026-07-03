"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

import { listRuns } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

const ITEMS: { href: string; title: string; icon: React.ReactNode }[] = [
  {
    href: "/",
    title: "New Analysis",
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
    title: "Data Observatory",
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

// resizable sidebar geometry: below COLLAPSE_AT it snaps to the icon
// rail; an open sidebar never sits narrower than SNAP_OPEN
const RAIL_W = 56;
const COLLAPSE_AT = 120;
const SNAP_OPEN = 172;
const MAX_W = 380;
const DEFAULT_W = 196;

export function NavRail() {
  const pathname = usePathname();
  // always open on load — width changes last for the session only
  const [width, setWidth] = useState(DEFAULT_W);
  const [dragging, setDragging] = useState(false);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const lastOpenW = useRef(DEFAULT_W);
  const open = width >= COLLAPSE_AT;

  const toggle = () =>
    setWidth((w) => (w >= COLLAPSE_AT ? RAIL_W : lastOpenW.current));

  // fully imperative drag: listeners attach synchronously in the same
  // pointerdown and the settle uses the event's own coordinates, so no
  // effect timing or stale state can lose the snap
  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    setDragging(true);
    const clampX = (x: number) => Math.min(MAX_W, Math.max(RAIL_W, x));
    const onMove = (ev: PointerEvent) => setWidth(clampX(ev.clientX));
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setDragging(false);
      const w = clampX(ev.clientX);
      if (w < COLLAPSE_AT) {
        setWidth(RAIL_W);
      } else {
        const settled = Math.max(w, SNAP_OPEN);
        lastOpenW.current = settled;
        setWidth(settled);
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  useEffect(() => {
    listRuns()
      .then(({ runs }) => setRecent(runs.slice(0, 6)))
      .catch(() => undefined);
    // refreshes on navigation, so a just-finished run shows up
  }, [pathname]);

  const activeFor = (href: string) => {
    if (href === "/") return pathname === "/";
    // saved runs are library entries — keep Library lit while reading one
    if (href === "/library") return pathname.startsWith("/library") || pathname.startsWith("/runs");
    return pathname.startsWith(href);
  };

  return (
    <nav
      style={{ width }}
      className={clsx(
        "relative flex flex-none flex-col gap-2 border-r border-line-softer bg-navbg py-3.5",
        !dragging && "transition-[width] duration-150",
        open ? "px-2.5" : "items-center",
        dragging && "select-none",
      )}
    >
      <div className={clsx("mb-4 flex h-[40px] items-center", open && "px-1.5")}>
        {open ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src="/brand/wordmark-white.svg" alt="Skeptic" className="h-[28px] w-auto" draggable={false} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src="/brand/s-mark-white.svg" alt="Skeptic" className="h-[32px] w-auto" draggable={false} />
        )}
      </div>
      {ITEMS.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          title={item.title}
          className={clsx(
            "flex h-[38px] items-center rounded-[10px]",
            open ? "w-full gap-3 px-2.5" : "w-[38px] justify-center",
            activeFor(item.href) ? "bg-trust-dim text-trust" : "text-ink-3 hover:text-ink",
          )}
        >
          <span className="flex-none">{item.icon}</span>
          {open && <span className="truncate text-[13px] font-semibold">{item.title}</span>}
        </Link>
      ))}
      {open && recent.length > 0 && (
        <div className="mt-5 flex min-h-0 flex-col overflow-hidden">
          <div className="mb-1.5 px-2.5 font-mono text-[10px] font-medium tracking-[.14em] text-ink-5">
            RECENT ANALYSES
          </div>
          <div className="flex flex-col gap-[2px] overflow-y-auto">
            {recent.map((r) => (
              <Link
                key={r.id}
                href={`/runs/${r.id}`}
                title={r.name}
                className={clsx(
                  "truncate rounded-[8px] px-2.5 py-[7px] text-[12.5px]",
                  pathname === `/runs/${r.id}`
                    ? "bg-raised-2 text-ink"
                    : "text-ink-4 hover:bg-raised hover:text-ink-2",
                )}
              >
                {r.name}
              </Link>
            ))}
          </div>
        </div>
      )}
      <button
        onClick={toggle}
        aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
        title={open ? "Collapse sidebar" : "Expand sidebar"}
        className={clsx(
          "mt-auto flex h-[38px] items-center rounded-[10px] text-ink-4 hover:bg-raised-2 hover:text-ink",
          open ? "w-full gap-3 px-2.5" : "w-[38px] justify-center",
        )}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={clsx("flex-none transition-transform", open && "rotate-180")}
        >
          <path d="M8 6l4 4-4 4" />
        </svg>
        {open && <span className="text-[13px] font-semibold">Collapse</span>}
      </button>
      <div
        onPointerDown={startDrag}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        className={clsx(
          "absolute -right-[3px] top-0 z-20 h-full w-[7px] cursor-col-resize",
          dragging ? "bg-trust/30" : "hover:bg-trust/20",
        )}
      />
    </nav>
  );
}
