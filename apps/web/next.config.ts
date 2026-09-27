import type { NextConfig } from "next";

/**
 * Shipwright web app config.
 *
 * `transpilePackages` is REQUIRED by the monorepo integration contract: the shared
 * workspace packages ship raw TS/CSS and are compiled by the app's build, not
 * pre-built. See docs/VERSIONS.md.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@foundry/ui", "@foundry/api-types"],
  // Allow importing the repo-root `brand.json` (the single canonical brand source) from lib/brand.ts.
  experimental: { externalDir: true },
  // The browser never calls the orchestrator directly (09-frontend.md §A1/§A4):
  // no cross-origin fetches are declared here; the BFF proxies /api/* in a later phase.
  poweredByHeader: false,
};

export default nextConfig;
