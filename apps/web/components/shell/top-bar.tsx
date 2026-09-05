"use client";

import { Button, Icon } from "@foundry/ui";
import { useRouter } from "next/navigation";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme";
import { BRAND } from "@/lib/brand";
import { useCommandPalette } from "./command-palette";
import { useNewMission } from "./new-mission";
import { Notifications } from "./notifications";

/** Breadcrumb title for a path (mirrors the mockup crumbMap). */
function crumbFor(pathname: string): string {
  if (pathname === "/dashboard") return "Command Center";
  if (/^\/dashboard\/missions\/[^/]+$/.test(pathname)) return "Live Build";
  if (pathname.startsWith("/dashboard/missions")) return "Missions";
  if (pathname.startsWith("/dashboard/team")) return "Team";
  if (pathname.startsWith("/dashboard/models")) return "Models & Connections";
  if (pathname.startsWith("/dashboard/skills")) return "Skills";
  if (pathname.startsWith("/dashboard/memory")) return "Memory";
  if (pathname.startsWith("/dashboard/integrations")) return "Integrations";
  if (pathname.startsWith("/dashboard/settings")) return "Settings";
  return BRAND.name;
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const palette = useCommandPalette();
  const newMission = useNewMission();

  return (
    <header className="topbar">
      <button type="button" className="icon-btn menu-btn" onClick={onMenu} aria-label="Menu">
        <Icon name="menu" size={18} />
      </button>
      <div className="crumb">{crumbFor(pathname)}</div>
      <div className="topbar-spacer" />

      <button type="button" className="search-btn" onClick={palette.open} aria-label="Search">
        <Icon name="search" size={16} />
        <span>Search…</span>
        <kbd>⌘K</kbd>
      </button>

      <button type="button" className="icon-btn" data-tip="Spend this month" aria-label="Spend"
        onClick={() => router.push("/dashboard/models")}>
        <Icon name="dollar" size={17} />
      </button>

      <Notifications />
      <ThemeToggle />

      <Button variant="primary" onClick={newMission.open}>
        <Icon name="plus" size={16} /> New mission
      </Button>
    </header>
  );
}
