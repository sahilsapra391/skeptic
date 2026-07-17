import type { MetadataRoute } from "next";

// SEO (launch L4): the indexable face is the landing + the auth placeholder
// pages + legal. App surfaces under (app)/ are noindexed in their layout and
// /api/ is disallowed in robots.ts — neither belongs here. Public run pages
// join only when a server-rendered share surface exists.
const BASE = "https://skeptic.fyi";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${BASE}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${BASE}/signup`, changeFrequency: "weekly", priority: 0.5 },
    { url: `${BASE}/signin`, changeFrequency: "weekly", priority: 0.5 },
    { url: `${BASE}/terms`, changeFrequency: "monthly", priority: 0.3 },
    { url: `${BASE}/privacy`, changeFrequency: "monthly", priority: 0.3 },
    { url: `${BASE}/refunds`, changeFrequency: "monthly", priority: 0.3 },
  ];
}
