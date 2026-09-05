"use client";

/**
 * Tickets board (v2 Phase 5 — plan 05 §3).
 *
 * Shipwright's built-in Jira-like board. Columns follow the lifecycle order (To Do → In Progress →
 * In Review → QA → Done); the `reopened` state renders in the To Do column with a red badge. Cards
 * carry the ticket key, kind (epic/story/bug), agent chip, and reopen count. The drawer shows the
 * structured description, the activity feed from ticket_events, linked tickets, and an evidence
 * gallery streamed from the Artifact endpoint. Filter by Epic (mission) narrows the board.
 *
 * The board derives entirely from the ticket rows the engine's consumer writes — no state is
 * duplicated here. When the Settings → Features toggle is off, the page shows an enable CTA.
 */

import { Badge, type BadgeTone, Button, Icon, type IconName, cx } from "@foundry/ui";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { isApiError } from "@/lib/api";
import { useToast } from "@/components/toast";
import {
  type Features,
  type Ticket,
  type TicketDetail,
  type TicketEvent,
  type TicketKind,
  type TicketStatus,
  artifactContentUrl,
  getFeatures,
  getTicket,
  listTickets,
  updateFeatures,
} from "@/lib/foundry";

/* ------------------------------------------------------------------ */
/* Reference tables                                                    */
/* ------------------------------------------------------------------ */

interface ColumnMeta {
  key: TicketStatus;
  label: string;
  color: string;
}
/**
 * Board columns, left→right in lifecycle order, then a trailing Blocked column for paused work
 * (mirrors the Missions board's Stopped column). `reopened` is NOT a column — it folds into To Do
 * with a red badge; `blocked` (a force-stopped run or a ticket awaiting a human) IS its own column.
 */
const COLUMNS: ColumnMeta[] = [
  { key: "todo", label: "To Do", color: "var(--muted)" },
  { key: "in_progress", label: "In Progress", color: "var(--brand)" },
  { key: "in_review", label: "In Review", color: "var(--blue)" },
  { key: "qa", label: "QA", color: "var(--amber)" },
  { key: "done", label: "Done", color: "var(--green)" },
  { key: "blocked", label: "Blocked", color: "var(--red)" },
];

const KIND_META: Record<TicketKind, { label: string; tone: BadgeTone; icon: IconName }> = {
  epic: { label: "Epic", tone: "brand", icon: "rocket" },
  story: { label: "Story", tone: "blue", icon: "doc" },
  bug: { label: "Bug", tone: "red", icon: "bug" },
};

const STATUS_TONE: Record<TicketStatus, BadgeTone> = {
  todo: "neutral",
  in_progress: "brand",
  qa: "amber",
  in_review: "blue",
  done: "green",
  reopened: "red",
  blocked: "red",
};
const STATUS_LABEL: Record<TicketStatus, string> = {
  todo: "To Do",
  in_progress: "In Progress",
  qa: "QA",
  in_review: "In Review",
  done: "Done",
  reopened: "Reopened",
  blocked: "Blocked",
};

const EVENT_ICON: Record<string, IconName> = {
  created: "plus",
  transitioned: "arrow",
  commented: "pen",
  evidence_attached: "flask",
  reopened: "arrow",
  linked: "branch",
  reworked: "bolt",
  shipped: "rocket",
  sync_error: "shield",
};

/** Which column a ticket sits in — a reopened ticket shows in To Do (plan 05 §3). */
const columnOf = (t: Ticket): TicketStatus => (t.status === "reopened" ? "todo" : t.status);

function relativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** Read a snake_case key from an opaque JSON blob (event body / description are passed through). */
function field<T = unknown>(blob: Record<string, unknown> | undefined, key: string): T | undefined {
  return blob ? (blob[key] as T | undefined) : undefined;
}
function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/* ------------------------------------------------------------------ */
/* Cards                                                              */
/* ------------------------------------------------------------------ */

/** Brand colour per engineering role — used for the assignee avatar + role chip so a glance tells you
 *  who owns a ticket (matches the org-chart palette). Falls back to muted for unknown/custom roles. */
const ROLE_COLOR: Record<string, string> = {
  backend: "var(--green)", frontend: "var(--blue)", fullstack: "var(--cyan)",
  qa: "var(--amber)", devops: "var(--cyan)", security: "var(--red)",
  pm: "var(--pink)", ba: "var(--pink)", designer: "var(--pink)",
  cto: "var(--brand)", ceo: "var(--brand)",
};
const roleColor = (role?: string | null): string => ROLE_COLOR[(role || "").toLowerCase()] || "var(--muted)";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

/** Tiny inline formatter: renders **bold**, *italic*, `code` and `path/like.ext` tokens inside a
 *  plain string — so a ticket's Goal reads with emphasis instead of as a flat blob. */
