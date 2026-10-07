import { Suspense } from "react";
import { Workspace } from "@/components/workspace/Workspace";

/** Dashboard workspace: /?c=<dashboard>&p=<page>&m=<measure> */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Workspace />
    </Suspense>
  );
}
