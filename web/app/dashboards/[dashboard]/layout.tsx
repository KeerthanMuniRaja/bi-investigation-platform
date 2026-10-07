import { Suspense, type ReactNode } from "react";
import { DashboardWorkspace } from "@/components/workspace/Workspace";

/*
 * /dashboards/<dashboard>/...  The workspace (lineage canvas, details panel)
 * lives in this layout so it stays mounted while you move between pages and
 * measures; the page files below only give each address its meaning.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}><DashboardWorkspace>{children}</DashboardWorkspace></Suspense>;
}
