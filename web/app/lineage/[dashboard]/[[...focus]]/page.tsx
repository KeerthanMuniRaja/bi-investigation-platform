import { Suspense } from "react";
import { LineagePage } from "@/components/lineage/LineagePage";

/** /lineage/<dashboard>[/measures/<measure> | /tables/<table>[/columns/<column>] | ...] */
export default function Page() {
  return <Suspense fallback={null}><LineagePage /></Suspense>;
}
