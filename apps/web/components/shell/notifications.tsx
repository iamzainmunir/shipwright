"use client";

import { Icon } from "@foundry/ui";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/toast";
import { useShellData } from "./shell-data";

/** Bell button + anchored popover. Surfaces real open blockers (the things needing a human). */
export function Notifications() {
  const router = useRouter();
  const { toast } = useToast();
  const { blockers, missions } = useShellData();
  const [open, setOpen] = useState(false);
  const [read, setRead] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 64, right: 12 });

  const openBlockers = blockers.filter((b) => !b.resolvedAt); // only unresolved items are "notifications"
  const hasUnread = openBlockers.length > 0 && !read;

  useEffect(() => {
    if (!open) return;
    const bell = btnRef.current;
    const rect = bell?.getBoundingClientRect();
    if (rect) setPos({ top: rect.bottom + 8, right: Math.max(12, window.innerWidth - rect.right) });
    popRef.current?.focus(); // move focus into the popover
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!popRef.current?.contains(t) && !btnRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      bell?.focus(); // restore focus to the bell on close
    };
  }, [open]);

  const missionKeyOf = (missionId: string) => missions.find((m) => m.id === missionId)?.key;

  function goToBlocker(missionId: string) {
    setOpen(false);
    const key = missionKeyOf(missionId);
    router.push(key ? `/dashboard/missions/${key}` : "/dashboard/missions");
  }

  return (
    <>
      <button
        ref={btnRef} type="button" className="icon-btn" data-tip="Notifications"
        aria-label={hasUnread ? `Notifications, ${openBlockers.length} need attention` : "Notifications"}
        aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="bell" size={17} />
        {hasUnread && <span className="dot-badge" />}
      </button>
      {open && (
        <div ref={popRef} tabIndex={-1} className="popover" style={{ top: pos.top, right: pos.right, outline: "none" }} role="dialog" aria-label="Notifications">
          <div className="pop-head">
            <span className="row gap-8" style={{ fontWeight: 700 }}>
              <Icon name="bell" size={15} /> Notifications
            </span>
            <button
              type="button" className="c-brand clickable" style={{ fontSize: 12.5, fontWeight: 600, background: "none", border: "none" }}
              onClick={() => { setRead(true); setOpen(false); toast("All caught up", "You're up to date", "ok"); }}
            >
              Mark all read
            </button>
          </div>
          <div className="pop-list">
            {openBlockers.length === 0 && (
              <div className="empty" style={{ padding: "28px 12px" }}>
                <div className="em-ic"><Icon name="check" size={28} /></div>
                No blockers — the team is running clean.
              </div>
            )}
            {openBlockers.map((b) => (
              <button
                key={b.id} type="button" className="notif-item" style={{ width: "100%", textAlign: "left", background: "none", border: "none" }}
                onClick={() => goToBlocker(b.missionId)}
              >
                <span className={`t-ic ${b.severity === "block" ? "tint-red" : "tint-amber"}`} style={{ width: 30, height: 30, borderRadius: 9, display: "grid", placeContent: "center", flex: "0 0 30px" }}>
                  <Icon name={b.kind === "approval" ? "shield" : "bolt"} size={15} />
                </span>
                <div className="li-main">
                  <div className="li-title" style={{ fontSize: 13 }}>{b.detail}</div>
                  <div className="li-sub">{b.kind} · {missionKeyOf(b.missionId) ?? "mission"}</div>
                </div>
              </button>
            ))}
          </div>
          <div className="pop-foot">
            <span className="faint text-xs">{openBlockers.length} open blocker{openBlockers.length === 1 ? "" : "s"}</span>
            <button
              type="button" className="c-brand clickable" style={{ fontSize: 12.5, fontWeight: 600, background: "none", border: "none" }}
              onClick={() => { setOpen(false); router.push("/dashboard"); }}
            >
              Open Command Center →
            </button>
          </div>
        </div>
      )}
    </>
  );
}
