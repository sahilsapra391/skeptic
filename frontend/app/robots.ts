import type { MetadataRoute } from "next";

// /api/ is the only crawl-blocked path: the app screens under (app)/ handle
// themselves via robots noindex,follow (their layout), which crawlers can
// only read if allowed to fetch the page — so they stay crawlable here.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/api/"] }],
    sitemap: "https://skeptic.fyi/sitemap.xml",
  };
}
