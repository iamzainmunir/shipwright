"use client";

import { Badge, type BadgeTone, Button, Icon, type IconName, cx } from "@foundry/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isApiError } from "@/lib/api";
import { useConfirm } from "@/components/confirm";
import { useShellData } from "@/components/shell/shell-data";
import { useToast } from "@/components/toast";
import {
  type Blocker,
  type Mission,
  type MissionStage,
  PRIORITY_LABEL,
  type Priority,
  type Team,
  deleteMission,
  importMissions,
  listBlockers,
  listMissions,
  listTeams,
} from "@/lib/foundry";

/* ------------------------------------------------------------------ */
/* Static reference tables (stages, priorities, blockers, stage teams) */
/* ------------------------------------------------------------------ */

interface StageMeta {
  key: MissionStage;
  label: string;
  color: string;
  tone: BadgeTone;
}

/** Column order + colour per stage — matches the mockup's flow left→right. */
const STAGES: StageMeta[] = [
  { key: "backlog", label: "Backlog", color: "var(--muted)", tone: "neutral" },
  { key: "spec", label: "Spec", color: "var(--cyan)", tone: "cyan" },
  { key: "building", label: "Building", color: "var(--brand)", tone: "brand" },
  { key: "review", label: "Review", color: "var(--blue)", tone: "blue" },
  { key: "qa", label: "QA", color: "var(--amber)", tone: "amber" },
  { key: "shipped", label: "Shipped", color: "var(--green)", tone: "green" },
];
/**
 * A "Stopped" column for suspended work — a mission whose latest run was force-stopped or failed
 * (and hasn't shipped). The engine also parks such a mission at stage="stopped" so every surface
 * agrees. It's not a real pipeline stage; the card keeps its stage badge, it just parks here until
 * retried. (Blocked = awaiting your approval at the gate stays in its stage — it's waiting on you.)
 */
type ColumnKey = MissionStage | "stopped";
interface ColumnMeta {
  key: ColumnKey;
  label: string;
  color: string;
  tone: BadgeTone;
}
const STOPPED_META: ColumnMeta = { key: "stopped", label: "Stopped", color: "var(--red)", tone: "red" };
const COLUMNS: ColumnMeta[] = [...STAGES, STOPPED_META];
// Lookup by stage key, INCLUDING "stopped" (the engine now uses it as a real stage value), so a
// stopped mission's badge/colour resolve instead of crashing on an undefined entry.
const STAGE_BY_KEY: Record<ColumnKey, ColumnMeta> = Object.fromEntries(
  COLUMNS.map((c) => [c.key, c]),
) as Record<ColumnKey, ColumnMeta>;
const STOPPED_RUN_STATUS = new Set(["cancelled", "failed"]);

/** Which board column a mission belongs in — the Stopped column overrides its stage. */
function columnOf(m: Mission): ColumnKey {
  if (m.stage === "stopped") return "stopped";
  if (m.stage !== "shipped" && m.lastRunStatus != null && STOPPED_RUN_STATUS.has(m.lastRunStatus)) {
    return "stopped";
  }
  return m.stage;
}

/**
 * Run statuses that mean a mission is settled enough to delete from the board — no run is in
 * flight. The API rejects a delete with 422 while a run is active, so we only offer the affordance
 * when the last run is null (never run) or terminal (succeeded | failed | cancelled).
 */
const DELETABLE_RUN_STATUS = new Set(["succeeded", "failed", "cancelled"]);
function canDeleteMission(m: Mission): boolean {
  return m.lastRunStatus == null || DELETABLE_RUN_STATUS.has(m.lastRunStatus);
}

const PRIORITY_TONE: Record<Priority, BadgeTone> = { p0: "red", p1: "amber", p2: "blue", p3: "neutral" };

