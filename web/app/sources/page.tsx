import { Suspense } from "react";
import { Sources } from "@/components/recon/Sources";

export default function Page() {
  return <Suspense fallback={null}><Sources /></Suspense>;
}
