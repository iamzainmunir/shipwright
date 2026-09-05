"use client";

import { Icon } from "@foundry/ui";
import type { IconName } from "@foundry/ui";
import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

export type ToastKind = "ok" | "info" | "warn" | "err";

interface ToastItem {
  id: number;
  title: string;
  sub?: string;
  kind: ToastKind;
  leaving: boolean;
}

/** kind → tint class + icon (mirrors the mockup's toast styling). */
const STYLE: Record<ToastKind, { tint: string; icon: IconName }> = {
  ok: { tint: "tint-green", icon: "check" },
  info: { tint: "tint-brand", icon: "bolt" },
  warn: { tint: "tint-amber", icon: "shield" },
  err: { tint: "tint-red", icon: "bug" },
};

interface ToastContextValue {
  toast: (title: string, sub?: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((cur) => cur.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    window.setTimeout(() => setItems((cur) => cur.filter((t) => t.id !== id)), 260);
  }, []);

  const toast = useCallback(
    (title: string, sub?: string, kind: ToastKind = "ok") => {
      const id = nextId.current++;
      setItems((cur) => [...cur, { id, title, sub, kind, leaving: false }]);
      window.setTimeout(() => dismiss(id), 3200);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div id="toast-root">
        {items.map((t) => (
          <div key={t.id} className={t.leaving ? "toast leaving" : "toast"} role="status">
            <span className={`t-ic ${STYLE[t.kind].tint}`}>
              <Icon name={STYLE[t.kind].icon} size={16} />
            </span>
            <div>
              <div className="t-title">{t.title}</div>
              {t.sub && <div className="t-sub">{t.sub}</div>}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
