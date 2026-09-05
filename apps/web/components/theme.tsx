"use client";

import { Icon } from "@foundry/ui";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

/** The three theme choices. Light is the product default; dark and system are opt-in. */
export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "foundry.theme";

interface ThemeContextValue {
  theme: Theme;
  resolved: "light" | "dark";
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function resolve(theme: Theme): "light" | "dark" {
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return theme;
}

/** Write the choice to <html> so the token layer swaps, matching the pre-hydration init script. */
function applyToDocument(theme: Theme): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = resolve(theme);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("light");
  const [resolved, setResolved] = useState<"light" | "dark">("light");

  // Sync React state to the theme the init script already applied to <html>.
  useEffect(() => {
    const stored = (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "light";
    setThemeState(stored);
    setResolved(resolve(stored));
  }, []);

  // Follow the OS while on "system".
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      setResolved(mq.matches ? "dark" : "light");
      applyToDocument("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next);
    applyToDocument(next);
    setThemeState(next);
    setResolved(resolve(next));
  }, []);

  const toggle = useCallback(() => {
    setTheme(resolve(theme) === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}

/** Topbar sun/moon toggle. Renders a stable placeholder until mounted to avoid hydration flicker. */
export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return (
    <button
      type="button"
      className="icon-btn"
      onClick={toggle}
      data-tip="Theme"
      aria-label="Toggle theme"
    >
      {mounted && <Icon name={resolved === "dark" ? "sun" : "moon"} size={17} />}
    </button>
  );
}
