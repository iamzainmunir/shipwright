import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ShellProviders } from "@/components/shell/providers";

/**
 * Dashboard shell — the mission-control layout (sidebar + topbar + overlays) shared by every
 * `/dashboard/*` route. Client shell contexts (theme, toasts, command palette, new-mission)
 * wrap the routed view.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <ShellProviders>
      <AppShell>{children}</AppShell>
    </ShellProviders>
  );
}
