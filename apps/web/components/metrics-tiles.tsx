"use client";

import { useEffect, useState } from "react";
import { Icon, type IconName } from "@foundry/ui";
import { type Metrics, getMetrics } from "@/lib/foundry";
import { isApiError } from "@/lib/api";

/**
 * Command-Center stat tiles — the four org-pulse counters at the top of the dashboard.
 * Fed by `GET /api/v1/metrics` and polled every 5s so the numbers stay live. Only counts the
 * API actually returns are shown; no trend deltas are invented.
 */

interface Tile {
  icon: IconName;
  /** `tint-*` helper applied to the tile's icon chip. */
  tint: string;
  label: string;
  value: (m: Metrics) => string;
  note: string;
}

const TILES: Tile[] = [
  { icon: "rocket", tint: "tint-green", label: "Shipped", value: (m) => String(m.shipped), note: "merged to main" },
  { icon: "kanban", tint: "tint-brand", label: "Missions in flight", value: (m) => String(m.activeMissions), note: "spec → review" },
  { icon: "users", tint: "tint-cyan", label: "Agents active", value: (m) => `${m.agentsWorking}/${m.agents}`, note: "on shift now" },
  { icon: "bolt", tint: "tint-amber", label: "Runs", value: (m) => String(m.runs), note: "started to date" },
];

export function MetricsTiles() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getMetrics()
        .then((d) => {
          if (!alive) return;
          setMetrics(d);
          setError(null);
        })
        .catch((e) => {
          if (alive) setError(isApiError(e) ? e.message : "Failed to load metrics");
        });
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Only surface the error before the first successful load; a transient poll failure keeps the
  // last-good tiles on screen rather than blanking the whole row.
  if (error && !metrics) return <div className="lb-error mb-20">{error}</div>;

  return (
    <div className="grid g-4 mb-20">
      {TILES.map((t) => (
        <div key={t.label} className="stat">
          <div className={`stat-ic ${t.tint}`}>
            <Icon name={t.icon} size={17} />
          </div>
          <div className="stat-label">{t.label}</div>
          <div className="stat-value">{metrics ? t.value(metrics) : "—"}</div>
          <div className="stat-trend">
            <span className="faint">{t.note}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