function richText(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+?)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={i++}>{m[1]}</strong>);
    else if (m[2]) out.push(<em key={i++}>{m[2]}</em>);
    else if (m[3]) out.push(<code key={i++} className="tk-code">{m[3]}</code>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Friendly role label — acronyms upper-cased, others Title-cased ("backend" → "Backend"). */
const ROLE_LABELS: Record<string, string> = { qa: "QA", cto: "CTO", pm: "PM", ba: "BA", ceo: "CEO", devops: "DevOps" };
const roleLabel = (r?: string | null): string =>
  !r ? "" : (ROLE_LABELS[r] ?? r.charAt(0).toUpperCase() + r.slice(1));

/** The person a ticket is assigned to — a coloured avatar + name + role chip. */
function AssigneeChip({ name, role }: { name?: string | null; role?: string | null }) {
  if (!name) return <span className="tk-unassigned"><Icon name="users" size={12} /> Unassigned</span>;
  const c = roleColor(role);
  return (
    <span className="tk-assignee" style={{ "--rc": c } as CSSProperties}>
      <span className="tk-ava" style={{ background: c }}>{initials(name)}</span>
      <span className="tk-ava-name">{name}</span>
      {role && <span className="tk-ava-role">{roleLabel(role)}</span>}
    </span>
  );
}

function TicketCard({ ticket, onOpen, flash = false }: { ticket: Ticket; onOpen: () => void; flash?: boolean }) {
  const kind = KIND_META[ticket.kind];
  const accent =
    ticket.kind === "bug" ? "var(--red)" : ticket.kind === "epic" ? "var(--brand)" : "var(--blue)";
  return (
    <div
      className={cx("kanban-card", flash && "kanban-card-flash")}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
      style={{ borderTop: `2px solid ${accent}` }}
    >
      <div className="row between gap-8">
        <Badge tone={kind.tone} style={{ minWidth: 0 }}>
          <Icon name={kind.icon} size={11} /> {kind.label}
        </Badge>
        <span className="mono text-xs faint truncate">{ticket.key}</span>
      </div>

      <div className="fw-6 mt-8" style={{ fontSize: 13.5, lineHeight: 1.35 }}>
        {ticket.title}
      </div>

      {ticket.status === "reopened" && (
        <div className="mt-8">
          <Badge tone="red" dot>
            reopened ×{ticket.reopenCount}
          </Badge>
        </div>
      )}

      <div className="row between mt-12" style={{ minWidth: 0, gap: 8 }}>
        <AssigneeChip name={ticket.agentName} role={ticket.agentRole} />
        <span className="text-xs faint" style={{ whiteSpace: "nowrap" }}>
          {relativeTime(ticket.updatedAt ?? ticket.createdAt)}
        </span>
      </div>

      {ticket.jiraKey && (
        <div className="mt-8">
          <span className="chip" style={{ fontSize: 10.5 }}>
            <Icon name="external" size={11} /> {ticket.jiraKey}
          </span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Drawer                                                             */
/* ------------------------------------------------------------------ */

function evidenceIds(events: TicketEvent[]): string[] {
  const ids = new Set<string>();
  for (const e of events) {
    for (const id of strList(field(e.body, "artifact_ids"))) ids.add(id);
  }
  return [...ids];
}

function DescBlock({ ticket }: { ticket: Ticket }) {
  const d = ticket.description || {};
  const goal = field<string>(d, "goal");
  const acceptance = strList(field(d, "acceptance"));
  const dod = strList(field(d, "dod"));
  const owned = strList(field(d, "owned_paths"));
  const skills = strList(field(d, "skills"));
  const impl = field<string>(d, "impl_notes");
  const failure = field<string>(d, "failure");
  return (
    <div className="tk-sections">
      {goal && (
        <section className="tk-section">
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--brand)" }} />Goal</h4>
          <div className="tk-goal">{richText(goal)}</div>
        </section>
      )}
      {failure && (
        <section>
          <h4 className="drawer-h" style={{ color: "var(--red)" }}>
            <span className="dh-mark" style={{ background: "var(--red)" }} />Failure
          </h4>
          <div className="callout callout-red">
            <Icon name="bug" size={14} />
            <span>{failure}</span>
          </div>
        </section>
      )}
      {acceptance.length > 0 && (
        <section>
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--cyan)" }} />Acceptance criteria</h4>
          <ul className="check-list">
            {acceptance.map((a, i) => (
              <li key={i}><span className="ck ck-cyan"><Icon name="check" size={11} /></span><span>{a}</span></li>
            ))}
          </ul>
        </section>
      )}
      {dod.length > 0 && (
        <section>
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--green)" }} />Definition of done</h4>
          <ul className="check-list">
            {dod.map((a, i) => (
              <li key={i}><span className="ck ck-green"><Icon name="check" size={11} /></span><span>{a}</span></li>
            ))}
          </ul>
        </section>
      )}
      {skills.length > 0 && (
        <section>
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--brand-2)" }} />Skills needed</h4>
          <div className="row wrap gap-6">
            {skills.map((skl) => (
              <span key={skl} className="chip"><Icon name="sparkles" size={11} /> {skl}</span>
            ))}
          </div>
        </section>
      )}
      {owned.length > 0 && (
        <section>
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--amber)" }} />Owned paths</h4>
          <div className="row wrap gap-6">
            {owned.map((p) => (
              <span key={p} className="chip mono owned-chip">
                <Icon name="doc" size={11} /> {p}
              </span>
            ))}
          </div>
        </section>
      )}
      {impl && (
        <section>
          <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--muted)" }} />Implementation notes</h4>
          <p className="drawer-p">{impl}</p>
        </section>
      )}
    </div>
  );
}

