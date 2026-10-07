import { Suspense, type ReactNode } from "react";
import { ModelView } from "@/components/model/ModelView";

/* /dashboards/<dashboard>/model/<view> — the model documentation, one tab per view */
export default function ModelLayout({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}><ModelView />{children}</Suspense>;
}
