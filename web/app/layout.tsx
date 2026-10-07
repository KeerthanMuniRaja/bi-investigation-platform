import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Suspense } from "react";
import { Shell } from "@/components/shell/Shell";
import "./styles/base.css";
import "./styles/lineage.css";
import "./styles/workspace.css";
import "./styles/next.css";

// fonts are downloaded at build time and served from this app — nothing
// is fetched from Google when the app runs
const sans = Inter({ variable: "--font-sans", subsets: ["latin"] });
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "BI Trust Platform",
  description: "Explore Power BI dashboards: pages, measures, lineage, health checks and reconciliation.",
};

// apply the saved theme before first paint (no light/dark flash)
const themeScript = `try{var t=localStorage.getItem("bip-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <Suspense fallback={<div className="app-boot"><span className="spinner" /></div>}>
          <Shell>{children}</Shell>
        </Suspense>
      </body>
    </html>
  );
}
