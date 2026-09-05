"use client";

import { Icon } from "@foundry/ui";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { BRAND } from "@/lib/brand";
import { type AutonomyLevel, getSettings } from "@/lib/foundry";
import { NAV, computeCounts } from "./nav-items";
import { useShellData } from "./shell-data";

const AUTONOMY_LABEL: Record<AutonomyLevel, string> = {
  manual: "Manual", assisted: "Assisted", supervised: "Supervised", autonomous: "Autonomous",
};
const AUTONOMY_PCT: Record<AutonomyLevel, number> = {
  manual: 25, assisted: 50, supervised: 70, autonomous: 100,
};

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SideNav({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  const data = useShellData();
  const counts = computeCounts(data);
  const [autonomy, setAutonomy] = useState<AutonomyLevel>("supervised");

  useEffect(() => {
    getSettings().then((s) => setAutonomy(s.autonomy)).catch(() => {});
  }, []);

  // "In flight" = actively being worked (not backlog, not shipped) — distinct from the nav badge.
  const inFlight = data.missions.filter((m) =>
    ["spec", "building", "review", "qa"].includes(m.stage),
  ).length;

  return (
    <aside className={open ? "sidebar open" : "sidebar"} aria-label="Primary">
      <Link href="/dashboard" className="brand" aria-label={`${BRAND.name} — dashboard`} onClick={onNavigate}>
        <div className="brand-logo">
          <svg width="20" height="20" viewBox="0 0 64 64" fill="none" aria-hidden="true">
            <path d="M24 20 L14 32 L24 44" stroke="currentColor" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
            <path d="M40 20 L50 32 L40 44" stroke="currentColor" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
            <path d="M32 23 L33.7 30.3 L41 32 L33.7 33.7 L32 41 L30.3 33.7 L23 32 L30.3 30.3 Z" fill="currentColor" />
          </svg>
        </div>
        <div>
          <div className="brand-name">{BRAND.name}</div>
          <div className="brand-sub">{BRAND.tagline}</div>
        </div>
      </Link>

      {NAV.map((group) => (
        <div key={group.heading}>
          <div className="nav-group-label">{group.heading}</div>
          {group.items.map((item) => {
            const active = isActive(pathname, item.href);
            const count = item.countKey ? counts[item.countKey] : undefined;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                className={active ? "nav-item active" : "nav-item"}
                aria-current={active ? "page" : undefined}
              >
                <span className="nav-ico"><Icon name={item.icon} size={18} /></span>
                <span>{item.label}</span>
                {count !== undefined && count > 0 && <span className="nav-count">{count}</span>}
              </Link>
            );
          })}
        </div>
      ))}

      <div className="sidebar-foot">
        <div className="autonomy-card">
          <div className="lbl">Global autonomy</div>
          <div className="val">
            <span className="dot pulse bg-brand" />
            {AUTONOMY_LABEL[autonomy]}
          </div>
          <div className="progress thin mt-8">
            <div className="bar" style={{ width: `${AUTONOMY_PCT[autonomy]}%` }} />
          </div>
          <div className="row between mt-8">
            <span className="faint text-xs">{inFlight} mission{inFlight === 1 ? "" : "s"} in flight</span>
            <Link href="/dashboard/settings" onClick={onNavigate} className="c-brand text-xs fw-6">Tune</Link>
          </div>
        </div>
      </div>
    </aside>
  );
}
