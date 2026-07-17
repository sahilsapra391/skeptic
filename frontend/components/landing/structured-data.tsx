// JSON-LD for the landing (launch L4). Server component — rendered once from
// app/page.tsx (server context; a client boundary can't parent it), emits ONE
// script tag, ships no client JS.
//
// Anti-hype rail: NO aggregateRating / review / interactionStatistic nodes,
// ever — we have none, and inventing them is a hard no. Every claim below is
// grounded in shipped behavior (engine guardrails 1–5) or the live price list
// ($0 anon trial → $10 one-time pack, hence AggregateOffer 0–10, offerCount 2).

const BASE = "https://skeptic.fyi";

const GRAPH = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "WebSite",
      "@id": `${BASE}/#website`,
      name: "Skeptic",
      url: `${BASE}/`,
      description:
        "The options backtester that argues with you. Pitch a trade in " +
        "plain English; get the honest read — refusals included.",
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${BASE}/#app`,
      name: "Skeptic",
      applicationCategory: "FinanceApplication",
      operatingSystem: "Web",
      url: `${BASE}/`,
      description:
        "Backtest options strategies in plain English on real intraday " +
        "options data. Skeptic interviews you instead of guessing at " +
        "ambiguous strategy details, fills at bid/ask (never mid), then " +
        "attacks its own result — held-out data, walk-forward windows, " +
        "Monte Carlo reshuffles, sensitivity nudges — and refuses to grade " +
        "strategies the evidence can't support. Research tool, not " +
        "financial advice.",
      offers: {
        "@type": "AggregateOffer",
        lowPrice: 0,
        highPrice: 10,
        priceCurrency: "USD",
        offerCount: 2,
      },
      publisher: {
        "@type": "Organization",
        name: "Skeptic",
        url: `${BASE}/`,
        logo: `${BASE}/og-image-1200x630.png`,
      },
    },
  ],
};

export function StructuredData() {
  return (
    <script
      type="application/ld+json"
      // "<" escaped so no payload can ever close the script tag — static
      // today, but the guard costs nothing if the graph grows dynamic fields
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(GRAPH).replace(/</g, "\\u003c"),
      }}
    />
  );
}
