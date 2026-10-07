import { Suspense } from "react";
import { LineagePage } from "@/components/lineage/LineagePage";

export default function Page() {
  return <Suspense fallback={null}><LineagePage /></Suspense>;
}
