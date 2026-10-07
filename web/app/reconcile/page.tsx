import { Suspense } from "react";
import { Wizard } from "@/components/recon/Wizard";

export default function Page() {
  return <Suspense fallback={null}><Wizard /></Suspense>;
}
