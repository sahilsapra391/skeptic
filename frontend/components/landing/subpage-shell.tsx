import Link from "next/link";

/**
 * Shared frame for the landing's satellite pages (signup/signin/legal):
 * wordmark home-link on top, centered narrow column, window scroll.
 * Serif is reserved for the page h1 (typography rule).
 */
export function SubpageShell({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-ground">
      <div className="mx-auto flex max-w-[720px] flex-col px-6 pb-20 pt-10">
        <Link href="/" className="inline-block" aria-label="Skeptic home">
          {/* theme-appropriate mark: white on dark, black on light — pure
              CSS swap so this stays a server component (no theme hook) */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/wordmark-white.svg"
            alt="SK=PT/C"
            className="h-[14px] w-auto [[data-theme=light]_&]:hidden"
          />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/wordmark-black.svg"
            alt=""
            aria-hidden
            className="hidden h-[14px] w-auto [[data-theme=light]_&]:block"
          />
        </Link>
        {kicker && (
          <div className="mt-14 font-mono text-[11.5px] font-medium tracking-[.14em] text-trust">
            {kicker}
          </div>
        )}
        <h1 className="mt-3 font-serif text-[34px] font-medium leading-[1.15] text-ink">
          {title}
        </h1>
        <div className="mt-6 text-[14.5px] leading-[1.7] text-ink-2">{children}</div>
      </div>
    </div>
  );
}
