"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";
import { SideNav } from "./side-nav";
import { TopBar } from "./top-bar";

/** The `.app` grid: sidebar + (topbar + routed view). Owns the mobile drawer open state. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="app">
      <SideNav open={sidebarOpen} onNavigate={() => setSidebarOpen(false)} />
      <div className="main">
        <TopBar onMenu={() => setSidebarOpen((v) => !v)} />
        {/* Keyed by route so the fade-in animation replays on each navigation. */}
        <main className="view" key={pathname}>
          {children}
        </main>
      </div>
    </div>
  );
}
