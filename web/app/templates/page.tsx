import { Suspense } from "react";
import { Templates } from "@/components/recon/Templates";

export default function Page() {
  return <Suspense fallback={null}><Templates /></Suspense>;
}
