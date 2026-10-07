"use client";

import { useParams } from "next/navigation";
import { RunsList } from "@/components/recon/Runs";
import { recordId } from "@/lib/routes";

/** /templates/<name>-<number> — the run history of one template */
export default function Page() {
  const { template } = useParams<{ template: string }>();
  return <RunsList template={recordId("template", template)} />;
}
