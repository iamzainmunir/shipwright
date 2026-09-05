"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  type Agent,
  type Blocker,
  type Integration,
  type MemoryItem,
  type Mission,
  type ModelConnection,
  type Skill,
  listAgents,
  listBlockers,
  listIntegrations,
  listMemory,
  listMissions,
  listModelConnections,
  listSkills,
} from "@/lib/foundry";

/** Collections the shell needs for nav counts, the command palette, and notifications.
 *  Fetched once, refreshable after a mutation (e.g. dispatching a new mission). */
interface ShellData {
  missions: Mission[];
  agents: Agent[];
  models: ModelConnection[];
  skills: Skill[];
  memories: MemoryItem[];
  integrations: Integration[];
  blockers: Blocker[];
  loaded: boolean;
  refresh: () => void;
}

function emptyState(): Omit<ShellData, "refresh"> {
  return {
    missions: [], agents: [], models: [], skills: [], memories: [], integrations: [], blockers: [],
    loaded: false,
  };
}

const ShellDataContext = createContext<ShellData | null>(null);

export function ShellDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<ShellData, "refresh">>(emptyState);

  const refresh = useCallback(() => {
    Promise.allSettled([
      listMissions(), listAgents(), listModelConnections(), listSkills(),
      listMemory(), listIntegrations(), listBlockers(),
    ]).then(([m, a, mo, s, me, i, b]) => {
      const val = <T,>(r: PromiseSettledResult<T[]>): T[] => (r.status === "fulfilled" ? r.value : []);
      setState({
        missions: val(m), agents: val(a), models: val(mo), skills: val(s),
        memories: val(me), integrations: val(i), blockers: val(b), loaded: true,
      });
    });
  }, []);

  // Load once, then poll so the nav badges (Missions/Team/… counts) stay live — a mission created,
  // shipped or deleted anywhere updates the sidebar within a few seconds without a manual refresh.
  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 4000);
    return () => window.clearInterval(id);
  }, [refresh]);

  return <ShellDataContext.Provider value={{ ...state, refresh }}>{children}</ShellDataContext.Provider>;
}

export function useShellData(): ShellData {
  const ctx = useContext(ShellDataContext);
  if (!ctx) throw new Error("useShellData must be used within <ShellDataProvider>");
  return ctx;
}
