/**
 * Public runtime configuration.
 *
 * Only `NEXT_PUBLIC_*` values may be read here — this module is imported by client
 * components, so it MUST NOT reference server-only secrets (09-frontend.md §A4/§A5).
 * `NEXT_PUBLIC_API_BASE` points at the orchestrator (default port 8000, docs/VERSIONS.md);
 * in production every browser request is proxied through the Next BFF instead.
 */

const DEFAULT_API_BASE = "http://localhost:8000";

/** Base URL for the orchestrator API, without a trailing slash. */
export function getApiBase(): string {
  // Reference the literal key so Next can statically inline it into the client bundle.
  const raw = process.env.NEXT_PUBLIC_API_BASE;
  const base = raw && raw.trim() ? raw.trim() : DEFAULT_API_BASE;
  return base.replace(/\/+$/, "");
}

export const env = {
  apiBase: getApiBase(),
} as const;
