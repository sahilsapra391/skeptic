/**
 * Shared building blocks for the legal pages (terms / privacy / refunds).
 * Section headings are sans (Archivo) — the serif stays reserved for the
 * page h1 in SubpageShell (typography rule).
 */

export function Updated({ date }: { date: string }) {
  return (
    <p className="mb-2 font-mono text-[11.5px] text-ink-4">Last updated: {date}</p>
  );
}

export function Lead({ children }: { children: React.ReactNode }) {
  return <p className="text-[14.5px] leading-[1.7] text-ink-2">{children}</p>;
}

export function Section({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-8">
      <h2 className="text-[15px] font-semibold text-ink">
        {n}. {title}
      </h2>
      <div className="mt-2 space-y-3 text-[14px] leading-[1.7] text-ink-2">{children}</div>
    </section>
  );
}

export function Bullets({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="ml-4 list-disc space-y-1.5 text-[14px] leading-[1.65] text-ink-2 marker:text-ink-4">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}
