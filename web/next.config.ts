import type { NextConfig } from "next";

const q = (key: string, value = `(?<${key}>.+)`) => ({ type: "query" as const, key, value });

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  devIndicators: false,
  // lets a production build run beside `next dev` (NEXT_DIST_DIR=.next-build npm run build)
  distDir: process.env.NEXT_DIST_DIR || ".next",

  /*
   * Links from before the readable URLs (bookmarks, shared links). They land
   * on the new address with the raw id in it; the page then swaps in the
   * readable name (see lib/routes.ts).
   */
  async redirects() {
    return [
      // workspace: /?c=<connection>&p=<page>&m=<object>
      { source: "/", has: [q("c"), q("p"), q("m")], destination: "/dashboards/:c/pages/:p/objects/:m", permanent: false },
      { source: "/", has: [q("c"), q("m")], destination: "/dashboards/:c/objects/:m", permanent: false },
      { source: "/", has: [q("c"), q("p")], destination: "/dashboards/:c/pages/:p", permanent: false },
      { source: "/", has: [q("c")], destination: "/dashboards/:c", permanent: false },
      { source: "/dashboards", destination: "/", permanent: false },  // the home page lists the dashboards
      // model views: /models/<connection>?tab=<tab>
      { source: "/models/:cid", has: [q("tab", "report")], destination: "/dashboards/:cid/model/report-map", permanent: false },
      { source: "/models/:cid", has: [q("tab", "health")], destination: "/dashboards/:cid/model/health-check", permanent: false },
      { source: "/models/:cid", has: [q("tab", "(?<tab>measures|tables|relationships)")], destination: "/dashboards/:cid/model/:tab", permanent: false },
      { source: "/models/:cid", destination: "/dashboards/:cid/model", permanent: false },
      { source: "/models", destination: "/", permanent: false },
      // results
      { source: "/runs", has: [q("template")], destination: "/templates/:template", permanent: false },
      { source: "/runs", destination: "/results", permanent: false },
      { source: "/runs/:id", destination: "/results/:id", permanent: false },
      // lineage explorer: /lineage?c=<connection>&n=<object>
      { source: "/lineage", has: [q("c"), q("n")], destination: "/lineage/:c/objects/:n", permanent: false },
      { source: "/lineage", has: [q("c")], destination: "/lineage/:c", permanent: false },
    ];
  },
};

export default nextConfig;