/** How each blocker kind reads on a card (label + one-click action verb + glyph). */
const BLOCKER_KINDS: Record<string, { label: string; action: string; icon: IconName }> = {
  approval: { label: "Awaiting approval", action: "Review & approve", icon: "check" },
  question: { label: "Needs your input", action: "Answer", icon: "command" },
  limit: { label: "Usage limit reached", action: "Raise limit", icon: "bolt" },
  token: { label: "Token expired", action: "Reconnect", icon: "plug" },
  budget: { label: "Budget cap hit", action: "Raise budget", icon: "dollar" },
  error: { label: "Build failed", action: "View log", icon: "bug" },
  dependency: { label: "Blocked by a dependency", action: "View", icon: "branch" },
};
const DEFAULT_BLOCKER: { label: string; action: string; icon: IconName } = {
  label: "Awaiting approval",
  action: "Review & approve",
  icon: "check",
};
const blockerMeta = (kind: string) => BLOCKER_KINDS[kind] ?? DEFAULT_BLOCKER;
const isHard = (b: Blocker) => b.severity === "block";
const blockerTint = (b: Blocker): "tint-red" | "tint-amber" => (isHard(b) ? "tint-red" : "tint-amber");
const blockerColor = (b: Blocker) => (isHard(b) ? "var(--red)" : "var(--amber)");

const IN_FLIGHT: MissionStage[] = ["spec", "building", "review", "qa"];

/**
 * Stage-transition flash. When a card changes column between polls we add `.moved`
 * for ~1.2s: a subtle brand-tinted ring + slight lift so the movement is easy to
 * catch on a live board. Colours are token-only (var(--brand)/var(--line)) so it
 * reads correctly in both light and dark themes, and honours reduced-motion.
 */
const BOARD_CSS = `
@keyframes cardMoved {
  0%   { box-shadow: 0 0 0 0 color-mix(in srgb, var(--brand) 55%, transparent); }
  25%  { box-shadow: 0 0 0 4px color-mix(in srgb, var(--brand) 30%, transparent); transform: translateY(-2px) scale(1.015); }
  100% { box-shadow: var(--shadow-sm); transform: none; }
}
.kanban-card.moved {
  border-color: color-mix(in srgb, var(--brand) 55%, var(--line));
  animation: cardMoved 1.2s ease-out;
}
@media (prefers-reduced-motion: reduce) {
  .kanban-card.moved { animation: none; }
}
/* Subtle per-card delete: a small close button in the top-right corner that fades in on hover
   (or focus, for keyboard users). Token-only colours so it reads in both themes; on touch, where
   there is no hover, it stays faintly visible so it remains reachable. */
.kanban-card { position: relative; }
.kanban-card .card-del {
  position: absolute; top: 10px; right: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; padding: 0; border-radius: 7px;
  border: 1px solid var(--line); background: var(--panel);
  color: var(--faint); cursor: pointer; opacity: 0;
  transition: opacity .14s ease, color .14s ease, border-color .14s ease;
}
.kanban-card:hover .card-del,
.kanban-card .card-del:focus-visible { opacity: 1; }
.kanban-card .card-del:hover {
  color: var(--red);
  border-color: color-mix(in srgb, var(--red) 45%, var(--line));
}
@media (hover: none) { .kanban-card .card-del { opacity: .7; } }
`;

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/** Accept both ISO timestamps and pre-humanised strings ("4m ago"). */
function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const t = Date.parse(value);
  if (Number.isNaN(t)) return value;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

const missionHref = (m: Mission) => `/dashboard/missions/${m.key}`;

/* ------------------------------------------------------------------ */
/* Presentational components                                           */
/* ------------------------------------------------------------------ */

function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <Badge tone={PRIORITY_TONE[priority]} style={{ minWidth: 34, justifyContent: "center" }}>
      {PRIORITY_LABEL[priority] ?? priority}
    </Badge>
  );
}

function LabelChips({ labels }: { labels: string[] }) {
  if (labels.length === 0) return null;
  const shown = labels.slice(0, 3);
  const extra = labels.length - shown.length;
  const chip = { fontSize: 10.5, fontWeight: 600, padding: "2px 8px", borderRadius: 6 } as const;
  return (
    <div className="row wrap gap-6 mt-12">
      {shown.map((l) => (
        <span key={l} className="chip" style={chip}>
          {l}
        </span>
      ))}
      {extra > 0 && (
        <span className="chip" style={chip}>
          +{extra}
        </span>
      )}
    </div>
  );
}

function ProgressRow({ value }: { value: number }) {
  return (
    <div className="row gap-8 mt-12">
      <div className="progress thin flex-1">
        <div className="bar" style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs faint" style={{ minWidth: 30, textAlign: "right" }}>
        {value}%
      </span>
    </div>
  );
}

