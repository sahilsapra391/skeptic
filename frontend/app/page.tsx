import type { Metadata } from "next";

import { LandingPage } from "@/components/landing/landing-page";
import { StructuredData } from "@/components/landing/structured-data";

export const metadata: Metadata = {
  // title + description inherit the root defaults — the root layout's
  // defaults ARE the landing copy. The canonical lives here (not in the
  // root layout) so child routes never inherit it.
  alternates: { canonical: "/" },
};

export default function Landing() {
  return (
    <>
      {/* server-rendered JSON-LD — must stay a sibling of the client
          assembly, never its child */}
      <StructuredData />
      <LandingPage />
    </>
  );
}