/** Dot color per activity kind — so a glance down the feed reads the story: created, moved, shipped… */
const EVENT_DOT: Record<string, string> = {
  created: "var(--brand)", transitioned: "var(--blue)", reopened: "var(--red)",
  shipped: "var(--green)", commented: "var(--cyan)", evidence_attached: "var(--amber)",
  linked: "var(--muted)", reworked: "var(--amber)", sync_error: "var(--red)",
};

/** Compact actor for a feed row: a role-colored avatar + name + role — the real person (e.g. Humna,
 *  Ansa, Kamran), not a generic label. */
function FeedActor({ name, role }: { name?: string | null; role?: string | null }) {
  if (!name) return null;
  const c = roleColor(role);
  return (
    <span className="feed-actor">
      <span className="feed-ava" style={{ background: c }}>{initials(name)}</span>
      <span className="feed-actor-name">{name}</span>
      {role && <span className="feed-actor-role" style={{ color: c, "--rc": c } as CSSProperties}>{role}</span>}
    </span>
  );
}

function StatusPill({ status }: { status: TicketStatus }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_LABEL[status] ?? status}</Badge>;
}

function ActivityFeed({ events }: { events: TicketEvent[] }) {
  if (events.length === 0) return <p className="drawer-p faint">No activity yet.</p>;
  return (
    <ol className="feed">
      {events.map((e) => {
        const reason = field<string>(e.body, "reason");
        const blockedBy = field<string>(e.body, "blocked_by");
        const from = e.fromStatus as TicketStatus | undefined;
        const to = e.toStatus as TicketStatus | undefined;
        const color = EVENT_DOT[e.kind] ?? "var(--muted)";
        return (
          <li key={e.id} className="feed-row">
            <span
              className="feed-dot"
              style={{
                color,
                border: `1px solid color-mix(in srgb, ${color} 45%, var(--line))`,
                background: `color-mix(in srgb, ${color} 13%, var(--panel-2))`,
              }}
            >
              <Icon name={EVENT_ICON[e.kind] ?? "clock"} size={12} />
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="row between gap-8">
                <span className="fw-6 text-sm" style={{ textTransform: "capitalize" }}>
                  {e.kind.replace(/_/g, " ")}
                </span>
                <span className="text-xs faint" style={{ whiteSpace: "nowrap" }}>{relativeTime(e.createdAt)}</span>
              </div>
              <div className="row wrap gap-8 mt-6" style={{ alignItems: "center" }}>
                <FeedActor name={e.actorName} role={e.actorRole} />
                {from && to && (
                  <span className="feed-transition">
                    <StatusPill status={from} />
                    <Icon name="arrow" size={13} />
                    <StatusPill status={to} />
                  </span>
                )}
              </div>
              {reason && <p className="drawer-p mt-6">{reason}</p>}
              {blockedBy && (
                <div className="mt-6">
                  <Badge tone="red" dot>
                    Blocked by {blockedBy}
                  </Badge>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Renders one evidence artifact large, in-app. Screenshots (PNG) show as an image; if that fails
 *  the artifact is an HTML page (e.g. the built app), so fall back to a sandboxed iframe. */
function EvidenceView({ url }: { url: string }) {
  const [asFrame, setAsFrame] = useState(false);
  if (asFrame) return <iframe className="lb-frame" src={url} sandbox="" title="Evidence" />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img className="lb-img" src={url} alt="QA evidence" onError={() => setAsFrame(true)} />;
}

function TicketDrawer({
  detail,
  keyOf,
  onClose,
}: {
  detail: TicketDetail;
  keyOf: (id: string) => string;
  onClose: () => void;
}) {
  const { ticket, events, links } = detail;
  const kind = KIND_META[ticket.kind];
  const shots = useMemo(() => evidenceIds(events), [events]);
  const accent =
    ticket.kind === "bug" ? "var(--red)" : ticket.kind === "epic" ? "var(--brand)" : "var(--blue)";
  const [lightbox, setLightbox] = useState<number | null>(null); // index into shots

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (lightbox !== null) setLightbox(null);
        else onClose();
      } else if (lightbox !== null && shots.length > 1) {
        if (e.key === "ArrowRight") setLightbox((i) => ((i ?? 0) + 1) % shots.length);
        if (e.key === "ArrowLeft") setLightbox((i) => ((i ?? 0) - 1 + shots.length) % shots.length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, lightbox, shots.length]);

  return (
    <>
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal-card tk-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`${ticket.key} ${ticket.title}`}
        // A definite (viewport-based) width so the overlay's auto grid track doesn't collapse it to
        // content width — percentages resolve to `auto` inside an auto track.
        style={{ width: "min(760px, calc(100vw - 40px))", maxWidth: "none", "--accent": accent } as CSSProperties}
      >
        <div className="tk-accent" />
        <div className="modal-head">
          <div className="row gap-10" style={{ minWidth: 0 }}>
            <Badge tone={kind.tone}>
              <Icon name={kind.icon} size={12} /> {kind.label}
            </Badge>
            <span className="mono text-sm faint">{ticket.key}</span>
            <Badge tone={STATUS_TONE[ticket.status]} dot>
              {STATUS_LABEL[ticket.status]}
            </Badge>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="modal-body" style={{ maxHeight: "70vh", overflow: "auto" }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>{ticket.title}</h2>
          <div className="row wrap gap-8" style={{ marginBottom: 18 }}>
            <AssigneeChip name={ticket.agentName} role={ticket.agentRole} />
            {ticket.reopenCount > 0 && (
              <Badge tone="red" dot>
                reopened ×{ticket.reopenCount}
              </Badge>
            )}
            {ticket.priority && (
              <span className="chip" style={{ fontSize: 11, fontWeight: 700 }}>{ticket.priority}</span>
            )}
            {ticket.jiraKey && (
              <span className="chip" style={{ fontSize: 11 }}>
                <Icon name="external" size={11} /> Jira {ticket.jiraKey}
              </span>
            )}
            {/* raw role:/agent: labels are shown as the coloured assignee chip above, not as text */}
            {ticket.labels
              .filter((l) => !l.startsWith("role:") && !l.startsWith("agent:"))
              .map((l) => (
                <span key={l} className="chip" style={{ fontSize: 10.5 }}>
                  {l}
                </span>
              ))}
          </div>

          <DescBlock ticket={ticket} />

          {links.length > 0 && (
            <section style={{ marginTop: 18 }}>
              <h4 className="drawer-h">Linked</h4>
              <div className="row wrap gap-6">
                {links.map((ln) => {
                  const other = ln.fromTicket === ticket.id ? ln.toTicket : ln.fromTicket;
                  const dir = ln.fromTicket === ticket.id ? ln.linkType : `${ln.linkType} ←`;
                  return (
                    <span key={ln.id} className="chip" style={{ fontSize: 11 }}>
                      <Icon name="branch" size={11} /> {dir} {keyOf(other)}
                    </span>
                  );
                })}
              </div>
            </section>
          )}

          {shots.length > 0 && (
            <section style={{ marginTop: 18 }}>
              <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--pink)" }} />Evidence</h4>
              <div className="evidence-grid">
                {shots.map((id, i) => (
                  <button
                    key={id}
                    type="button"
                    className="evidence-thumb"
                    onClick={() => setLightbox(i)}
                    aria-label={`Open evidence ${i + 1}`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={artifactContentUrl(id)} alt={`QA evidence ${i + 1}`} loading="lazy" />
                    <span className="evidence-zoom"><Icon name="search" size={14} /></span>
                  </button>
                ))}
              </div>
            </section>
          )}

          <section style={{ marginTop: 18 }}>
            <h4 className="drawer-h"><span className="dh-mark" style={{ background: "var(--muted)" }} />Activity</h4>
            <ActivityFeed events={events} />
          </section>
        </div>
      </div>
      </div>

      {/* In-app evidence lightbox — portalled to <body> so no transformed/backdrop-filtered ancestor
          (the drawer, the app shell) captures its position:fixed. It fills the whole viewport, centered. */}
      {lightbox !== null && shots[lightbox] && typeof document !== "undefined" && createPortal(
        <div
          className="lightbox"
          onMouseDown={(e) => e.target === e.currentTarget && setLightbox(null)}
        >
          <button type="button" className="lb-close icon-btn" onClick={() => setLightbox(null)} aria-label="Close">
            <Icon name="close" size={18} />
          </button>
          {shots.length > 1 && (
            <button
              type="button"
              className="lb-nav lb-prev icon-btn"
              onClick={() => setLightbox((idx) => ((idx ?? 0) - 1 + shots.length) % shots.length)}
              aria-label="Previous"
            >
              <span style={{ display: "inline-flex", transform: "rotate(180deg)" }}><Icon name="arrow" size={18} /></span>
            </button>
          )}
          <EvidenceView key={shots[lightbox]} url={artifactContentUrl(shots[lightbox])} />

          {shots.length > 1 && (
            <button
              type="button"
              className="lb-nav lb-next icon-btn"
              onClick={() => setLightbox((idx) => ((idx ?? 0) + 1) % shots.length)}
              aria-label="Next"
            >
              <Icon name="arrow" size={18} />
            </button>
          )}
          {shots.length > 1 && <div className="lb-count">{lightbox + 1} / {shots.length}</div>}
        </div>,
        document.body,
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                              */
/* ------------------------------------------------------------------ */

interface Project {
  epic: Ticket;
  missionId: string;
  items: Ticket[];   // stories + bugs (never the epic itself)
  stories: Ticket[];
  bugs: Ticket[];
  done: number;
  total: number;
  bugsOpen: number;
}

/** A card in the project picker — one per Epic (mission). Click to open its board. */
function ProjectCard({ p, onOpen }: { p: Project; onOpen: () => void }) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  const agents = Array.from(
    new Set(p.stories.map((s) => s.agentName).filter((n): n is string => !!n)),
  ).slice(0, 4);
  return (
    <button type="button" className="proj-card" onClick={onOpen}>
      <div className="row between gap-8">
        <span className="mono text-xs faint">{p.epic.key}</span>
        <Badge tone={STATUS_TONE[p.epic.status]} dot>{STATUS_LABEL[p.epic.status]}</Badge>
      </div>
      <div className="proj-title">{p.epic.title}</div>
      <div className="proj-meta">
        <span>{p.total} stor{p.total === 1 ? "y" : "ies"}</span>
        <span className="proj-dot">·</span>
        <span className={p.bugsOpen ? "c-red" : undefined}>
          {p.bugs.length} bug{p.bugs.length === 1 ? "" : "s"}
          {p.bugsOpen ? ` (${p.bugsOpen} open)` : ""}
        </span>
      </div>
      <div className="row gap-8 mt-12" style={{ alignItems: "center" }}>
        <div className="progress thin" style={{ flex: 1 }}>
          <div className="bar" style={{ width: `${pct}%` }} />
        </div>
        <span className="text-xs faint" style={{ minWidth: 30, textAlign: "right" }}>{pct}%</span>
      </div>
      <div className="proj-foot">
        {agents.length > 0 ? (
          <span className="chip" style={{ fontSize: 10.5, padding: "2px 8px" }}>
            <Icon name="users" size={11} /> {agents.join(", ")}
          </span>
        ) : (
          <span className="text-xs faint">no work items yet</span>
        )}
        <span className="proj-open">Open board →</span>
      </div>
    </button>
  );
}

export default function TicketsPage() {
  const { toast } = useToast();
  const [features, setFeatures] = useState<Features | null>(null);
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedMission, setSelectedMission] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [enabling, setEnabling] = useState(false);
  // Flash a card when its status changes between polls, so a live transition is visible, not silent.
  const prevStatusRef = useRef<Map<string, string>>(new Map());
  const [flashIds, setFlashIds] = useState<ReadonlySet<string>>(() => new Set());

  const reload = useCallback(async () => {
    setError(null);
    try {
      const f = await getFeatures();
      setFeatures(f);
      if (f.tickets) setTickets(await listTickets());
      else setTickets([]);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to load tickets");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Load the drawer detail whenever a card is opened.
  useEffect(() => {
    if (!openId) {
      setDetail(null);
      return;
    }
    let alive = true;
    void getTicket(openId)
      .then((d) => alive && setDetail(d))
      .catch(() => alive && toast("Could not open ticket", undefined, "err"));
    return () => {
      alive = false;
    };
  }, [openId, toast]);

  // Live board: poll the tickets (and the open drawer) every 2s so column transitions — To Do →
  // In Progress → QA → In Review → Done, plus new bugs and reopens — appear as they happen, without
  // a manual refresh. Silent (no spinner/error reset); a transient blip just keeps the last state.
  useEffect(() => {
    if (!features?.tickets) return;
    let alive = true;
    const tick = () => {
      void listTickets().then((rows) => alive && setTickets(rows)).catch(() => {});
      if (openId) void getTicket(openId).then((d) => alive && setDetail(d)).catch(() => {});
    };
    const id = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [features?.tickets, openId]);

  // Detect which tickets just changed status (a live transition) and flash those cards briefly.
  useEffect(() => {
    if (!tickets) return;
    const prev = prevStatusRef.current;
    const moved = new Set<string>();
    for (const t of tickets) {
      const before = prev.get(t.id);
      if (before !== undefined && before !== t.status) moved.add(t.id);
      prev.set(t.id, t.status);
    }
    if (moved.size === 0) return;
    setFlashIds(moved);
    const clear = window.setTimeout(() => setFlashIds(new Set()), 1400);
    return () => window.clearTimeout(clear);
  }, [tickets]);

  const enableBoard = async () => {
    setEnabling(true);
    try {
      const f = await updateFeatures({ tickets: true });
      setFeatures(f);
      setTickets(await listTickets());
      toast("Ticket board enabled", "Backfilled Epics for existing missions", "ok");
    } catch (e) {
      toast("Could not enable", isApiError(e) ? e.message : "Try again", "err");
    } finally {
      setEnabling(false);
    }
  };

  const keyOf = useCallback(
    (id: string) => tickets?.find((t) => t.id === id)?.key ?? id.slice(0, 6),
    [tickets],
  );

  // One project per Epic (mission), each carrying its own Stories + Bugs. The board scopes to one
  // project at a time — the Epic represents the whole project, so it is never shown as a ticket card.
  const projects = useMemo<Project[]>(() => {
    const rows = tickets ?? [];
    return rows
      .filter((t) => t.kind === "epic")
      .map((epic) => {
        const items = rows.filter((t) => t.missionId === epic.missionId && t.kind !== "epic");
        const stories = items.filter((t) => t.kind === "story");
        const bugs = items.filter((t) => t.kind === "bug");
        return {
          epic, missionId: epic.missionId, items, stories, bugs,
          done: stories.filter((s) => s.status === "done").length,
          total: stories.length,
          bugsOpen: bugs.filter((b) => b.status !== "done").length,
        };
      })
      .sort((a, b) =>
        String(b.epic.updatedAt || b.epic.createdAt || "").localeCompare(
          String(a.epic.updatedAt || a.epic.createdAt || ""),
        ));
  }, [tickets]);

  const selected = useMemo(
    () => projects.find((p) => p.missionId === selectedMission) ?? null,
    [projects, selectedMission],
  );

  const byColumn = useMemo(() => {
    const map: Record<TicketStatus, Ticket[]> = {
      todo: [], in_progress: [], qa: [], in_review: [], done: [], reopened: [], blocked: [],
    };
    if (selected) for (const t of selected.items) map[columnOf(t)].push(t);
    return map;
  }, [selected]);

  return (
    <div>
      <style>{PAGE_CSS}</style>
      <div className="page-header">
        <div>
          <h1 className="page-title">Tickets</h1>
          <p className="page-desc">
            {selected
              ? "This project's board — the Stories and Bugs the PM broke it into, each with its live status, owner, and the QA evidence behind every verdict."
              : "Pick a project to open its board. Each project's work is broken into Stories (one per build subtask) and Bugs (one per QA finding), tracked live from the pipeline."}
          </p>
        </div>
        {features?.tickets && (
          <div className="page-actions" style={{ alignItems: "center" }}>
            <span className="tk-live" title="The board updates automatically as the team works">
              <span className="tk-live-dot" /> Live
            </span>
            {selected && (
              <Button variant="subtle" onClick={() => setSelectedMission(null)}>
                <span style={{ display: "inline-flex", transform: "rotate(180deg)" }}>
                  <Icon name="arrow" size={15} />
                </span>
                All projects
              </Button>
            )}
            <Button variant="subtle" onClick={() => void reload()}>
              <Icon name="refresh" size={15} /> Refresh
            </Button>
          </div>
        )}
      </div>

      {error && <div className="lb-error">{error}</div>}
      {!features && !error && <p style={{ color: "var(--faint)" }}>Loading…</p>}

      {features && !features.tickets && (
        <div className="card pad center" style={{ padding: "48px 24px", maxWidth: 520, margin: "40px auto" }}>
          <div className="row" style={{ justifyContent: "center", marginBottom: 12 }}>
            <Icon name="ticket" size={30} />
          </div>
          <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>The ticket board is off</h3>
          <p className="faint" style={{ fontSize: 13.5, marginBottom: 18, lineHeight: 1.5 }}>
            Turn it on to track every mission as an Epic, each build subtask as a Story, and each QA
            finding as a Bug — with evidence and a full activity trail. Enabling backfills Epics for
            your existing missions.
          </p>
          <Button onClick={() => void enableBoard()} disabled={enabling}>
            <Icon name="check" size={15} /> {enabling ? "Enabling…" : "Enable ticket board"}
          </Button>
        </div>
      )}

      {/* Project picker — one card per project (Epic). */}
      {features?.tickets && tickets && !selected && (
        projects.length === 0 ? (
          <div className="card pad center" style={{ padding: "40px 20px", color: "var(--faint)" }}>
            No projects yet — start a mission and it appears here with its tickets.
          </div>
        ) : (
          <div className="proj-grid">
            {projects.map((p) => (
              <ProjectCard key={p.epic.id} p={p} onOpen={() => setSelectedMission(p.missionId)} />
            ))}
          </div>
        )
      )}

      {/* Scoped board — the selected project's Stories + Bugs. */}
      {features?.tickets && selected && (
        <>
          <div className="proj-head">
            <div style={{ minWidth: 0 }}>
              <div className="row gap-8" style={{ marginBottom: 4 }}>
                <Badge tone={KIND_META.epic.tone}><Icon name="rocket" size={11} /> Epic</Badge>
                <span className="mono text-xs faint">{selected.epic.key}</span>
                <Badge tone={STATUS_TONE[selected.epic.status]} dot>{STATUS_LABEL[selected.epic.status]}</Badge>
              </div>
              <h2 className="proj-head-title">{selected.epic.title}</h2>
              <div className="proj-meta">
                {selected.total} stor{selected.total === 1 ? "y" : "ies"} · {selected.done} done
                {selected.bugs.length ? ` · ${selected.bugs.length} bug${selected.bugs.length === 1 ? "" : "s"}` : ""}
              </div>
            </div>
          </div>
          {selected.items.length === 0 ? (
            <div className="card pad center" style={{ padding: "40px 20px", color: "var(--faint)" }}>
              No tickets yet — the PM breaks this project into Stories (and Bugs) as the build runs.
            </div>
          ) : (
            <div className="board">
              {COLUMNS.map((col) => (
                <div key={col.key} className="column">
                  <div className="column-head">
                    <span className="dot" style={{ background: col.color }} />
                    <span className="col-name">{col.label}</span>
                    <span className="col-count">{byColumn[col.key].length}</span>
                  </div>
                  <div>
                    {byColumn[col.key].length === 0 ? (
                      <div
                        className="center text-xs faint"
                        style={{ border: "1px dashed var(--line-2)", borderRadius: 11, padding: "18px 10px" }}
                      >
                        —
                      </div>
                    ) : (
                      byColumn[col.key].map((t) => (
                        <TicketCard key={t.id} ticket={t} flash={flashIds.has(t.id)} onOpen={() => setOpenId(t.id)} />
                      ))
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {openId && detail && (
        <TicketDrawer detail={detail} keyOf={keyOf} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}

/* Scoped styles for drawer sections + the evidence gallery (reuses global tokens/classes). */
const PAGE_CSS = `
/* live board: "Live" pill + a brief flash when a card changes column (a transition just happened) */
.tk-live { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700;
  color: var(--green); padding: 5px 11px; border-radius: 999px;
  background: color-mix(in srgb, var(--green) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--green) 30%, var(--line)); }
.tk-live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green);
  box-shadow: 0 0 0 0 color-mix(in srgb, var(--green) 60%, transparent); animation: tk-pulse 1.8s ease-out infinite; }
@keyframes tk-pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--green) 55%, transparent); }
  70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
.kanban-card-flash { animation: tk-card-flash 1.4s ease-out; }
@keyframes tk-card-flash {
  0% { box-shadow: 0 0 0 2px var(--brand), 0 8px 22px -8px color-mix(in srgb, var(--brand) 60%, transparent);
       transform: translateY(-2px); }
  100% { box-shadow: none; transform: none; } }
/* project picker */
.proj-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.proj-card { display: flex; flex-direction: column; gap: 4px; text-align: left; width: 100%;
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 15px 16px;
  cursor: pointer; box-shadow: var(--shadow-sm); transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease; }
.proj-card:hover { transform: translateY(-3px); border-color: var(--line-2); box-shadow: var(--shadow); }
.proj-title { font-family: var(--display); font-weight: 700; font-size: 15.5px; line-height: 1.3; margin-top: 6px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.proj-meta { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; color: var(--muted); }
.proj-dot { color: var(--faint); }
.proj-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 13px;
  padding-top: 11px; border-top: 1px solid var(--line); min-width: 0; }
.proj-foot > .chip { min-width: 0; overflow: hidden; }
.proj-foot > .chip > .truncate, .proj-foot .chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proj-open { font-size: 11.5px; font-weight: 700; color: var(--brand); white-space: nowrap; flex: 0 0 auto; }
/* scoped board header */
.proj-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 15px 18px; margin-bottom: 16px; }
.proj-head-title { font-family: var(--display); font-weight: 800; font-size: 20px; letter-spacing: -.01em; line-height: 1.25; }
.stack-14 > section + section { margin-top: 14px; }
.drawer-h { font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--faint); margin-bottom: 6px; }
.drawer-p { font-size: 13.5px; line-height: 1.55; color: var(--text); white-space: pre-wrap; }
.drawer-ul { margin: 0; padding-left: 18px; font-size: 13.5px; line-height: 1.6; color: var(--text); }
.evidence-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.evidence-thumb { display: block; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--panel-2); aspect-ratio: 16 / 10; }
.evidence-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.feed { list-style: none; margin: 0; padding: 0; }
.feed-row { display: flex; gap: 11px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.feed-row:last-child { border-bottom: 0; }
.feed-row { padding: 12px 0; }
.feed-dot { flex: 0 0 28px; width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; background: var(--panel-2); border: 1px solid var(--line); color: var(--muted); }
.feed-dot.err { color: var(--red); border-color: var(--red); }
/* compact colored actor in a feed row — role-colored avatar + name + role */
.feed-actor { display: inline-flex; align-items: center; gap: 6px; }
.feed-ava { width: 19px; height: 19px; border-radius: 50%; display: grid; place-items: center;
  color: #fff; font-size: 9px; font-weight: 800; flex: 0 0 auto; }
.feed-actor-name { font-size: 12.5px; font-weight: 700; color: var(--text); }
.feed-actor-role { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em;
  padding: 1px 6px; border-radius: 999px; background: color-mix(in srgb, var(--rc) 15%, transparent); }
/* From → To status transition chips */
.feed-transition { display: inline-flex; align-items: center; gap: 5px; }
.feed-transition svg { color: var(--faint); flex: 0 0 auto; }
.mt-4 { margin-top: 4px; }
.stack-14 { display: block; }
/* drawer polish: kind-colored top accent + colored section markers + callouts + checklists */
.tk-drawer { position: relative; overflow: hidden; }
.tk-accent { position: absolute; top: 0; left: 0; right: 0; height: 4px; background: var(--accent);
  background: linear-gradient(90deg, var(--accent), color-mix(in srgb, var(--accent) 55%, transparent)); z-index: 1; }
.drawer-h { display: flex; align-items: center; font-size: 11px; font-weight: 800; letter-spacing: .07em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 9px; }
.dh-mark { width: 4px; height: 13px; border-radius: 2px; margin-right: 8px; flex: 0 0 auto; }
/* section separation — a hairline + breathing room between Goal / Acceptance / DoD / Activity */
.tk-sections { display: flex; flex-direction: column; }
.tk-sections > section { padding: 16px 0; }
.tk-sections > section:first-child { padding-top: 2px; }
.tk-sections > section + section { border-top: 1px solid var(--line); }
/* Goal reads as a soft callout card with real emphasis, not a flat paragraph */
.tk-goal { font-size: 13.5px; line-height: 1.6; color: var(--text); white-space: pre-wrap;
  background: color-mix(in srgb, var(--brand) 5%, var(--panel-2)); border: 1px solid var(--line);
  border-left: 3px solid var(--brand); border-radius: 10px; padding: 12px 14px; }
.tk-goal strong { font-weight: 700; color: var(--text); }
.tk-goal em { font-style: italic; color: var(--muted); }
.tk-code, .tk-goal code { font-family: var(--mono); font-size: 12px; padding: 1px 5px; border-radius: 5px;
  background: color-mix(in srgb, var(--brand) 12%, transparent); color: var(--brand-2); }
/* assignee: a coloured avatar + name + role, so ownership reads at a glance */
.tk-assignee { display: inline-flex; align-items: center; gap: 7px; padding: 3px 10px 3px 3px;
  border-radius: 999px; background: color-mix(in srgb, var(--rc) 10%, var(--panel-2));
  border: 1px solid color-mix(in srgb, var(--rc) 32%, var(--line)); }
.tk-ava { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center;
  color: #fff; font-size: 10px; font-weight: 800; letter-spacing: .02em; flex: 0 0 auto; }
.tk-ava-name { font-size: 12.5px; font-weight: 700; color: var(--text); }
.tk-ava-role { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
  color: var(--rc); background: color-mix(in srgb, var(--rc) 16%, transparent); padding: 1px 6px; border-radius: 999px; }
.tk-unassigned { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--faint);
  padding: 4px 10px; border-radius: 999px; border: 1px dashed var(--line-2); }
.callout { display: flex; align-items: flex-start; gap: 9px; padding: 11px 13px; border-radius: 11px;
  font-size: 13.5px; line-height: 1.5; color: var(--text); }
.callout-red { background: color-mix(in srgb, var(--red) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--red) 26%, transparent); }
.callout-red > svg { color: var(--red); flex: 0 0 auto; margin-top: 1px; }
.check-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.check-list li { display: flex; align-items: flex-start; gap: 9px; font-size: 13.5px; line-height: 1.45; color: var(--text); }
.check-list li > span:last-child { min-width: 0; overflow-wrap: anywhere; }
.ck { flex: 0 0 18px; width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; margin-top: 1px; }
.ck-green { color: var(--green); background: color-mix(in srgb, var(--green) 16%, transparent); }
.ck-cyan { color: var(--cyan); background: color-mix(in srgb, var(--cyan) 16%, transparent); }
.owned-chip { color: var(--amber); background: color-mix(in srgb, var(--amber) 10%, transparent) !important;
  border-color: color-mix(in srgb, var(--amber) 30%, transparent) !important; font-size: 11px; }
/* evidence thumbnails (buttons) + hover zoom affordance */
.evidence-thumb { position: relative; padding: 0; cursor: pointer; }
.evidence-thumb:hover { border-color: var(--brand); }
.evidence-zoom { position: absolute; inset: 0; display: grid; place-items: center; color: #fff;
  background: rgba(6, 8, 15, .38); opacity: 0; transition: opacity .15s ease; }
.evidence-thumb:hover .evidence-zoom { opacity: 1; }
/* in-app lightbox — centered overlay above the drawer, never a new tab / bottom dock */
.lightbox { position: fixed; inset: 0; z-index: 90; display: grid; place-items: center; padding: 44px;
  background: rgba(4, 5, 10, .82); backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px); animation: fade .18s ease; }
.lb-img { max-width: min(1120px, 92vw); max-height: 86vh; border-radius: 12px; box-shadow: var(--shadow);
  background: #fff; }
.lb-frame { width: min(1120px, 92vw); height: 86vh; border: 0; border-radius: 12px; background: #fff; box-shadow: var(--shadow); }
.lightbox .icon-btn { color: #fff; background: rgba(255, 255, 255, .12); border: 1px solid rgba(255, 255, 255, .22); }
.lightbox .icon-btn:hover { background: rgba(255, 255, 255, .22); }
.lb-close { position: absolute; top: 20px; right: 24px; }
.lb-nav { position: absolute; top: 50%; transform: translateY(-50%); }
.lb-prev { left: 24px; }
.lb-next { right: 24px; }
.lb-count { position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%);
  color: rgba(255, 255, 255, .85); font-size: 12px; font-weight: 600; }
`;