/** The amber/red "Blocked / Awaiting approval" strip on a card. */
function BlockerPill({ blocker }: { blocker: Blocker }) {
  const meta = blockerMeta(blocker.kind);
  return (
    <div
      className={`row gap-6 mt-12 ${blockerTint(blocker)}`}
      style={{ padding: "5px 10px", borderRadius: 9, fontSize: 11.5, fontWeight: 700 }}
      title={blocker.detail}
    >
      <Icon name={meta.icon} size={12} />
      <span className="truncate">{meta.label}</span>
    </div>
  );
}

/** The role actively working each in-flight stage (real stage → owning role). */
const STAGE_WORKER: Record<string, string> = {
  spec: "PM", building: "Backend", qa: "QA", review: "CTO",
};

function MissionCard({
  mission,
  blocker,
  teamName,
  moved,
  onDelete,
}: {
  mission: Mission;
  blocker: Blocker | null;
  /** Resolved team name for this mission, or null when it runs on the org roster. */
  teamName: string | null;
  /** True for ~1.2s right after this card changed stage — drives the flash animation. */
  moved: boolean;
  /** Delete this mission — omitted (no button) while a run is in flight. */
  onDelete?: () => void;
}) {
  const stage = STAGE_BY_KEY[mission.stage];
  const accent = blocker ? blockerColor(blocker) : stage.color;
  const worker = STAGE_WORKER[mission.stage];
  const live = Boolean(worker) && !blocker && !mission.isBlocked;
  return (
    <Link
      href={missionHref(mission)}
      className={cx("kanban-card", moved && "moved")}
      style={{ display: "block", borderTop: `2px solid ${accent}`, color: "inherit", textDecoration: "none" }}
    >
      {onDelete && (
        <button
          type="button"
          className="card-del"
          aria-label={`Delete mission ${mission.key}`}
          title="Delete mission"
          // Inside the card <Link>: stop the click so it deletes instead of navigating.
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onDelete();
          }}
        >
          <Icon name="close" size={13} />
        </button>
      )}
      <div className="row between gap-8">
        <PriorityBadge priority={mission.priority} />
        <span className="mono text-xs faint truncate" style={{ paddingRight: onDelete ? 26 : undefined }}>
          {mission.key} · {mission.source}
        </span>
      </div>
      <div
        className="fw-6 mt-8"
        style={{
          fontSize: 13.5,
          lineHeight: 1.35,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {mission.title}
      </div>
      {teamName && (
        <div className="row gap-6 mt-8" style={{ minWidth: 0 }}>
          <span className="chip" style={{ fontSize: 10.5, fontWeight: 600, padding: "2px 8px", borderRadius: 6 }}>
            <Icon name="users" size={11} /> <span className="truncate">{teamName}</span>
          </span>
        </div>
      )}
      {blocker ? (
        <BlockerPill blocker={blocker} />
      ) : mission.isBlocked ? (
        <div className="mt-12">
          <Badge tone="neutral" dot>
            blocked
          </Badge>
        </div>
      ) : null}
      <ProgressRow value={mission.progress} />
      <LabelChips labels={mission.labels} />
      <div className="row between mt-12">
        {live ? (
          <span className="row gap-6 text-xs fw-6" style={{ color: "var(--green)" }}>
            <span className="dot pulse bg-green" /> {worker} working
          </span>
        ) : (
          <span className="text-xs faint">{mission.stage === "shipped" ? "Shipped" : "—"}</span>
        )}
        <span className="text-xs faint">{relativeTime(mission.updatedAt)}</span>
      </div>
    </Link>
  );
}

function KanbanColumn({
  stage,
  missions,
  blockerOf,
  teamNameOf,
  isMoved,
  onDelete,
}: {
  stage: ColumnMeta;
  missions: Mission[];
  blockerOf: (m: Mission) => Blocker | null;
  teamNameOf: (m: Mission) => string | null;
  isMoved: (m: Mission) => boolean;
  /** Delete a mission from the board — returns null when that mission has a run in flight. */
  onDelete: (m: Mission) => (() => void) | null;
}) {
  return (
    <div className="column">
      <div className="column-head">
        <span className="dot" style={{ background: stage.color }} />
        <span className="col-name">{stage.label}</span>
        <span className="col-count">{missions.length}</span>
      </div>
      {missions.length > 0 ? (
        missions.map((m) => (
          <MissionCard
            key={m.id}
            mission={m}
            blocker={blockerOf(m)}
            teamName={teamNameOf(m)}
            moved={isMoved(m)}
            onDelete={onDelete(m) ?? undefined}
          />
        ))
      ) : (
        <div
          className="center text-xs faint"
          style={{ border: "1px dashed var(--line-2)", borderRadius: 11, padding: "20px 10px" }}
        >
          No missions
        </div>
      )}
    </div>
  );
}

