"use client";

import { useParams } from "next/navigation";
import { RunDetail } from "@/components/recon/Runs";

/** /results/<name>-<number> — one reconciliation result */
export default function Page() {
  const { result } = useParams<{ result: string }>();
  return <RunDetail slug={result} />;
}
