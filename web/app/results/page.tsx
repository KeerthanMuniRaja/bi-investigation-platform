import { Suspense } from "react";
import { RunsList } from "@/components/recon/Runs";

/** /results — every reconciliation run, newest first */
export default function Page() {
  return <Suspense fallback={null}><RunsList /></Suspense>;
}