function MissionRow({
  mission,
  blocker,
  teamName,
}: {
  mission: Mission;
  blocker: Blocker | null;
  teamName: string | null;
}) {
  const router = useRouter();
  const stage = STAGE_BY_KEY[mission.stage];
  const go = () => router.push(missionHref(mission));
  return (
    <tr
      role="button"
      aria-label={`Open mission ${mission.key}: ${mission.title}`}
      style={{ cursor: "pointer" }}
      tabIndex={0}
      onClick={go}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      }}
    >
      <td>
        <div className="row gap-8">
          <span className="mono text-xs faint">{mission.key}</span>
          <span className="text-xs faint">{mission.source}</span>
        </div>
        <div className="fw-6 truncate" style={{ marginTop: 3, maxWidth: 360 }}>
          {mission.title}
        </div>
        {blocker ? (
          <span className={`badge ${blockerTint(blocker)}`} style={{ marginTop: 5 }}>
            <Icon name={blockerMeta(blocker.kind).icon} size={11} />
            {blockerMeta(blocker.kind).label}
          </span>
        ) : mission.isBlocked ? (
          <span className="badge" style={{ marginTop: 5 }}>
            blocked
          </span>
        ) : null}
      </td>
      <td>
        <Badge tone={stage.tone} dot>
          {stage.label}
        </Badge>
      </td>
      <td>
        <PriorityBadge priority={mission.priority} />
      </td>
      <td>
        <span className="pill">{mission.autonomy}</span>
      </td>
      <td>
        <div className="row gap-8">
          <div className="progress thin" style={{ width: 88 }}>
            <div className="bar" style={{ width: `${mission.progress}%` }} />
          </div>
          <span className="text-xs faint">{mission.progress}%</span>
        </div>
      </td>
      <td>
        {teamName ? (
          <span className="badge" style={{ maxWidth: 160 }}>
            <Icon name="users" size={11} />
            <span className="truncate">{teamName}</span>
          </span>
        ) : (
          <span className="text-xs faint">—</span>
        )}
      </td>
      <td>
        <span className="text-xs faint">{relativeTime(mission.updatedAt)}</span>
      </td>
    </tr>
  );
}

