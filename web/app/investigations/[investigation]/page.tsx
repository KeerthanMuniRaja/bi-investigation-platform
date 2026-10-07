"use client";

import { useParams } from "next/navigation";
import { InvestigationDetail } from "@/components/recon/Investigations";

/** /investigations/<question>-<number> — one investigation case */
export default function Page() {
  const { investigation } = useParams<{ investigation: string }>();
  return <InvestigationDetail slug={investigation} />;
}
