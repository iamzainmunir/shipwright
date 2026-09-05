"use client";

import { Icon } from "@foundry/ui";
import type { IconName } from "@foundry/ui";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTheme } from "@/components/theme";
import { NAV } from "./nav-items";
import { useNewMission } from "./new-mission";
import { useShellData } from "./shell-data";

interface Command {
  icon: IconName;
  label: string;
  hint: string;
  run: () => void;
}

interface CommandPaletteContextValue {
  open: () => void;
}

const CommandPaletteContext = createContext<CommandPaletteContextValue | null>(null);

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const newMission = useNewMission();

  // Global shortcuts: ⌘K/Ctrl+K opens the palette; "n" opens New Mission when not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsOpen((v) => !v);
        return;
      }
      const target = e.target as HTMLElement | null;
      const typing = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (e.key === "n" && !typing && !e.metaKey && !e.ctrlKey && !isOpen) {
        e.preventDefault();
        newMission.open();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, newMission]);

  return (
    <CommandPaletteContext.Provider value={{ open }}>
      {children}
      {isOpen && <Palette onClose={close} onNewMission={newMission.open} />}
    </CommandPaletteContext.Provider>
  );
}

export function useCommandPalette(): CommandPaletteContextValue {
  const ctx = useContext(CommandPaletteContext);
  if (!ctx) throw new Error("useCommandPalette must be used within <CommandPaletteProvider>");
  return ctx;
}

function Palette({ onClose, onNewMission }: { onClose: () => void; onNewMission: () => void }) {
  const router = useRouter();
  const { toggle } = useTheme();
  const { missions, agents, skills, models } = useShellData();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const go = useCallback((href: string) => { onClose(); router.push(href); }, [onClose, router]);

  const commands = useMemo<Command[]>(() => {
    const q = query.trim().toLowerCase();
    const navCmds: Command[] = NAV.flatMap((g) => g.items).map((n) => ({
      icon: n.icon, label: n.label, hint: "Go", run: () => go(n.href),
    }));
    const actions: Command[] = [
      { icon: "plus", label: "New mission…", hint: "Create", run: () => { onClose(); onNewMission(); } },
      { icon: "sun", label: "Toggle theme", hint: "Theme", run: () => { onClose(); toggle(); } },
    ];
    const statics = [...navCmds, ...actions].filter((c) => !q || c.label.toLowerCase().includes(q));

    const missionHits: Command[] = missions
      .filter((m) => `${m.key} ${m.title}`.toLowerCase().includes(q))
      .slice(0, 4)
      .map((m) => ({ icon: "kanban", label: `${m.key} · ${m.title}`, hint: "Mission", run: () => go(`/dashboard/missions/${m.key}`) }));

    const agentHits: Command[] = q
      ? agents.filter((a) => `${a.name} ${a.roleKey}`.toLowerCase().includes(q)).slice(0, 3)
          .map((a) => ({ icon: "users", label: `${a.name} · ${a.roleKey}`, hint: "Team", run: () => go("/dashboard/team") }))
      : [];
    const skillHits: Command[] = q
      ? skills.filter((s) => `${s.name} ${s.category}`.toLowerCase().includes(q)).slice(0, 3)
          .map((s) => ({ icon: "sparkles", label: s.name, hint: "Skill", run: () => go("/dashboard/skills") }))
      : [];
    const modelHits: Command[] = q
      ? models.filter((m) => `${m.provider}`.toLowerCase().includes(q)).slice(0, 2)
          .map((m) => ({ icon: "cpu", label: m.provider, hint: "Model", run: () => go("/dashboard/models") }))
      : [];

    return [...statics, ...missionHits, ...agentHits, ...skillHits, ...modelHits];
  }, [query, missions, agents, skills, models, go, onClose, onNewMission, toggle]);

  useEffect(() => setActive(0), [query]);

  // Restore focus to whatever opened the palette when it closes.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, commands.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); commands[active]?.run(); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  useEffect(() => {
    listRef.current?.querySelector(".cmdk-item.active")?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <div
      className="overlay"
      style={{ placeItems: "start center", paddingTop: "12vh" }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="cmdk-input">
          <Icon name="search" size={18} />
          <input
            autoFocus value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
            placeholder="Search actions, missions, agents…" autoComplete="off" aria-label="Search"
          />
          <kbd>Esc</kbd>
        </div>
        <div className="cmdk-list" ref={listRef} role="listbox" aria-label="Results">
          {commands.length === 0 && <div className="cmdk-item">No matches</div>}
          {commands.map((c, i) => (
            <div
              key={`${c.label}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "cmdk-item active" : "cmdk-item"}
              onMouseEnter={() => setActive(i)}
              onClick={c.run}
            >
              <span className="k-ico"><Icon name={c.icon} size={16} /></span>
              <span>{c.label}</span>
              <span className="k-hint">{c.hint}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
