"use client";

import { Button, Icon, type IconName } from "@foundry/ui";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * App-styled replacement for the native `window.confirm`.
 *
 * `const confirm = useConfirm()` returns a promise-based function: `await confirm({...})`
 * resolves true on confirm, false on cancel/escape/backdrop. The dialog reuses the shared
 * `.overlay`/`.modal-card` design so it matches every other modal (create-skill, connect, …).
 */
export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "default";
  icon?: IconName;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error("useConfirm must be used within <ConfirmProvider>");
  return fn;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>(
    (options) =>
      new Promise<boolean>((resolve) => {
        resolver.current = resolve;
        setOpts(options);
      }),
    [],
  );

  const settle = useCallback((value: boolean) => {
    setOpts(null);
    resolver.current?.(value);
    resolver.current = null;
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && <ConfirmDialog opts={opts} onResolve={settle} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({ opts, onResolve }: { opts: ConfirmOptions; onResolve: (v: boolean) => void }) {
  const danger = opts.tone === "danger";
  const icon: IconName = opts.icon ?? (danger ? "shield" : "sparkles");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onResolve(false);
      // Enter confirms only non-destructive dialogs; a danger action requires an explicit click
      // so a stray keypress can never delete something.
      if (e.key === "Enter" && !danger) onResolve(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onResolve, danger]);

  return (
    <div
      className="overlay"
      role="alertdialog"
      aria-modal
      aria-label={opts.title}
      onMouseDown={(e) => e.target === e.currentTarget && onResolve(false)}
    >
      <div className="modal-card" style={{ maxWidth: 440 }}>
        <div className="modal-head">
          <h3 className="row" style={{ gap: 10 }}>
            <span
              className="stat-ic"
              style={{
                position: "static", width: 34, height: 34, flex: "0 0 34px",
                display: "grid", placeItems: "center", borderRadius: 10,
                color: danger ? "var(--red)" : "var(--brand-2)",
                background: danger
                  ? "color-mix(in srgb, var(--red) 14%, transparent)"
                  : "color-mix(in srgb, var(--brand) 14%, transparent)",
              }}
            >
              <Icon name={icon} size={16} />
            </span>
            {opts.title}
          </h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={() => onResolve(false)}>
            <Icon name="close" size={16} />
          </button>
        </div>
        {opts.message && (
          <div className="modal-body">
            <p className="muted" style={{ margin: 0, fontSize: 13.5, lineHeight: 1.6 }}>{opts.message}</p>
          </div>
        )}
        <div className="modal-foot">
          <Button variant="ghost" onClick={() => onResolve(false)}>{opts.cancelLabel ?? "Cancel"}</Button>
          <Button variant={danger ? "danger" : "primary"} autoFocus={!danger} onClick={() => onResolve(true)}>
            {danger && <Icon name="close" size={15} />} {opts.confirmLabel ?? "Confirm"}
          </Button>
        </div>
      </div>
    </div>
  );
}
