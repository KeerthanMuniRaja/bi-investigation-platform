import Link from "next/link";
import { EmptyState, I } from "@/components/ui";

/** any address that isn't a page of the app (inside the normal shell) */
export default function NotFound() {
  return (
    <div className="card" style={{ marginTop: 24 }}>
      <EmptyState title="This page doesn't exist" icon={I.search}
        text="The address may be mistyped, or it points at something that was renamed or removed.">
        <div className="row" style={{ justifyContent: "center" }}>
          <Link className="btn primary" href="/">Go to your dashboards</Link>
          <Link className="btn" href="/results">Reconciliation results</Link>
        </div>
      </EmptyState>
    </div>
  );
}
