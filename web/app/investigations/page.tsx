import { Suspense } from "react";
import { InvestigationsList } from "@/components/recon/Investigations";

export default function Page() {
  return <Suspense fallback={null}><InvestigationsList /></Suspense>;
}