function MissionTable({
  missions,
  blockerOf,
  teamNameOf,
}: {
  missions: Mission[];
  blockerOf: (m: Mission) => Blocker | null;
  teamNameOf: (m: Mission) => string | null;
}) {
  if (missions.length === 0) {
    return (
      <div className="card">
        <div className="empty">
          <div className="em-ic">◈</div>
          No missions match your filters.
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div style={{ overflowX: "auto" }}>
        <table className="table" style={{ minWidth: 780 }}>
          <thead>
            <tr>
              <th>Mission</th>
              <th>Stage</th>
              <th>Priority</th>
              <th>Autonomy</th>
              <th>Progress</th>
              <th>Team</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {missions.map((m) => (
              <MissionRow key={m.id} mission={m} blocker={blockerOf(m)} teamName={teamNameOf(m)} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Always drawn from ALL blocked missions, independent of the active filters. */
function AttentionPanel({ items }: { items: { mission: Mission; blocker: Blocker }[] }) {
  if (items.length === 0) return null;
  const hard = items.some(({ blocker }) => isHard(blocker));
  const edge = hard ? "var(--red)" : "var(--amber)";
  return (
    <div
      className="card pad mb-20"
      style={{
        borderColor: `color-mix(in srgb, ${edge} 32%, var(--line))`,
        // Subtle amber/red wash that fades back into the card — token-only so it
        // reads correctly in both light and dark (mirrors the mockup's gradient).
        background: `linear-gradient(180deg, color-mix(in srgb, ${edge} 7%, var(--panel)), var(--panel) 72%)`,
      }}
    >
      <div className="row between wrap mb-16">
        <span className="row gap-8">
          <span style={{ color: edge, display: "inline-flex" }}>
            <Icon name="bell" size={16} />
          </span>
          <b>
            {items.length} mission{items.length !== 1 ? "s" : ""} need your attention
          </b>
        </span>
        <span className="text-xs faint">Approve, reconnect, answer or raise a limit to unblock the team</span>
      </div>
      <div className="col gap-8">
        {items.map(({ mission, blocker }) => {
          const meta = blockerMeta(blocker.kind);
          return (
            <div
              key={mission.id}
              className="row gap-12"
              style={{
                padding: "11px 12px",
                borderRadius: 12,
                background: "var(--panel-2)",
                border: "1px solid var(--line)",
              }}
            >
              <span
                className={`center ${blockerTint(blocker)}`}
                style={{ width: 38, height: 38, borderRadius: 11, flex: "0 0 38px" }}
              >
                <Icon name={meta.icon} size={16} />
              </span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="row wrap gap-8">
                  <span className="mono text-xs faint">{mission.key}</span>
                  <PriorityBadge priority={mission.priority} />
                  <span className={`badge ${blockerTint(blocker)}`}>
                    <Icon name={meta.icon} size={11} />
                    {meta.label}
                  </span>
                  <span className="text-xs faint">{STAGE_BY_KEY[mission.stage].label}</span>
                </div>
                <div className="fw-6 text-sm truncate mt-4">{mission.title}</div>
                <div className="li-sub truncate">
                  {blocker.detail}
                  {/* Only shown when the API actually carries a raised-at timestamp — never fabricated. */}
                  {blocker.createdAt && (
                    <span className="faint"> · blocked {relativeTime(blocker.createdAt)}</span>
                  )}
                </div>
              </div>
              <Link
                href={missionHref(mission)}
                className="btn sm primary"
                style={{ flex: "0 0 auto", textDecoration: "none" }}
              >
                <Icon name={meta.icon} size={14} />
                {meta.action}
              </Link>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface StatTileProps {
  icon: IconName;
  tint: string;
  label: string;
  value: number;
  foot: React.ReactNode;
  ring?: string;
}
function StatTile({ icon, tint, label, value, foot, ring }: StatTileProps) {
  return (
    <div className="stat" style={ring ? { boxShadow: `inset 0 0 0 1px ${ring}` } : undefined}>
      <div className={`stat-ic ${tint}`}>
        <Icon name={icon} size={17} />
      </div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-trend">{foot}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Import-from-Jira modal                                              */
/* ------------------------------------------------------------------ */

/**
 * "Import from Jira" modal — turns pasted Jira issues into real missions.
 *
 * The textarea accepts either one issue per line ("PROJ-123 Add login screen") or a pasted Jira
 * JSON export; the server (`importMissions`) parses both and creates a real mission per issue —
 * no live Jira sync is faked. On success we close, toast how many landed, then ask the board to
 * reload so the freshly-created missions show up at once.
 */
function ImportJiraModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  /** Called after a successful import so the board can re-fetch its missions. */
  onImported: () => void;
}) {
  const { toast } = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape closes the modal (mirrors the confirm/new-mission modals).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const canImport = text.trim().length > 0 && !busy;

  async function runImport() {
    if (!canImport) return;
    setBusy(true);
    setError(null);
    try {
      const res = await importMissions(text.trim());
      onClose();
      toast(
        "Imported from Jira",
        `${res.imported} mission${res.imported !== 1 ? "s" : ""} added to Backlog`,
        "ok",
      );
      onImported(); // pull the new missions onto the board now rather than waiting for the poll
    } catch (e) {
      // Surface the API message inline so the user can fix the paste and retry.
      setError(
        isApiError(e) ? e.message : "Couldn't import those issues — check the format and try again.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Import from Jira"
        style={{ maxWidth: 540 }}
      >
        <div className="modal-head">
          <h3 className="row" style={{ gap: 10 }}>
            <Icon name="external" size={16} /> Import from Jira
          </h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="imp-text">Paste Jira issues</label>
            <textarea
              id="imp-text"
              className="textarea mono"
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="One per line — e.g. PROJ-123 Add login screen — or paste a Jira JSON export."
              style={{ minHeight: 160 }}
            />
            <span className="hint">
              One issue per line (<span className="mono">PROJ-123 Add login screen</span>), or paste a
              Jira JSON export. Each issue becomes a real mission in Backlog for the PM agent to triage.
            </span>
          </div>
          {error && (
            <div className="lb-error" style={{ marginTop: 12 }}>
              {error}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!canImport} onClick={runImport}>
            <Icon name="external" size={15} /> {busy ? "Importing…" : "Import"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

type View = "board" | "list";
type PriorityFilter = "all" | Priority;

export default function MissionsPage() {
  const [missions, setMissions] = useState<Mission[] | null>(null);
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [view, setView] = useState<View>("board");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<PriorityFilter>("all");
  const [source, setSource] = useState("all");
  const [project, setProject] = useState("all"); // filter to one mission/project by its key
  const [attnOnly, setAttnOnly] = useState(false);

  // Per-mission previous stage + the keys currently flashing (moved column this poll).
  const prevStageRef = useRef<Map<string, ColumnKey>>(new Map());
  const [movedKeys, setMovedKeys] = useState<ReadonlySet<string>>(() => new Set());

  const confirm = useConfirm();
  // The nav badges (Missions/Tickets counts) live in shell-data, which loads once. Refresh it after
  // a delete so the sidebar count drops in step with the board instead of going stale until reload.
  const { refresh: refreshShell } = useShellData();

  // Import-from-Jira modal open state (mirrors the mockup's "Import from Jira" affordance).
  const [importOpen, setImportOpen] = useState(false);

  /**
   * Pull the board's data (missions + their blockers) in one shot. Shared by the 1.5s live poll
   * and by an explicit refresh right after a Jira import, so freshly-created missions appear at
   * once instead of waiting for the next poll tick. Stable identity (empty deps) so the polling
   * effect below runs only once.
   */
  const reload = useCallback(async () => {
    const [m, b] = await Promise.all([listMissions(), listBlockers()]);
    setMissions(m);
    setBlockers(b);
  }, []);

  useEffect(() => {
    let active = true;
    const load = (first: boolean) =>
      reload().catch((e) => {
        // Surface a hard error only on the first load; a transient poll blip is ignored so it
        // never wipes an already-rendered board.
        if (active && first) setError(isApiError(e) ? e.message : "Failed to load missions");
      });
    void load(true);
    // Live board: re-poll frequently so cards visibly move between stage columns as runs progress.
    const t = setInterval(() => void load(false), 1500);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [reload]);

  // Teams change rarely, so fetch them once (not on the 1.5s board poll). On failure we simply
  // show no team chips rather than block the board.
  useEffect(() => {
    let active = true;
    listTeams()
      .then((next) => { if (active) setTeams(next); })
      .catch(() => { /* board still works without team chips */ });
    return () => { active = false; };
  }, []);

  /** teamId → team name, for the subtle team chip on each card/row. */
  const teamNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of teams) map.set(t.id, t.name);
    return map;
  }, [teams]);

  const teamNameOf = useMemo(
    () =>
      (m: Mission): string | null =>
        (m.teamId ? teamNameById.get(m.teamId) : undefined) ?? null,
    [teamNameById],
  );

  /** missionId → first open blocker. Blockers are cross-referenced by mission. */
  const blockerByMission = useMemo(() => {
    const map = new Map<string, Blocker>();
    for (const b of blockers) {
      if (b.resolvedAt) continue;
      if (!map.has(b.missionId)) map.set(b.missionId, b);
    }
    return map;
  }, [blockers]);

  const blockerOf = useMemo(
    () =>
      (m: Mission): Blocker | null =>
        // Only surface a real Blocker record — never invent a kind/detail/action for a
        // mission that is merely flagged blocked. Those show a neutral "blocked" label instead.
        blockerByMission.get(m.id) ?? blockerByMission.get(m.key) ?? null,
    [blockerByMission],
  );

  // Delete a mission from the board: confirm, call the API, then optimistically drop the card (the
  // 1.5s poll reconciles). The API 422s if a run is active, so we only ever wire this to missions
  // that pass `canDeleteMission`; a surprise 422 still surfaces as the board error banner.
  const handleDelete = useCallback(
    async (m: Mission) => {
      const ok = await confirm({
        title: "Delete this mission?",
        message: `“${m.title}” and all its runs/logs will be removed. This can't be undone.`,
        confirmLabel: "Delete mission",
        tone: "danger",
        icon: "close",
      });
      if (!ok) return;
      try {
        await deleteMission(m.key);
        setMissions((prev) => prev?.filter((x) => x.key !== m.key) ?? prev);
        refreshShell(); // drop the sidebar Missions/Tickets counts in step with the board
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to delete the mission");
      }
    },
    [confirm, refreshShell],
  );

  // A per-mission delete callback for the card — null (no button) while a run is in flight.
  const deleteHandlerOf = useCallback(
    (m: Mission): (() => void) | null => (canDeleteMission(m) ? () => void handleDelete(m) : null),
    [handleDelete],
  );

  const sources = useMemo(
    () => Array.from(new Set((missions ?? []).map((m) => m.source))).sort(),
    [missions],
  );

  // Project options for the filter: each mission is a project. Key disambiguates the
  // label ("M-151 · TODO app simple version"); the option value is the mission key.
  const projects = useMemo(() => {
    const byKey = new Map<string, string>();
    for (const m of missions ?? []) {
      if (!byKey.has(m.key)) byKey.set(m.key, `${m.key} · ${m.title}`);
    }
    return [...byKey.entries()]
      .map(([key, label]) => ({ key, label }))
      .sort((a, b) => a.key.localeCompare(b.key));
  }, [missions]);

  // Detect stage transitions between polls and flash the affected cards for ~1.2s.
  // The removal timer (1200ms) always fires before the next poll (1500ms), so cards
  // reliably clear; the initial load records baselines without flashing anything.
  useEffect(() => {
    if (!missions) return;
    const prev = prevStageRef.current;
    const moved: string[] = [];
    for (const m of missions) {
      const col = columnOf(m); // track the COLUMN so a move to/from "Stopped" also flashes
      const before = prev.get(m.key);
      if (before !== undefined && before !== col) moved.push(m.key);
      prev.set(m.key, col);
    }
    if (moved.length === 0) return;
    setMovedKeys((cur) => new Set([...cur, ...moved]));
    const t = setTimeout(() => {
      setMovedKeys((cur) => {
        const next = new Set(cur);
        for (const k of moved) next.delete(k);
        return next;
      });
    }, 1200);
    return () => clearTimeout(t);
  }, [missions]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (missions ?? []).filter((m) => {
      if (attnOnly && !blockerOf(m)) return false;
      if (priority !== "all" && m.priority !== priority) return false;
      if (source !== "all" && m.source !== source) return false;
      if (project !== "all" && m.key !== project) return false;
      if (q) {
        const hay = `${m.key} ${m.title} ${m.labels.join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [missions, query, priority, source, project, attnOnly, blockerOf]);

  const attention = useMemo(
    () =>
      (missions ?? [])
        .map((m) => ({ mission: m, blocker: blockerOf(m) }))
        .filter((x): x is { mission: Mission; blocker: Blocker } => x.blocker !== null),
    [missions, blockerOf],
  );

  const all = missions ?? [];
  const inFlight = all.filter((m) => IN_FLIGHT.includes(m.stage)).length;
  const shipped = all.filter((m) => m.stage === "shipped").length;
  const p0Open = all.filter((m) => m.priority === "p0" && m.stage !== "shipped").length;
  const hardAttention = attention.some(({ blocker }) => isHard(blocker));

  const priorities: PriorityFilter[] = ["all", "p0", "p1", "p2", "p3"];

  return (
    <div>
      <style>{BOARD_CSS}</style>
      <div className="page-header">
        <div>
          <h1 className="page-title">Missions</h1>
          <p className="page-desc">
            Every ticket the org is working — from raw intake in Backlog through spec, build, QA and review to
            Shipped. Open a mission to watch the AI team build it.
          </p>
        </div>
        <div className="page-actions">
          <div className="segmented" role="tablist" aria-label="View">
            <button
              type="button"
              className={view === "board" ? "active" : ""}
              aria-pressed={view === "board"}
              onClick={() => setView("board")}
            >
              <Icon name="kanban" size={14} /> Board
            </button>
            <button
              type="button"
              className={view === "list" ? "active" : ""}
              aria-pressed={view === "list"}
              onClick={() => setView("list")}
            >
              <Icon name="menu" size={14} /> List
            </button>
          </div>
          {/* Import external tickets (Jira issues) as real missions — mirrors the mockup's
              "Import from Jira" button beside the Board/List toggle. */}
          <Button variant="subtle" onClick={() => setImportOpen(true)}>
            <Icon name="external" size={15} /> Import from Jira
          </Button>
        </div>
      </div>

      {/* Paste Jira issues → real missions in Backlog. Self-contained (owns its own text/busy/
          error state); on success it reloads the board so the new missions appear immediately. */}
      {importOpen && (
        <ImportJiraModal onClose={() => setImportOpen(false)} onImported={() => void reload()} />
      )}

      {error && <div className="lb-error">{error}</div>}
      {!missions && !error && <p style={{ color: "var(--faint)" }}>Loading…</p>}

      {missions && (
        <>
          <div className="grid g-4 mb-20">
            <StatTile
              icon="bell"
              tint={hardAttention ? "tint-red" : attention.length ? "tint-amber" : "tint-brand"}
              label="Needs attention"
              value={attention.length}
              ring={
                attention.length
                  ? `color-mix(in srgb, ${hardAttention ? "var(--red)" : "var(--amber)"} 35%, var(--line))`
                  : undefined
              }
              foot={
                <span className={attention.length ? (hardAttention ? "c-red" : "c-amber") : "faint"}>
                  {attention.length ? "action required" : "all clear"}
                </span>
              }
            />
            <StatTile
              icon="kanban"
              tint="tint-brand"
              label="Missions in flight"
              value={inFlight}
              foot={<span className="faint">spec · build · QA · review</span>}
            />
            <StatTile
              icon="rocket"
              tint="tint-green"
              label="Shipped"
              value={shipped}
              foot={<span className="faint">merged to main</span>}
            />
            <StatTile
              icon="shield"
              tint="tint-red"
              label="P0 open"
              value={p0Open}
              foot={<span className={p0Open ? "c-red" : "faint"}>{p0Open ? "highest priority" : "none open"}</span>}
            />
          </div>

          <div className="card pad row wrap gap-10 mb-20">
            <div style={{ position: "relative", flex: "1 1 220px", minWidth: 200 }}>
              <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--faint)", pointerEvents: "none", display: "inline-flex" }}>
                <Icon name="search" size={16} />
              </span>
              <input
                className="input"
                style={{ paddingLeft: 36, width: "100%" }}
                placeholder="Search key, title, label…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoComplete="off"
                aria-label="Search missions"
              />
            </div>
            <div className="segmented" role="group" aria-label="Filter by priority">
              {priorities.map((p) => (
                <button
                  key={p}
                  type="button"
                  className={priority === p ? "active" : ""}
                  aria-pressed={priority === p}
                  onClick={() => setPriority(p)}
                >
                  {p === "all" ? "All" : PRIORITY_LABEL[p]}
                </button>
              ))}
            </div>
            <select
              className="select"
              style={{ minWidth: 150 }}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              aria-label="Filter by source"
            >
              <option value="all">All sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select
              className="select"
              style={{ minWidth: 170, maxWidth: 260 }}
              value={project}
              onChange={(e) => setProject(e.target.value)}
              aria-label="Filter by project"
            >
              <option value="all">All projects</option>
              {projects.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={`btn sm ${attnOnly ? "primary" : "subtle"}`}
              aria-pressed={attnOnly}
              onClick={() => setAttnOnly((v) => !v)}
            >
              <Icon name="bell" size={14} /> Needs attention
            </button>
            <span className="row gap-12" style={{ marginLeft: "auto" }}>
              <span
                className="row gap-6 text-xs fw-6"
                style={{ color: "var(--green)" }}
                title="Live — the board refreshes automatically"
              >
                <span className="dot pulse bg-green" /> Live
              </span>
              <span className="text-xs faint" style={{ whiteSpace: "nowrap" }}>
                {filtered.length} mission{filtered.length !== 1 ? "s" : ""}
              </span>
            </span>
          </div>

          {/* Needs-attention panel — sits between the filters and the board (mockup parity).
              Always drawn from ALL blocked missions, independent of the active filters. */}
          <AttentionPanel items={attention} />

          {view === "board" ? (
            <div className="board">
              {COLUMNS.map((col) => (
                <KanbanColumn
                  key={col.key}
                  stage={col}
                  missions={filtered.filter((m) => columnOf(m) === col.key)}
                  blockerOf={blockerOf}
                  teamNameOf={teamNameOf}
                  isMoved={(m) => movedKeys.has(m.key)}
                  onDelete={deleteHandlerOf}
                />
              ))}
            </div>
          ) : (
            <MissionTable missions={filtered} blockerOf={blockerOf} teamNameOf={teamNameOf} />
          )}
        </>
      )}
    </div>
  );
}
