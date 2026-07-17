"use client";

/**
 * New Analysis — the /new page mount of the shared run flow. The flow
 * itself lives in components/run-flow.tsx and is also embedded by the
 * landing's run popup (launch L4).
 */

import { RunFlow } from "@/components/run-flow";

export default function NewAnalysisPage() {
  return <RunFlow />;
}
