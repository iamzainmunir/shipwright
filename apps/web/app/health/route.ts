import { NextResponse } from "next/server";

/**
 * App-level liveness probe for the web service itself (distinct from the orchestrator's
 * `/healthz`, which the dashboard queries via lib/api). Used by container/orchestration
 * health checks. Always dynamic so it reflects the running process, never a build snapshot.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ status: "ok" });
}
