"use client";

import type { ReactNode } from "react";
import { ConfirmProvider } from "@/components/confirm";
import { ToastProvider } from "@/components/toast";
import { CommandPaletteProvider } from "./command-palette";
import { NewMissionProvider } from "./new-mission";
import { ShellDataProvider } from "./shell-data";

/** Dashboard shell contexts, nested so each can depend on the ones above it.
 *  ThemeProvider lives at the root layout (shared with the landing page). */
export function ShellProviders({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <ShellDataProvider>
          <NewMissionProvider>
            <CommandPaletteProvider>{children}</CommandPaletteProvider>
          </NewMissionProvider>
        </ShellDataProvider>
      </ConfirmProvider>
    </ToastProvider>
  );
}
