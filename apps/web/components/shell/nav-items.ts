import type { IconName } from "@foundry/ui";
import type {
  Agent, Integration, MemoryItem, Mission, ModelConnection, Skill,
} from "@/lib/foundry";

export type CountKey = "missions" | "team" | "models" | "skills" | "memory" | "integrations";

export interface NavItem {
  label: string;
  href: string;
  icon: IconName;
  countKey?: CountKey;
}

export interface NavGroup {
  heading: string;
  items: NavItem[];
}

/** Sidebar + command-palette navigation, grouped exactly as the mockup. */
export const NAV: NavGroup[] = [
  {
    heading: "Workspace",
    items: [
      { label: "Command Center", href: "/dashboard", icon: "grid" },
      { label: "Missions", href: "/dashboard/missions", icon: "kanban", countKey: "missions" },
      { label: "Tickets", href: "/dashboard/tickets", icon: "ticket" },
      { label: "Team", href: "/dashboard/team", icon: "users", countKey: "team" },
    ],
  },
  {
    heading: "Intelligence",
    items: [
      { label: "Models", href: "/dashboard/models", icon: "cpu", countKey: "models" },
      { label: "Skills", href: "/dashboard/skills", icon: "sparkles", countKey: "skills" },
      { label: "Memory", href: "/dashboard/memory", icon: "brain", countKey: "memory" },
    ],
  },
  {
    heading: "Platform",
    items: [
      { label: "Integrations", href: "/dashboard/integrations", icon: "plug", countKey: "integrations" },
      { label: "Settings", href: "/dashboard/settings", icon: "gear" },
    ],
  },
];

interface CountSource {
  missions: Mission[];
  agents: Agent[];
  models: ModelConnection[];
  skills: Skill[];
  memories: MemoryItem[];
  integrations: Integration[];
}

/** Live nav badge counts, matching the mockup's count functions. */
export function computeCounts(d: CountSource): Record<CountKey, number> {
  return {
    missions: d.missions.filter((m) => m.stage !== "shipped").length,
    team: d.agents.length,
    models: d.models.filter((m) => m.status === "connected").length,
    skills: d.skills.filter((s) => s.installed).length,
    memory: d.memories.length,
    integrations: d.integrations.filter((i) => i.status === "connected").length,
  };
}
