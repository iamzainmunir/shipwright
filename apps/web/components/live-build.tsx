"use client";

/**
 * Live Build — the mission-detail showpiece: watch the AI team develop one mission end-to-end.
 *
 * Wired to the real orchestrator: starts a durable run, streams the team console over SSE
 * (with a polling fallback so it stays correct without a socket), renders the phase pipeline
 * from the run's steps, surfaces any code diff a build event carries, and drives the supervised
 * **merge gate** — when the run suspends on an approval blocker, Approve/Reject resolve it via
 * `decideGate` and the run resumes → shipped.
 *
 * Restyled to the "mission control" look using the global design system
 * (app/globals.css) and the @foundry/ui primitives. Sections the API does not
 * expose (acceptance checks, QA rows) are *derived* from real steps/events — never fabricated.
 */

import { Badge, type BadgeTone, Button, Icon, type IconName, cx } from "@foundry/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import {
  AUTONOMY_LEVELS,
  PRIORITY_LABEL,
  type Agent,
  type ClarifyAnswer,
  type EventItem,
  type Mission,
  type MissionStage,
  type Priority,
  type Run,
  type GithubRepo,
  type Step,
  type StepStatus,
  type Team,
  type UpdateMissionInput,
  type PreviewStatus,
  cancelRun,
  decideGate,
  getPreview,
  startPreview,
  stopPreview,
  deleteMission,
  getMission,
  getRun,
  listAgents,
  listBlockers,
  listGithubBranches,
  listGithubRepos,
  listMissionRuns,
  listRunEvents,
  listSteps,
  listTeams,
  requestChange,
  retryRun,
  runExportUrl,
  runStreamUrl,
  startRun,
  submitClarification,
  updateMission,
} from "@/lib/foundry";
import { isApiError } from "@/lib/api";
import { Markdown } from "@/components/markdown";
import { useConfirm } from "@/components/confirm";
import Link from "next/link";
import { useRouter } from "next/navigation";

/* ============================================================
   Static reference (design constants — not fabricated records)
   ============================================================ */

const STEP_TONE: Record<StepStatus, BadgeTone> = {
  done: "green",
  active: "brand",
  gated: "amber",
  blocked: "amber",
  queued: "neutral",
};

const STEP_COLOR: Record<StepStatus, string> = {
  done: "var(--green)",
  active: "var(--brand)",
  gated: "var(--amber)",
  blocked: "var(--amber)",
  queued: "var(--faint)",
};

const STEP_ICON: Record<StepStatus, "check" | "bolt" | "shield" | "clock"> = {
  done: "check",
  active: "bolt",
  gated: "shield",
  blocked: "shield",
  queued: "clock",
};

const STEP_LABEL: Record<StepStatus, string> = {
  done: "done",
  active: "in progress",
  gated: "needs approval",
  blocked: "blocked",
  queued: "queued",
};

const RUN_TONE: Record<string, BadgeTone> = {
  succeeded: "green",
  running: "brand",
  blocked: "amber",
  paused: "amber",
  failed: "red",
  queued: "neutral",
  cancelled: "neutral",
};

const STAGE_TONE: Record<MissionStage, BadgeTone> = {
  backlog: "neutral",
  spec: "cyan",
  building: "brand",
  qa: "amber",
  review: "blue",
  shipped: "green",
  stopped: "neutral",
};

const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  p0: "red",
  p1: "amber",
  p2: "blue",
  p3: "neutral",
};

/**
 * Routing/loop events. The run engine is now a decision graph, so it streams `route.*` events
 * (payload.kind) whenever work moves between phases — a rework loop, an escalation to the CTO,
 * or the CTO's chosen direction. We chip + tint these lines so a human instantly sees a stage
 * transition happened rather than another line of chatter. Tone follows the movement's meaning:
 * amber = a loop back / escalation, blue = a CTO redirect, green = forward progress.
 */
const ROUTE_CHIP: Record<string, { tone: BadgeTone; label: string }> = {
  "route.rework": { tone: "amber", label: "rework" },
  "route.rework.capped": { tone: "amber", label: "rework capped" },
  "route.escalate": { tone: "amber", label: "escalate" },
  "route.redesign": { tone: "blue", label: "redesign" },
  "route.rebuild": { tone: "blue", label: "rebuild" },
  "route.proceed": { tone: "green", label: "proceed" },
};

/** CSS color token per tone we tint route lines with (left border + faint background). */
const ROUTE_TONE_COLOR: Record<string, string> = {
  amber: "var(--amber)",
  blue: "var(--blue)",
  green: "var(--green)",
};

/**
 * Resolve a routing event's chip (tone + short label + tint color). Returns null for any
 * non-route event. Unmapped future `route.*` kinds fall back to a neutral blue transition
 * chip so a new movement still stands out instead of silently reading as plain chatter.
 */
function routeChip(kind: string): { tone: BadgeTone; label: string; color: string } | null {
  if (!kind.startsWith("route.")) return null;
  const known = ROUTE_CHIP[kind];
  const tone = known?.tone ?? "blue";
  const label = known?.label ?? kind.slice("route.".length).replace(/\./g, " ");
  return { tone, label, color: ROUTE_TONE_COLOR[tone] ?? "var(--brand)" };
}

const AVATAR_PALETTE = [
  "var(--brand)",
  "var(--cyan)",
  "var(--green)",
  "var(--amber)",
  "var(--blue)",
  "var(--pink)",
];

const TABS = [
  { key: "pipeline", label: "Pipeline" },
  { key: "spec", label: "Spec & checks" },
  { key: "diff", label: "Code" },
  { key: "qa", label: "QA" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

/* ============================================================
   Small pure helpers
   ============================================================ */

function hueFor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[h % AVATAR_PALETTE.length] ?? "var(--brand)";
}

function initials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  const first = parts[0];
  if (!first) return "?";
  if (parts.length === 1) return first.slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1] ?? first;
  return ((first[0] ?? "") + (last[0] ?? "")).toUpperCase();
}

// Proper display names + avatar abbreviations for agent role keys (pm, backend, …).
const ROLE_LABEL: Record<string, string> = {
  ceo: "CEO", cto: "CTO", pm: "PM", ba: "Business Analyst", backend: "Backend",
  frontend: "Frontend", qa: "QA", devops: "DevOps", designer: "Designer", security: "Security",
};
const ROLE_ABBR: Record<string, string> = {
  ceo: "CE", cto: "CTO", pm: "PM", ba: "BA", backend: "BE",
  frontend: "FE", qa: "QA", devops: "OPS", designer: "DS", security: "SEC",
};
const roleLabel = (role: string): string =>
  ROLE_LABEL[role.toLowerCase()] ?? role.charAt(0).toUpperCase() + role.slice(1);
const roleAbbr = (role: string): string => ROLE_ABBR[role.toLowerCase()] ?? initials(roleLabel(role));

// A parallel build stamps its step detail with the real per-role agent breakdown, e.g.
// `build-roles:[{"role":"backend","count":2},{"role":"frontend","count":1}]`. Solo builds leave
// detail unset, so the step keeps its single-role avatar. Returns null for anything else.
interface RoleCount {
  role: string;
  count: number;
}
function parseBuildRoles(detail?: string | null): RoleCount[] | null {
  if (!detail || !detail.startsWith("build-roles:")) return null;
  try {
    const arr = JSON.parse(detail.slice("build-roles:".length));
    if (Array.isArray(arr) && arr.length > 0 && arr.every((x) => x && typeof x.role === "string")) {
      return arr.map((x) => ({ role: String(x.role), count: Number(x.count) || 1 }));
    }
  } catch {
    /* malformed → fall back to the single-role display */
  }
  return null;
}

/** Plain-English names for the internal phase keys, so non-technical users understand the pipeline. */
const PHASE_LABEL: Record<string, string> = {
  intake: "Understanding the request",
  clarify: "Asking questions",
  spec: "Planning & spec",
  "build.api": "Building",
  qa: "Testing",
  review: "Review",
  "cto.decision": "CTO decision",
  ship: "Merge & deploy",
};
const phaseLabel = (phase: string): string =>
  PHASE_LABEL[phase] ?? phase.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Condense markdown/multi-line text to one clean line (for compact rows like QA checks). */
function summarize(text: string, max = 140): string {
  const clean = (text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/[#*_>~`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function fmtClock(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtWhen(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function autonomyLabel(autonomy: string): string {
  const match = AUTONOMY_LEVELS.find((l) => l.key === autonomy);
  return match ? match.label : autonomy.charAt(0).toUpperCase() + autonomy.slice(1);
}

function isTerminal(status: string | undefined): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/* ---- Pipeline rows (real steps only) ---- */

interface PipelineRow {
  id: string;
  title: string;
  phase: string;
  agentRole: string;
  status: StepStatus;
  detail?: string | null;
}

/** Rows come ONLY from the run's real steps — empty until a run produces them. */
function toPipelineRows(steps: Step[]): PipelineRow[] {
  return steps.map((s) => ({
    id: s.id,
    title: s.title,
    phase: s.phase,
    agentRole: s.agentRole ?? "Agent",
    status: s.status,
    detail: s.detail,
  }));
}

/* ---- Team, derived from who the run assigns to each phase ---- */

interface TeamMember {
  agentId: string;
  name: string;
  roleKey: string;
  level: string;
  status: StepStatus;
  phase: string;
}

const _PHASE_ORDER = ["intake", "clarify", "spec", "plan", "build.api", "review", "qa", "cto.decision", "ship"];
const levelLabel = (l: string): string => (l ? l.charAt(0).toUpperCase() + l.slice(1) : "");

/**
 * The team rail: the actual AGENTS on this mission (name + role + level), not just roles. Parallel
 * build workers are identified by name from the build events (so a frontend agent shows as Frontend);
 * reasoning phases (PM/CTO/QA/DevOps) map their role to the mission team's agent for that role. Status
 * is each agent's current state (done when shipped; live status from the current run otherwise).
 */
function buildTeam(
  agents: Agent[],
  teamMembers: { agentId: string; accountable: boolean }[],
  historyRows: PipelineRow[],
  liveRows: PipelineRow[],
  events: EventItem[],
  shipped: boolean,
): TeamMember[] {
  const byId = new Map(agents.map((a) => [a.id, a]));
  const byName = new Map(agents.map((a) => [a.name, a]));
  const liveByPhase = new Map(liveRows.map((r) => [r.phase, r]));
  const roleAgent = (role: string): Agent | undefined => {
    const inRole = teamMembers
      .map((m) => ({ m, a: byId.get(m.agentId) }))
      .filter((x): x is { m: { agentId: string; accountable: boolean }; a: Agent } =>
        x.a !== undefined && x.a.roleKey === role);
    return inRole.find((x) => x.m.accountable)?.a ?? inRole[0]?.a ?? agents.find((a) => a.roleKey === role);
  };
  const out = new Map<string, TeamMember>();
  const buildStatus: StepStatus = shipped ? "done" : (liveByPhase.get("build.api")?.status ?? "done");
  // 1) Parallel build workers — identified by name in the build events (so each real agent + its
  //    actual role appears, e.g. the frontend engineers building the UI).
  for (const e of events) {
    const who = (e.payload as { agent?: string } | undefined)?.agent;
    if (!who) continue;
    const a = byName.get((String(who).split(" · ")[0] ?? "").trim());
    if (!a || out.has(a.id)) continue;
    out.set(a.id, { agentId: a.id, name: a.name, roleKey: a.roleKey, level: a.level, phase: "build.api", status: buildStatus });
  }
  const hasWorkers = out.size > 0;
  // 2) Reasoning/single-role phases — map each role to the mission team's agent for it.
  for (const row of historyRows) {
    if (row.phase === "build.api" && hasWorkers) continue; // the build is covered by the named workers
    const a = roleAgent(row.agentRole);
    if (!a || out.has(a.id)) continue;
    const status: StepStatus = shipped ? "done" : (liveByPhase.get(row.phase)?.status ?? "done");
    out.set(a.id, { agentId: a.id, name: a.name, roleKey: a.roleKey, level: a.level, phase: row.phase, status });
  }
  return [...out.values()].sort((x, y) => {
    const px = _PHASE_ORDER.indexOf(x.phase), py = _PHASE_ORDER.indexOf(y.phase);
    return (px < 0 ? 99 : px) - (py < 0 ? 99 : py) || x.name.localeCompare(y.name);
  });
}

/* ---- Code diff, parsed from whatever a build event carries ---- */

interface DiffRow {
  kind: "add" | "del" | "ctx" | "hunk"; // hunk = the `@@ … @@` location header
  oldNo: string; // old-file line number (blank for added lines / hunk headers)
  newNo: string; // new-file line number (blank for removed lines / hunk headers)
  code: string;
}
/** One file's diff — a header + its own rows, so the viewer always knows which file it's showing. */
interface DiffFile {
  path: string;
  add: number;
  del: number;
  rows: DiffRow[];
}
interface DiffView {
  files: DiffFile[];
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

/** Git metadata lines that aren't code (never rendered as diff content). */
function isMetaLine(l: string): boolean {
  return (
    l.startsWith("index ") || l.startsWith("--- ") || l.startsWith("+++ ") ||
    l.startsWith("new file") || l.startsWith("deleted file") || l.startsWith("old mode") ||
    l.startsWith("new mode") || l.startsWith("similarity ") || l.startsWith("rename ") ||
    l.startsWith("copy ") || l.startsWith("Binary files") || l.startsWith("\\ ")
  );
}

/**
 * Parse a git unified diff into PER-FILE sections (like GitHub/Bitbucket), each with two-column
 * line numbers. Splitting on `diff --git` headers is what lets the UI show which file each hunk
 * belongs to instead of one undifferentiated blob.
 */
function parseUnifiedByFile(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("diff --git")) {
      const path = raw.match(/ b\/(.+)$/)?.[1] ?? raw.match(/ a\/(.+?) b\//)?.[1] ?? "file";
      files.push({ path, add: 0, del: 0, rows: [] });
      oldNo = 0;
      newNo = 0;
      continue;
    }
    const cur = files[files.length - 1]; // the file currently being parsed (undefined before any)
    if (raw.startsWith("+++ ")) {
      const p = raw.replace(/^\+\+\+ (b\/)?/, "").trim();
      if (cur && p && p !== "/dev/null") cur.path = p;
      continue;
    }
    if (isMetaLine(raw)) continue;
    if (raw.startsWith("@@")) {
      let c = cur;
      if (!c) {
        c = { path: "changes", add: 0, del: 0, rows: [] };
        files.push(c);
      }
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) {
        oldNo = Number.parseInt(m[1] ?? "0", 10);
        newNo = Number.parseInt(m[2] ?? "0", 10);
      }
      c.rows.push({ kind: "hunk", oldNo: "", newNo: "", code: raw });
      continue;
    }
    if (!cur) continue; // preamble before the first file header
    if (raw.startsWith("+")) {
      cur.rows.push({ kind: "add", oldNo: "", newNo: String(newNo++), code: raw.slice(1) });
      cur.add += 1;
    } else if (raw.startsWith("-")) {
      cur.rows.push({ kind: "del", oldNo: String(oldNo++), newNo: "", code: raw.slice(1) });
      cur.del += 1;
    } else {
      cur.rows.push({
        kind: "ctx", oldNo: String(oldNo++), newNo: String(newNo++),
        code: raw.startsWith(" ") ? raw.slice(1) : raw,
      });
    }
  }
  return files.filter((f) => f.rows.length > 0);
}

/** The most recent event whose payload carries a unified-diff string, parsed per-file. */
function extractDiff(events: EventItem[]): DiffView | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const rec = events[i]?.payload;
    const raw = rec ? asString(rec.diff) : undefined;
    if (!raw || !raw.trim()) continue;
    const files = parseUnifiedByFile(raw);
    if (files.length > 0) return { files };
  }
  return null;
}

/* ---- QA rows, derived from qa/test/review events or steps ---- */

type QaStatus = "pass" | "fail" | "run" | "wait";
interface QaRow {
  label: string;
  sub: string;
  status: QaStatus;
}

function deriveQa(rows: PipelineRow[], events: EventItem[]): QaRow[] {
  const qaEvents = events.filter((e) => /\b(qa|test|verify|acceptance|story|review)\b/i.test(`${e.type} ${e.text}`));
  if (qaEvents.length > 0) {
    return qaEvents.slice(-12).map((e) => ({
      label: e.text,
      sub: `${e.agentRole ? roleLabel(e.agentRole) : "QA"} · ${fmtClock(e.ts) || e.type}`,
      status: /pass|green|✓|ok\b/i.test(e.text)
        ? "pass"
        : /fail|gap|✗|error|leak/i.test(e.text)
          ? "fail"
          : "run",
    }));
  }
  return rows
    .filter((r) => /qa|test|review|security|verify/i.test(`${r.phase} ${r.title}`))
    .map((r) => ({
      label: r.title,
      sub: `${roleLabel(r.agentRole)} · ${phaseLabel(r.phase)}`,
      status:
        r.status === "done" ? "pass" : r.status === "blocked" || r.status === "gated" ? "fail" : r.status === "active" ? "run" : "wait",
    }));
}

/* ============================================================
   Presentational sub-components
   ============================================================ */

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="row gap-8 mb-16"
      style={{
        color: "var(--red)",
        background: "color-mix(in srgb, var(--red) 12%, transparent)",
        border: "1px solid color-mix(in srgb, var(--red) 45%, var(--line))",
        borderRadius: "var(--radius-sm)",
        padding: "10px 14px",
        fontSize: 13,
      }}
    >
      <Icon name="bug" size={15} />
      <span>{message}</span>
    </div>
  );
}

function RoleAvatar({ label, size = "sm" }: { label: string; size?: "sm" | "lg" }) {
  return (
    <span className={cx("avatar", size)} style={{ background: hueFor(label) }} aria-hidden="true">
      {roleAbbr(label)}
    </span>
  );
}

function Fact({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <span className="row gap-6">
      <Icon name={icon} size={13} />
      {children}
    </span>
  );
}

function PipelineTimeline({ rows }: { rows: PipelineRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="empty">
        <div className="em-ic">
          <Icon name="kanban" size={34} />
        </div>
        <div className="fw-6">No steps yet</div>
        <p className="text-sm muted mt-4" style={{ maxWidth: "42ch", margin: "4px auto 0" }}>
          Run the build to generate the pipeline.
        </p>
      </div>
    );
  }
  // Loops mean the same phase (build.api, qa, cto.decision…) can appear several times. Rows
  // stay 1:1 with real steps (keyed by step id, never deduped by phase), so every iteration
  // shows. We just tag the 2nd+ occurrence of a repeated phase with a "cycle N" hint so the
  // loop reads clearly instead of looking like a duplicate.
  const phaseTotals = new Map<string, number>();
  for (const row of rows) phaseTotals.set(row.phase, (phaseTotals.get(row.phase) ?? 0) + 1);
  const phaseSeen = new Map<string, number>();
  return (
    <div className="timeline">
      {rows.map((row) => {
        const cycle = (phaseSeen.get(row.phase) ?? 0) + 1;
        phaseSeen.set(row.phase, cycle);
        const repeats = (phaseTotals.get(row.phase) ?? 0) > 1;
        // Parallel build → show one avatar per role + counts ("Backend ×2 · Frontend ×1"); solo
        // builds and every other phase keep the single-role avatar/subtitle.
        const roles = parseBuildRoles(row.detail);
        return (
          <div className="tl-item" key={row.id}>
            <span className="tl-dot" style={{ background: STEP_COLOR[row.status] }}>
              <Icon name={STEP_ICON[row.status]} size={10} />
            </span>
            <div
              className="card pad"
              style={{
                padding: "12px 14px",
                borderColor: row.status === "active" ? "var(--brand)" : undefined,
                boxShadow: row.status === "active" ? "0 10px 30px -16px rgba(124,92,255,.6)" : undefined,
                opacity: row.status === "done" ? 0.85 : 1,
              }}
            >
              <div className="row between wrap gap-8">
                <div className="row gap-10" style={{ minWidth: 0 }}>
                  {roles ? (
                    <div className="avatar-group">
                      {roles.map((r) => (
                        <RoleAvatar key={r.role} label={r.role} />
                      ))}
                    </div>
                  ) : (
                    <RoleAvatar label={row.agentRole} />
                  )}
                  <div style={{ minWidth: 0 }}>
                    <div className="fw-6 text-sm">{row.title}</div>
                    <div className="li-sub">
                      {roles
                        ? `${roles.map((r) => `${roleLabel(r.role)} ×${r.count}`).join(" · ")} · ${phaseLabel(row.phase)}`
                        : `${roleLabel(row.agentRole)} · ${phaseLabel(row.phase)}`}
                    </div>
                  </div>
                </div>
                <div className="row gap-6">
                  {repeats && <Badge tone="neutral">cycle {cycle}</Badge>}
                  <Badge tone={STEP_TONE[row.status]} dot={row.status === "active"}>
                    {STEP_LABEL[row.status]}
                  </Badge>
                </div>
              </div>
              {row.detail && !roles && (
                <p className="text-sm muted mt-8" style={{ marginBottom: 0 }}>
                  {row.detail}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SpecTab({ mission, rows }: { mission: Mission; rows: PipelineRow[] }) {
  return (
    <div>
      <div className="row gap-8 mb-16 wrap">
        <Badge tone={STAGE_TONE[mission.stage]} dot>
          spec · {mission.stage}
        </Badge>
        <Badge tone="neutral">
          <Icon name="doc" size={12} /> {mission.source}
        </Badge>
      </div>
      <p className="muted mb-20" style={{ lineHeight: 1.6 }}>
        {mission.summary ?? "No summary yet — the intake interview locks the spec once it runs."}
      </p>
      <div className="section-label">Acceptance checks · one per phase</div>
      {rows.length === 0 ? (
        <p className="text-sm muted" style={{ marginBottom: 0 }}>
          No acceptance checks yet — they derive from the pipeline once the build runs.
        </p>
      ) : (
        // The card body owns the single scroll (pinned tabs above), so this list just flows.
        <div className="list">
          {rows.map((row) => (
            <div className="list-item" key={row.id}>
              <span className="dot" style={{ background: STEP_COLOR[row.status] }} />
              <div className="li-main">
                <div className="li-title" style={{ fontSize: 13 }}>
                  {row.title}
                </div>
                <div className="li-sub">{row.phase}</div>
              </div>
              <Badge tone={STEP_TONE[row.status]}>{STEP_LABEL[row.status]}</Badge>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** One file's diff — a collapsible header (path + counts) with its own hunks below (Bitbucket-style,
    so you always know which file you're looking at). */
function DiffFileSection({ file, open, onToggle }: { file: DiffFile; open: boolean; onToggle: () => void }) {
  return (
    <div className="diff-file">
      <button className="diff-file-head" onClick={onToggle} aria-expanded={open}>
        <Icon name="chevron" size={13} className={cx("diff-file-caret", open && "open")} />
        <span className="mono text-sm truncate" style={{ flex: 1, textAlign: "left" }}>{file.path}</span>
        <span className="text-xs" style={{ flex: "0 0 auto" }}>
          <span className="c-green">+{file.add}</span> <span className="c-red">-{file.del}</span>
        </span>
      </button>
      {open && (
        <div className="diff">
          {file.rows.map((r, i) => (
            <div
              key={i}
              className={cx("row", r.kind === "add" && "add", r.kind === "del" && "del", r.kind === "hunk" && "hunk")}
            >
              {r.kind === "hunk" ? (
                <span className="code hunk-code">{r.code}</span>
              ) : (
                <>
                  <span className="gutter-old">{r.oldNo}</span>
                  <span className="gutter-new">{r.newNo}</span>
                  <span className="sign">{r.kind === "add" ? "+" : r.kind === "del" ? "-" : " "}</span>
                  <span className="code">{r.code}</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DiffTab({ diff, mission }: { diff: DiffView | null; mission: Mission }) {
  // Collapse everything by default when there are many files (e.g. a committed venv) so the list
  // stays navigable; auto-expand when it's a small change set.
  const many = (diff?.files.length ?? 0) > 6;
  const [openPaths, setOpenPaths] = useState<ReadonlySet<string>>(
    () => new Set(many ? [] : (diff?.files ?? []).map((f) => f.path)),
  );
  const toggle = (path: string) =>
    setOpenPaths((cur) => {
      const next = new Set(cur);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  if (!diff || diff.files.length === 0) {
    return (
      <div className="empty">
        <div className="em-ic">
          <Icon name="doc" size={34} />
        </div>
        <div className="fw-6">No diff yet</div>
        <p className="text-sm muted mt-4" style={{ maxWidth: "42ch", margin: "4px auto 0" }}>
          Run the build — the code the engineers write streams in here as a diff.
          {mission.branch ? ` Target branch ${mission.branch}.` : ""}
        </p>
      </div>
    );
  }
  const totalAdd = diff.files.reduce((a, f) => a + f.add, 0);
  const totalDel = diff.files.reduce((a, f) => a + f.del, 0);
  const allOpen = openPaths.size === diff.files.length;
  return (
    <div>
      <div className="row between mb-12 wrap gap-8">
        <span className="section-label" style={{ margin: 0 }}>Files changed</span>
        <span className="row gap-10 text-xs faint">
          <button
            className="link-btn"
            onClick={() => setOpenPaths(allOpen ? new Set() : new Set(diff.files.map((f) => f.path)))}
          >
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
          <span>
            {diff.files.length} {diff.files.length === 1 ? "file" : "files"} ·{" "}
            <span className="c-green">+{totalAdd}</span> <span className="c-red">-{totalDel}</span>
          </span>
        </span>
      </div>
      <div className="diff-files">
        {diff.files.map((f) => (
          <DiffFileSection key={f.path} file={f} open={openPaths.has(f.path)} onToggle={() => toggle(f.path)} />
        ))}
      </div>
    </div>
  );
}

const QA_TONE: Record<QaStatus, BadgeTone> = { pass: "green", fail: "amber", run: "brand", wait: "neutral" };
const QA_COLOR: Record<QaStatus, string> = {
  pass: "var(--green)",
  fail: "var(--amber)",
  run: "var(--brand)",
  wait: "var(--faint)",
};
const QA_LABEL: Record<QaStatus, string> = { pass: "PASS", fail: "GAP", run: "RUN", wait: "…" };

function QaTab({ qa }: { qa: QaRow[] }) {
  if (qa.length === 0) {
    return (
      <div className="empty">
        <div className="em-ic">
          <Icon name="flask" size={34} />
        </div>
        <div className="fw-6">No QA yet</div>
        <p className="text-sm muted mt-4" style={{ maxWidth: "42ch", margin: "4px auto 0" }}>
          Once the build reaches QA, acceptance stories run in a real browser and land here.
        </p>
      </div>
    );
  }
  const passed = qa.filter((q) => q.status === "pass").length;
  const gaps = qa.filter((q) => q.status === "fail").length;
  return (
    <div>
      <div className="row gap-8 mb-16 wrap">
        <Badge tone="green">{passed} passed</Badge>
        {gaps > 0 && <Badge tone="amber">{gaps} gap</Badge>}
      </div>
      {qa.map((q, i) => (
        <div
          className="row gap-10"
          key={i}
          style={{ padding: "10px 0", borderBottom: "1px solid var(--line)", alignItems: "flex-start" }}
        >
          <span className="dot" style={{ background: QA_COLOR[q.status], marginTop: 6 }} />
          <div className="li-main">
            <div className="text-sm fw-6">{summarize(q.label)}</div>
            <div className="li-sub">{q.sub}</div>
          </div>
          <Badge tone={QA_TONE[q.status]}>{QA_LABEL[q.status]}</Badge>
        </div>
      ))}
    </div>
  );
}

function TeamCard({ team }: { team: TeamMember[] }) {
  return (
    <div className="card pad">
      <div className="section-label">Team on this mission</div>
      {team.length === 0 ? (
        <p className="text-sm muted" style={{ marginBottom: 0 }}>
          Team appears once the run starts.
        </p>
      ) : (
        team.map((member) => (
          <div className="row gap-10" key={member.agentId} style={{ padding: "7px 0" }}>
            <RoleAvatar label={member.roleKey} />
            <div className="li-main">
              <div className="row gap-6" style={{ alignItems: "center", flexWrap: "wrap" }}>
                <span className="li-title" style={{ fontSize: 13 }}>{member.name}</span>
                <span className="tm-rolechip" style={{ ["--rc" as string]: hueFor(member.roleKey) }}>
                  {roleLabel(member.roleKey)}
                </span>
                {member.level && <span className={`tm-level lvl-${member.level}`}>{levelLabel(member.level)}</span>}
              </div>
              <div className="li-sub">
                {phaseLabel(member.phase)} · {STEP_LABEL[member.status]}
              </div>
            </div>
            <span className="dot" style={{ background: STEP_COLOR[member.status] }} />
          </div>
        ))
      )}
    </div>
  );
}

function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      className="row between"
      style={{ padding: "9px 0", borderBottom: "1px solid var(--line)", fontSize: 13 }}
    >
      <span className="faint">{label}</span>
      <span style={{ minWidth: 0, textAlign: "right" }}>{children}</span>
    </div>
  );
}

/** "Run app": its OWN card in the right rail — launch the built app on a localhost port and open it
 *  in a new tab to test. Prominent (brand-tinted) once the mission has shipped; the server keeps
 *  running until Stop. Shown only for a built web app (project on disk). */
function RunAppCard({ missionKey, shipped }: { missionKey: string; shipped: boolean }) {
  const [pv, setPv] = useState<PreviewStatus | null>(null);  // null = still checking (avoids a flash)
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getPreview(missionKey).then((s) => alive && setPv(s)).catch(() => alive && setPv({ running: false }));
    return () => { alive = false; };
  }, [missionKey]);

  const run = async () => {
    setBusy(true); setErr(null);
    try { setPv(await startPreview(missionKey)); }
    catch (e) { setErr(isApiError(e) ? e.message : "could not start the app"); }
    finally { setBusy(false); }
  };
  const stop = async () => {
    setBusy(true);
    try { await stopPreview(missionKey); setPv({ running: false }); } catch { /* ignore */ }
    finally { setBusy(false); }
  };

  const running = Boolean(pv?.running && pv.url);
  return (
    <div className="card pad" style={shipped && !running ? {
      border: "1px solid color-mix(in srgb, var(--brand) 40%, var(--line))",
      background: "color-mix(in srgb, var(--brand) 5%, var(--panel))",
    } : undefined}>
      <div className="row gap-8" style={{ marginBottom: 10 }}>
        <Icon name="play" size={15} />
        <span className="fw-7">Run the app</span>
      </div>
      {pv === null ? (
        <div className="text-xs faint">Checking…</div>
      ) : running ? (
        <div className="col" style={{ gap: 8 }}>
          <a href={pv!.url} target="_blank" rel="noreferrer"
             aria-label="Open the running app in a new tab" style={{ textDecoration: "none" }}>
            <Button variant="primary" block><Icon name="external" size={15} /> Open app</Button>
          </a>
          <div className="row between">
            <span className="mono text-xs faint truncate" style={{ maxWidth: 150 }}>
              {pv!.url?.replace(/^https?:\/\//, "")}
            </span>
            <Button variant="ghost" size="sm" disabled={busy} onClick={stop}>
              <Icon name="close" size={13} /> {busy ? "Stopping…" : "Stop"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="col" style={{ gap: 6 }}>
          <Button variant="primary" block disabled={busy} onClick={run}>
            <Icon name="play" size={15} /> {busy ? "Starting…" : "Run app"}
          </Button>
          <span className="text-xs faint">
            {busy ? "Booting the server — this can take a moment…"
                  : "Launch it on a localhost port and open it to test."}
          </span>
        </div>
      )}
      {err && <div className="text-xs mt-8" style={{ color: "var(--red)" }}>{err}</div>}
    </div>
  );
}

function DetailsCard({ mission, teamName }: { mission: Mission; teamName: string | null }) {
  return (
    <div className="card pad">
      <div className="section-label">Details</div>
      <MetaRow label="Source">{mission.source}</MetaRow>
      <MetaRow label="Team">{teamName ?? "—"}</MetaRow>
      <MetaRow label="Autonomy">{autonomyLabel(mission.autonomy)}</MetaRow>
      <MetaRow label="Priority">{PRIORITY_LABEL[mission.priority]}</MetaRow>
      {mission.projectPath && (
        <MetaRow label="Project folder">
          <span className="mono text-xs" style={{ wordBreak: "break-all", textAlign: "right", display: "inline-block", maxWidth: 210 }}>
            {mission.projectPath}
          </span>
        </MetaRow>
      )}
      <MetaRow label="Repository">
        {mission.repoName && mission.repoUrl ? (
          <a className="c-brand mono text-xs row gap-4" href={mission.repoUrl} target="_blank"
             rel="noreferrer" style={{ maxWidth: 200, justifyContent: "flex-end" }}>
            <span className="truncate" style={{ display: "inline-block", maxWidth: 176 }}>{mission.repoName}</span>
            <Icon name="external" size={12} />
          </a>
        ) : (
          "—"
        )}
      </MetaRow>
      <MetaRow label="Branch">
        <span className="mono text-xs truncate" style={{ display: "inline-block", maxWidth: 180 }}>
          {mission.branch ?? "—"}
        </span>
      </MetaRow>
      <MetaRow label="Code">
        {mission.codeUrl ? (
          <a className="c-brand mono text-xs row gap-4" href={mission.codeUrl} target="_blank" rel="noreferrer">
            View on GitHub <Icon name="external" size={12} />
          </a>
        ) : (
          "—"
        )}
      </MetaRow>
      <MetaRow label="Pull request">
        {mission.prUrl ? (
          <a className="c-brand mono text-xs row gap-4" href={mission.prUrl} target="_blank" rel="noreferrer">
            open <Icon name="external" size={12} />
          </a>
        ) : (
          "—"
        )}
      </MetaRow>
      <MetaRow label="Updated">{fmtWhen(mission.updatedAt)}</MetaRow>
      <div className="row between wrap gap-6" style={{ padding: "10px 0 0", fontSize: 13 }}>
        <span className="faint">Labels</span>
        <span className="row gap-4 wrap" style={{ justifyContent: "flex-end" }}>
          {mission.labels.length > 0 ? (
            mission.labels.map((l) => (
              <span key={l} className="chip" style={{ padding: "2px 8px", fontSize: 11 }}>
                {l}
              </span>
            ))
          ) : (
            <span className="faint">—</span>
          )}
        </span>
      </div>
    </div>
  );
}

function ApprovalGate({
  mission,
  run,
  gated,
  busy,
  onDecide,
}: {
  mission: Mission;
  run: Run | null;
  gated: boolean;
  busy: boolean;
  onDecide: (decision: "approve" | "reject", opts?: { repo?: string; branch?: string; force?: boolean }) => void;
}) {
  const shipped = mission.stage === "shipped" || run?.status === "succeeded";
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState(mission.branch ?? "");
  const [force, setForce] = useState(false); // force-push is ONLY ever a user choice, here
  const [ghError, setGhError] = useState("");
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [loadingBranches, setLoadingBranches] = useState(false);

  // When the gate opens, load the repos the connected GitHub token can push to (repo picker).
  useEffect(() => {
    if (!gated) return;
    let alive = true;
    setLoadingRepos(true);
    setGhError("");
    listGithubRepos()
      .then((rs) => {
        if (!alive) return;
        setRepos(rs);
        const preferred = rs.find((r) => r.fullName === mission.repoName) ?? rs[0];
        if (preferred) setRepo((cur) => cur || preferred.fullName);
      })
      .catch((e) => { if (alive) setGhError(isApiError(e) ? e.message : "Couldn't load your GitHub repos"); })
      .finally(() => { if (alive) setLoadingRepos(false); });
    return () => { alive = false; };
  }, [gated, mission.repoName]);

  // Load the branches of the chosen repo (branch picker), defaulting to the mission's branch or the
  // repo's default branch.
  useEffect(() => {
    if (!gated || !repo) { setBranches([]); return; }
    let alive = true;
    setLoadingBranches(true);
    listGithubBranches(repo)
      .then((bs) => {
        if (!alive) return;
        setBranches(bs);
        const repoMeta = repos.find((r) => r.fullName === repo);
        const fallback =
          mission.branch && bs.includes(mission.branch) ? mission.branch
          : repoMeta?.defaultBranch && bs.includes(repoMeta.defaultBranch) ? repoMeta.defaultBranch
          : bs[0] ?? "";
        setBranch((cur) => (bs.includes(cur) ? cur : fallback));
      })
      .catch(() => { if (alive) setBranches([]); })
      .finally(() => { if (alive) setLoadingBranches(false); });
    return () => { alive = false; };
  }, [gated, repo, repos, mission.branch]);
  return (
    <div
      className="card pad"
      style={{
        borderColor: "color-mix(in srgb, var(--amber) 40%, var(--line))",
        background: "linear-gradient(180deg, color-mix(in srgb, var(--amber) 8%, transparent), transparent 70%)",
      }}
    >
      <div className="row between mb-8">
        <span className="row gap-8">
          <span className="c-amber">
            <Icon name="shield" size={16} />
          </span>
          <span className="fw-7">Approval gate</span>
        </span>
        {gated && (
          <Badge tone="amber" dot>
            required
          </Badge>
        )}
      </div>
      <p className="text-sm muted" style={{ lineHeight: 1.6 }}>
        Autonomy is <b>{autonomyLabel(mission.autonomy)}</b>. The team builds, tests, and reviews on its
        own — but <b>merge &amp; deploy wait for your sign-off</b>.
      </p>

      {gated ? (
        <div className="col gap-8 mt-16">
          {ghError ? (
            <div className="lb-error text-xs" style={{ lineHeight: 1.5 }}>
              {ghError} — connect GitHub on the{" "}
              <Link href="/dashboard/integrations" className="link">Integrations</Link> page, then reopen
              this gate. You can still approve to merge locally (nothing is pushed).
            </div>
          ) : (
            <>
              <div className="field" style={{ marginBottom: 0 }}>
                <label htmlFor="mg-repo">Repository to push</label>
                <select id="mg-repo" className="select" value={repo}
                        onChange={(e) => setRepo(e.target.value)}
                        disabled={loadingRepos || repos.length === 0}>
                  {loadingRepos && <option value="">Loading your repos…</option>}
                  {!loadingRepos && repos.length === 0 && <option value="">No repos found</option>}
                  {repos.map((r) => (
                    <option key={r.fullName} value={r.fullName}>
                      {r.fullName}{r.private ? " (private)" : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <label htmlFor="mg-branch">Branch</label>
                {/* A combobox: pick an existing branch from the suggestions, OR type a NEW name —
                    DevOps creates it on push (`git push HEAD:<branch>` creates a branch that doesn't
                    exist yet). */}
                <input id="mg-branch" className="input mono" list="mg-branch-list" value={branch}
                       autoComplete="off" spellCheck={false}
                       onChange={(e) => setBranch(e.target.value.trim())}
                       placeholder={loadingBranches ? "Loading branches…" : "main, or a new branch name"} />
                <datalist id="mg-branch-list">
                  {branches.map((b) => <option key={b} value={b} />)}
                </datalist>
              </div>
              {branch && !loadingBranches && !branches.includes(branch) ? (
                <span className="text-xs" style={{ color: "var(--brand)" }}>
                  <Icon name="branch" size={12} /> New branch — DevOps will create{" "}
                  <span className="mono">{branch}</span> and push to it.
                </span>
              ) : (
                <span className="text-xs faint">
                  Pick an existing branch or type a new one (it&apos;ll be created). Pushes the built
                  work &amp; opens a PR, using your connected GitHub token.
                </span>
              )}
              {repo && (
                <label className="row gap-8 text-xs" style={{ alignItems: "flex-start", cursor: "pointer" }}>
                  <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)}
                         style={{ marginTop: 2 }} />
                  <span className="muted" style={{ lineHeight: 1.5 }}>
                    <b className="c-red">Force-push</b> to <span className="mono">{branch || "the branch"}</span> if
                    the push is rejected — <b>overwrites that branch&apos;s remote history</b>. Only for repos
                    you own / throwaway repos. If you don&apos;t force, a rejected push re-opens this gate so
                    you can decide.
                  </span>
                </label>
              )}
            </>
          )}
          <Button variant="primary" block disabled={busy}
                  onClick={() => onDecide("approve", { repo: repo || undefined, branch: branch || undefined, force })}>
            <Icon name="check" size={16} /> {repo ? (force ? "Approve & force-push" : "Approve, push & open PR") : "Approve & merge"}
          </Button>
          <Button variant="danger" block disabled={busy} onClick={() => onDecide("reject")}>
            <Icon name="close" size={16} /> Reject
          </Button>
        </div>
      ) : (
        <div className="mt-16">
          <Button variant="primary" block disabled>
            <Icon name="check" size={16} /> {shipped ? "Merged & shipped" : "Approve & merge"}
          </Button>
          <p className="text-xs faint mt-8" style={{ textAlign: "center" }}>
            {shipped
              ? "This mission has shipped."
              : run?.status === "running"
                ? "The team is still working — the gate opens after review."
                : "Run the build; the gate opens when the team is ready to merge."}
          </p>
        </div>
      )}
    </div>
  );
}

/** The AI's clarifying questions — the run is paused until the user answers. */
function ClarifyCard({
  questions,
  busy,
  onSubmit,
}: {
  questions: string[];
  busy: boolean;
  onSubmit: (answers: ClarifyAnswer[]) => void;
}) {
  const [answers, setAnswers] = useState<string[]>(() => questions.map(() => ""));
  const set = (i: number, v: string) => setAnswers((prev) => prev.map((a, j) => (j === i ? v : a)));
  const anyAnswered = answers.some((a) => a.trim());

  return (
    <div className="card" style={{ borderColor: "color-mix(in srgb, var(--amber) 45%, var(--line))" }}>
      <div className="card-head">
        <h3 className="row" style={{ gap: 8 }}>
          <span className="dot pulse bg-amber" /> A few questions first
        </h3>
        <span className="pill" style={{ color: "var(--amber)", borderColor: "color-mix(in srgb, var(--amber) 40%, var(--line))", background: "rgba(246,196,84,.10)" }}>
          paused
        </span>
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <p className="text-xs faint" style={{ margin: 0 }}>
          The team needs these answered before it builds. Answer what you can — blanks are treated as
          &ldquo;you decide&rdquo;.
        </p>
        {questions.map((q, i) => (
          <div key={q} className="field" style={{ marginBottom: 0 }}>
            <label htmlFor={`clarify-${i}`} className="text-sm fw-6">{q}</label>
            <textarea
              id={`clarify-${i}`} className="textarea" rows={2} value={answers[i] ?? ""}
              onChange={(e) => set(i, e.target.value)} placeholder="Your answer…"
              style={{ minHeight: 56 }}
            />
          </div>
        ))}
        <Button
          variant="primary" block disabled={busy}
          onClick={() => onSubmit(questions.map((q, i) => ({ question: q, answer: (answers[i] ?? "").trim() })))}
        >
          <Icon name="check" size={16} /> {busy ? "Submitting…" : anyAnswered ? "Submit answers & build" : "Skip — you decide, build it"}
        </Button>
      </div>
    </div>
  );
}

/**
 * Request changes — the reopen-after-ship affordance. A mission that has shipped isn't the end
 * of the road: the owner describes what to change and we call `requestChange`, which starts a
 * fresh run that iterates on the existing project. Collapsed to a single button until clicked,
 * then reveals an inline textarea form (same pattern as ClarifyCard above).
 */
function RequestChangesCard({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (request: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const canSubmit = text.trim().length > 0 && !busy;

  return (
    <div
      className="card pad"
      style={{
        borderColor: "color-mix(in srgb, var(--brand) 40%, var(--line))",
        background: "linear-gradient(180deg, color-mix(in srgb, var(--brand) 8%, transparent), transparent 70%)",
      }}
    >
      <div className="row between mb-8">
        <span className="row gap-8">
          <span className="c-brand">
            <Icon name="pen" size={16} />
          </span>
          <span className="fw-7">Request changes</span>
        </span>
        <Badge tone="green" dot>
          shipped
        </Badge>
      </div>
      <p className="text-sm muted" style={{ lineHeight: 1.6 }}>
        This mission shipped — but you can keep iterating. Describe what to change and the team
        <b> reopens the project</b> for another pass.
      </p>

      {open ? (
        <div className="col gap-8 mt-16">
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="chg-req" className="text-sm fw-6">
              What should change?
            </label>
            <textarea
              id="chg-req"
              className="textarea"
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Add a dark theme to the settings screen and fix the login redirect."
              style={{ minHeight: 72 }}
            />
          </div>
          <Button variant="primary" block disabled={!canSubmit} onClick={() => onSubmit(text.trim())}>
            <Icon name="check" size={16} /> {busy ? "Submitting…" : "Submit change request"}
          </Button>
          <Button variant="ghost" block disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="mt-16">
          <Button variant="primary" block disabled={busy} onClick={() => setOpen(true)}>
            <Icon name="pen" size={16} /> Request changes
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * Retry a stopped run — the resume-with-saved-progress affordance. A run that was force-stopped
 * (or failed/gated) isn't the end: the engine resumes from the last completed phase (checkpoint),
 * keeping the requirements, on-disk project, and prior outputs. The intended flow is force-stop →
 * change the model in Models → retry, so the run continues on the new model with saved progress.
 * Same inline-card pattern as `RequestChangesCard` above; `fromStart` maps to the API's redo-all.
 */
function RetryCard({
  busy,
  onSubmit,
  onClose,
}: {
  busy: boolean;
  onSubmit: (fromStart: boolean) => void;
  onClose: () => void;
}) {
  const [fromStart, setFromStart] = useState(false);

  return (
    <div
      className="card pad"
      style={{
        borderColor: "color-mix(in srgb, var(--brand) 40%, var(--line))",
        background: "linear-gradient(180deg, color-mix(in srgb, var(--brand) 8%, transparent), transparent 70%)",
      }}
    >
      <div className="row between mb-8">
        <span className="row gap-8">
          <span className="c-brand">
            <Icon name="play" size={16} />
          </span>
          <span className="fw-7">Retry run</span>
        </span>
        <Badge tone="brand" dot>
          saved progress
        </Badge>
      </div>
      <p className="text-sm muted" style={{ lineHeight: 1.6 }}>
        This <b>resumes from where it stopped</b> — the requirements, the on-disk project, and every
        prior output are kept, so the team picks up at the last completed phase.
      </p>
      <p className="text-xs faint mt-8" style={{ lineHeight: 1.6, marginBottom: 0 }}>
        Tip: change the model in{" "}
        <Link href="/dashboard/models" className="c-brand">
          Models
        </Link>{" "}
        first to retry on a different model.
      </p>

      <label className="row gap-8 mt-16" style={{ cursor: busy ? "not-allowed" : "pointer", fontSize: 13 }}>
        <input
          type="checkbox"
          checked={fromStart}
          disabled={busy}
          onChange={(e) => setFromStart(e.target.checked)}
        />
        <span>
          Start over from the beginning
          <span className="faint" style={{ display: "block", fontSize: 11.5 }}>
            Ignore the checkpoint and redo the whole pipeline.
          </span>
        </span>
      </label>

      <div className="col gap-8 mt-16">
        <Button variant="primary" block disabled={busy} onClick={() => onSubmit(fromStart)}>
          <Icon name="play" size={16} /> {busy ? "Retrying…" : "Retry"}
        </Button>
        <Button asChild variant="subtle" block>
          <Link href="/dashboard/models">
            <Icon name="cpu" size={16} /> Open Models
          </Link>
        </Button>
        <Button variant="ghost" block disabled={busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/**
 * Edit mission — the between-builds editor. A mission can be refined (title, summary,
 * requirements, priority, autonomy, labels) whenever no run is active; the API rejects the edit
 * with 422 while a run is in flight or when the title is emptied. Same inline-card pattern as
 * `RequestChangesCard`/`RetryCard` above. On Save we diff the form against the loaded mission and
 * hand the parent ONLY the changed fields, so an untouched field is never overwritten.
 */
function EditMissionCard({
  mission,
  busy,
  onSubmit,
  onClose,
}: {
  mission: Mission;
  busy: boolean;
  onSubmit: (changes: UpdateMissionInput) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(mission.title);
  const [summary, setSummary] = useState(mission.summary ?? "");
  const [requirements, setRequirements] = useState(mission.requirements ?? "");
  // Priority is edited (and sent) as the API's "P0".."P3" form; the mission stores it lowercase.
  const [priority, setPriority] = useState(PRIORITY_LABEL[mission.priority]);
  const [autonomy, setAutonomy] = useState(mission.autonomy);
  const [labels, setLabels] = useState(mission.labels.join(", "));

  // The comma-separated labels field → a clean string[] (trimmed, blanks and dupes removed).
  const parseLabels = (raw: string): string[] => {
    const seen = new Set<string>();
    for (const part of raw.split(",")) {
      const t = part.trim();
      if (t) seen.add(t);
    }
    return [...seen];
  };

  const canSave = title.trim().length > 0 && !busy;

  const save = () => {
    // Build a minimal patch: include a field only when its value actually changed. This keeps
    // untouched fields untouched and never sends an empty title (the API 422s on that).
    const changes: UpdateMissionInput = {};
    const nextTitle = title.trim();
    if (nextTitle !== mission.title) changes.title = nextTitle;
    if (summary !== (mission.summary ?? "")) changes.summary = summary;
    if (requirements !== (mission.requirements ?? "")) changes.requirements = requirements;
    if (priority !== PRIORITY_LABEL[mission.priority]) changes.priority = priority;
    if (autonomy !== mission.autonomy) changes.autonomy = autonomy;
    const nextLabels = parseLabels(labels);
    if (nextLabels.join(" ") !== mission.labels.join(" ")) changes.labels = nextLabels;
    onSubmit(changes);
  };

  return (
    <div
      className="card pad"
      style={{
        borderColor: "color-mix(in srgb, var(--brand) 40%, var(--line))",
        background: "linear-gradient(180deg, color-mix(in srgb, var(--brand) 8%, transparent), transparent 70%)",
      }}
    >
      <div className="row between mb-8">
        <span className="row gap-8">
          <span className="c-brand">
            <Icon name="pen" size={16} />
          </span>
          <span className="fw-7">Edit mission</span>
        </span>
        <Badge tone="neutral">between builds</Badge>
      </div>
      <p className="text-sm muted" style={{ lineHeight: 1.6 }}>
        Refine this mission before the next build. Only the fields you change are saved.
      </p>

      <div className="col gap-8 mt-16">
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="edit-title" className="text-sm fw-6">Title</label>
          <input
            id="edit-title" className="input" value={title}
            onChange={(e) => setTitle(e.target.value)} placeholder="Mission title"
          />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="edit-summary" className="text-sm fw-6">Summary</label>
          <input
            id="edit-summary" className="input" value={summary}
            onChange={(e) => setSummary(e.target.value)} placeholder="One-line summary"
          />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="edit-req" className="text-sm fw-6">Requirements</label>
          <textarea
            id="edit-req" className="textarea" rows={4} value={requirements}
            onChange={(e) => setRequirements(e.target.value)}
            placeholder="Full brief the team builds against…" style={{ minHeight: 96 }}
          />
        </div>
        <div className="row gap-8 wrap">
          <div className="field" style={{ marginBottom: 0, flex: "1 1 120px" }}>
            <label htmlFor="edit-priority" className="text-sm fw-6">Priority</label>
            <select
              id="edit-priority" className="select" value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              {(["P0", "P1", "P2", "P3"] as const).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, flex: "1 1 140px" }}>
            <label htmlFor="edit-autonomy" className="text-sm fw-6">Autonomy</label>
            <select
              id="edit-autonomy" className="select" value={autonomy}
              onChange={(e) => setAutonomy(e.target.value)}
            >
              {AUTONOMY_LEVELS.map((l) => (
                <option key={l.key} value={l.key}>{l.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="edit-labels" className="text-sm fw-6">
            Labels <span className="faint">(comma-separated)</span>
          </label>
          <input
            id="edit-labels" className="input" value={labels}
            onChange={(e) => setLabels(e.target.value)} placeholder="auth, dark-mode, api"
          />
        </div>
        <Button variant="primary" block disabled={!canSave} onClick={save}>
          <Icon name="check" size={16} /> {busy ? "Saving…" : "Save changes"}
        </Button>
        <Button variant="ghost" block disabled={busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function TeamConsole({
  events,
  run,
  consoleRef,
  onScroll,
  atBottom,
  onJumpToLatest,
  tall,
  onToggleTall,
}: {
  events: EventItem[];
  run: Run | null;
  consoleRef: RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  atBottom: boolean;
  onJumpToLatest: () => void;
  tall: boolean;
  onToggleTall: () => void;
}) {
  return (
    <div className="card">
      <div className="card-head">
        <h3>
          <span className="dot pulse bg-green" /> Team console
        </h3>
        <div className="row gap-8">
          <button
            className="btn ghost sm"
            onClick={onToggleTall}
            data-tip={tall ? "Collapse the console" : "Expand for more logs"}
          >
            <Icon name={tall ? "close" : "external"} size={14} /> {tall ? "Collapse" : "Expand"}
          </button>
          {run ? (
            <a
              className="btn ghost sm"
              href={runExportUrl(run.id)}
              target="_blank"
              rel="noopener noreferrer"
              data-tip="Open the full run log (JSON) in a new tab"
            >
              <Icon name="doc" size={14} /> Full log
            </a>
          ) : (
            <span className="text-xs faint">live agent handoffs</span>
          )}
        </div>
      </div>
      <div className="card-body" style={{ position: "relative" }}>
        <div
          className={cx("log", "mv-console", tall && "mv-console-tall")}
          ref={consoleRef}
          onScroll={onScroll}
        >
          {events.length === 0 ? (
            <div className="faint">No activity yet — run the build to watch the team work.</div>
          ) : (
            events.map((e) => {
              const rich = (e.text?.length ?? 0) > 120 || (e.text?.includes("\n") ?? false);
              const kind = String((e.payload as { kind?: unknown })?.kind ?? "");
              // Routing/loop events (route.*) are the decision-graph's movements — make them pop.
              const route = routeChip(kind);
              const isErr = e.type === "error" || /error|fail|reject/i.test(`${e.type} ${kind}`);
              return (
                <div
                  className={`log-line${rich ? " rich" : ""}${isErr ? " err" : ""}`}
                  key={e.id}
                  // A subtle left border + tint (tokens only) sets a stage transition apart
                  // from ordinary log lines without hiding the event's own text.
                  style={
                    route
                      ? {
                          borderLeft: `2px solid ${route.color}`,
                          background: `color-mix(in srgb, ${route.color} 8%, transparent)`,
                          paddingLeft: 8,
                          borderRadius: "var(--radius-sm)",
                        }
                      : undefined
                  }
                >
                  <div className="row gap-8" style={{ minWidth: 0, alignItems: "baseline" }}>
                    <span className="ts">{fmtClock(e.ts) || "··:··"}</span>
                    <span className="who" style={{ color: isErr ? "var(--red)" : hueFor(e.agentRole ?? e.type) }}>
                      {e.agentRole ? roleLabel(e.agentRole) : "system"}
                    </span>
                    <span className={isErr ? "who" : "faint"} style={isErr ? { color: "var(--red)" } : undefined}>
                      {isErr ? "error" : e.type}
                    </span>
                    {route && (
                      <Badge tone={route.tone} dot>
                        {route.label}
                      </Badge>
                    )}
                    {!rich && (
                      <span style={{ minWidth: 0, flex: 1, wordBreak: "break-word", color: isErr ? "var(--red)" : undefined }}>
                        {e.text}
                      </span>
                    )}
                  </div>
                  {rich && <Markdown text={e.text} className="md-log" />}
                </div>
              );
            })
          )}
          {run && !isTerminal(run.status) && run.status !== "blocked" && (
            <div className="row gap-8" style={{ padding: "8px 2px", color: "var(--green)", opacity: 0.9 }}>
              <span className="dot pulse bg-green" />
              <span className="mono" style={{ fontSize: 12 }}>
                the team is working… (a local model can take ~30–60s per step — hang tight)
              </span>
            </div>
          )}
        </div>
        {/* When the user has scrolled up, auto-scroll pauses — offer a one-click jump back to live. */}
        {!atBottom && events.length > 0 && (
          <button
            className="btn sm"
            onClick={onJumpToLatest}
            style={{
              position: "absolute", right: 18, bottom: run ? 58 : 18, zIndex: 2,
              background: "var(--brand)", color: "#fff", boxShadow: "0 6px 18px -6px rgba(0,0,0,.5)",
            }}
          >
            <Icon name="arrow" size={14} /> Jump to latest
          </button>
        )}
        {run && (() => {
          // Metering reflects the model the team is ACTUALLY using, not one recorded label. We
          // aggregate per-model usage from the run's `phase.output` events and surface the model
          // with the most tokens (when several are in play). Tokens are exact (real usage); cost is
          // best-effort (some providers are priced from a default table), so it's shown as approx.
          const perModel = new Map<string, { tin: number; tout: number; cents: number }>();
          for (const e of events) {
            const p = e.payload as Record<string, unknown> | undefined;
            if (!p || p.kind !== "phase.output" || typeof p.model !== "string" || !p.model) continue;
            const u = perModel.get(p.model) ?? { tin: 0, tout: 0, cents: 0 };
            u.tin += Number(p.tokensIn ?? 0) || 0;
            u.tout += Number(p.tokensOut ?? 0) || 0;
            u.cents += Number(p.costCents ?? 0) || 0;
            perModel.set(p.model, u);
          }
          let pick = "", best = -1;
          for (const [m, u] of perModel) { const t = u.tin + u.tout; if (t > best) { best = t; pick = m; } }
          const u = pick ? perModel.get(pick)! : null;
          const model = pick || run.model || run.provider || "";
          const tin = u ? u.tin : run.tokensIn;
          const tout = u ? u.tout : run.tokensOut;
          const cents = u ? u.cents : run.costCents;
          const multi = perModel.size > 1;
          return (
            <div className="row gap-16 mt-12 wrap text-xs faint mono" style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
              {!model ? (
                <span>metering appears after the first model call…</span>
              ) : (
                <>
                  <span>{multi ? "top model" : "model"} {model}{multi ? ` · +${perModel.size - 1}` : ""}</span>
                  <span>tokens in {tin.toLocaleString()}</span>
                  <span>tokens out {tout.toLocaleString()}</span>
                  <span>cost ~${(cents / 100).toFixed(2)}</span>
                </>
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

/* ============================================================
   Screen
   ============================================================ */

const SCOPED_CSS = `
.mvlb .mv-grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
@media(max-width:980px){.mvlb .mv-grid{grid-template-columns:1fr}}
.mvlb .mv-console{height:300px;scroll-behavior:smooth}
.mvlb .mv-console-tall{height:72vh}
/* Tab card = a PINNED tab header (mv-tabbar, never scrolls) above a fixed-height scroll region
   (mv-tabbody). So the Pipeline/Code/QA content keeps its scrollbar, but the scrollbar lives in the
   content BELOW the tabs — never on the tab-header row. */
.mvlb .mv-tabcard{display:flex;flex-direction:column;max-height:min(680px,78vh)}
.mvlb .mv-tabbar{flex:0 0 auto}
.mvlb .mv-tabbody{flex:1 1 auto;min-height:0;overflow-y:auto}
/* Team-on-mission chips: role (coloured by role via --rc) + level, so both are clearly visible. */
.mvlb .gap-6{gap:6px}
.mvlb .tm-rolechip{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px;
  color:var(--rc);background:color-mix(in srgb, var(--rc) 14%, transparent);
  border:1px solid color-mix(in srgb, var(--rc) 32%, transparent);white-space:nowrap;line-height:1.4}
.mvlb .tm-level{font-size:10px;font-weight:800;padding:2px 7px;border-radius:999px;letter-spacing:.04em;
  text-transform:uppercase;white-space:nowrap;line-height:1.3;border:1px solid transparent}
.mvlb .lvl-junior{color:var(--muted);background:color-mix(in srgb, var(--muted) 14%, transparent);
  border-color:color-mix(in srgb, var(--muted) 26%, transparent)}
.mvlb .lvl-senior{color:var(--blue);background:color-mix(in srgb, var(--blue) 15%, transparent);
  border-color:color-mix(in srgb, var(--blue) 32%, transparent)}
.mvlb .lvl-principal{color:var(--brand);background:color-mix(in srgb, var(--brand) 15%, transparent);
  border-color:color-mix(in srgb, var(--brand) 34%, transparent)}
.mvlb .lvl-strategic{color:var(--amber);background:color-mix(in srgb, var(--amber) 17%, transparent);
  border-color:color-mix(in srgb, var(--amber) 36%, transparent)}
`;

export function LiveBuild({ missionKey }: { missionKey: string }) {
  const [mission, setMission] = useState<Mission | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  // Events + steps across ALL of the mission's runs — so Pipeline shows the COMPLETE history, Team
  // includes every role that ever worked, and Code/QA fall back to the latest build even when the
  // current run RESUMED past those phases (e.g. a retry from the ship checkpoint).
  const [missionEvents, setMissionEvents] = useState<EventItem[]>([]);
  const [missionSteps, setMissionSteps] = useState<Step[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<TabKey>("pipeline");
  const [hasOpenGate, setHasOpenGate] = useState(false); // an unresolved approval blocker exists
  const [clarify, setClarify] = useState<{ blockerId: string; questions: string[] } | null>(null);
  const [retryOpen, setRetryOpen] = useState(false); // the inline "Retry run" card is expanded
  const [editOpen, setEditOpen] = useState(false);   // the inline "Edit mission" card is expanded
  const [atBottom, setAtBottom] = useState(true);    // console pinned to the newest line?
  const [consoleTall, setConsoleTall] = useState(false); // expanded (taller) console

  const confirm = useConfirm();
  const router = useRouter();

  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true); // only auto-scroll when the user is already at the bottom

  // Track whether the user has scrolled up; if so, STOP auto-scrolling so they can read history.
  const onConsoleScroll = useCallback(() => {
    const el = consoleRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    stickRef.current = near;
    setAtBottom(near);
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = consoleRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight });
    stickRef.current = true;
    setAtBottom(true);
  }, []);

  const stopStreaming = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const refresh = useCallback(
    async (runId: string) => {
      // The poll fallback must refresh events too, or the console freezes when SSE drops.
      // We also refresh the mission (live fields: project folder, branch, PR url) AND the blockers,
      // so a clarify question or a merge gate raised mid-run appears WITHOUT a page reload, and the
      // approve button enables the moment the gate opens (not only once run.status catches up).
      const [r, s, ev, m, blockers] = await Promise.all([
        getRun(runId),
        listSteps(runId),
        listRunEvents(runId),
        getMission(missionKey).catch(() => null),
        listBlockers().catch(() => [] as Awaited<ReturnType<typeof listBlockers>>),
      ]);
      setRun(r);
      setSteps(s);
      setEvents(ev);
      if (m) setMission(m);
      const mid = m?.id;
      if (mid) {
        setHasOpenGate(blockers.some((b) => b.missionId === mid && b.kind === "approval" && !b.resolvedAt));
        const q = blockers.find((b) => b.missionId === mid && b.kind === "question" && !b.resolvedAt);
        if (q) {
          let questions: string[] = [];
          try { questions = (JSON.parse(q.detail) as { questions?: string[] }).questions ?? []; } catch { /* keep [] */ }
          // Keep the SAME object while the blocker is unchanged so the answer form doesn't lose typed text.
          setClarify((prev) => (prev && prev.blockerId === q.id ? prev : { blockerId: q.id, questions }));
        } else {
          setClarify(null);
        }
      }
      if (isTerminal(r.status)) stopStreaming();
    },
    [stopStreaming, missionKey],
  );

  const beginStreaming = useCallback(
    (runId: string) => {
      stopStreaming();
      const es = new EventSource(runStreamUrl(runId));
      es.onmessage = (m) => {
        try {
          const ev = JSON.parse(m.data) as EventItem;
          setEvents((prev) => [...prev, ev]);
          void refresh(runId);
        } catch {
          /* ignore keep-alive comments */
        }
      };
      es.onerror = () => void refresh(runId); // transport hiccup — polling covers it
      esRef.current = es;
      pollRef.current = setInterval(() => void refresh(runId), 1500);
      void refresh(runId);
    },
    [refresh, stopStreaming],
  );

  // Reload on mission change: reset stale state, then load the latest run + its history so
  // revisiting (or a server restart) shows the real console/pipeline/open-gate — not a blank page.
  useEffect(() => {
    setMission(null);
    setRun(null);
    setSteps([]);
    setEvents([]);
    setError(null);
    setHasOpenGate(false);
    setClarify(null);
    stopStreaming();
    let active = true;
    (async () => {
      try {
        const m = await getMission(missionKey);
        if (!active) return;
        setMission(m);
        const [runs, blockers] = await Promise.all([
          listMissionRuns(missionKey).catch(() => []),
          listBlockers().catch(() => []),
        ]);
        if (!active) return;
        setHasOpenGate(
          blockers.some((b) => b.missionId === m.id && b.kind === "approval" && !b.resolvedAt),
        );
        const q = blockers.find((b) => b.missionId === m.id && b.kind === "question" && !b.resolvedAt);
        if (q) {
          let questions: string[] = [];
          try {
            questions = (JSON.parse(q.detail) as { questions?: string[] }).questions ?? [];
          } catch {
            /* malformed detail — show no questions rather than crash */
          }
          setClarify({ blockerId: q.id, questions });
        }
        const latest = runs[runs.length - 1];
        if (latest) {
          const [s, ev] = await Promise.all([listSteps(latest.id), listRunEvents(latest.id)]);
          if (!active) return;
          setRun(latest);
          setSteps(s);
          setEvents(ev);
          // Poll while BLOCKED too (not just running): keeps a clarify question / merge gate live
          // and the approve button enabled without a manual page refresh.
          if (!isTerminal(latest.status)) beginStreaming(latest.id);
        }
      } catch (e) {
        if (active) setError(isApiError(e) ? e.message : "Failed to load mission");
      }
    })();
    return () => {
      active = false;
    };
  }, [missionKey, stopStreaming, beginStreaming]);

  useEffect(() => () => stopStreaming(), [stopStreaming]);

  // The mission's owning team, resolved from the real teams list (fetched once). Failure just
  // leaves the Details "Team" row showing "—" rather than breaking the build view.
  useEffect(() => {
    let active = true;
    Promise.all([listTeams().catch(() => [] as Team[]), listAgents().catch(() => [] as Agent[])])
      .then(([t, a]) => { if (active) { setTeams(t); setAgents(a); } });
    return () => { active = false; };
  }, []);

  const onRun = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await startRun(missionKey);
      setRun(r);
      setEvents([]);
      beginStreaming(r.id);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to start run");
    } finally {
      setBusy(false);
    }
  }, [missionKey, beginStreaming]);

  const onDecide = useCallback(
    async (decision: "approve" | "reject", opts?: { repo?: string; branch?: string; force?: boolean }) => {
      setBusy(true);
      try {
        await decideGate(missionKey, decision, opts ?? {});
        setHasOpenGate(false);
        // Reload the mission + latest run so the gate closes and the shipped/failed state shows,
        // even when the run was finalized server-side (restart-recovery path).
        const [m, runs] = await Promise.all([
          getMission(missionKey).catch(() => null),
          listMissionRuns(missionKey).catch(() => []),
        ]);
        if (m) setMission(m);
        const latest = runs[runs.length - 1];
        if (latest) {
          setRun(latest);
          const [s, ev] = await Promise.all([listSteps(latest.id), listRunEvents(latest.id)]);
          setSteps(s);
          setEvents(ev);
        }
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to resolve the gate");
      } finally {
        setBusy(false);
      }
    },
    [missionKey],
  );

  const onAnswer = useCallback(
    async (answers: ClarifyAnswer[]) => {
      setBusy(true);
      try {
        await submitClarification(missionKey, answers);
        setClarify(null);
        // The run resumes server-side — reload the latest run and reconnect the stream.
        const runs = await listMissionRuns(missionKey).catch(() => []);
        const latest = runs[runs.length - 1];
        if (latest) {
          setRun(latest);
          const [s, ev] = await Promise.all([listSteps(latest.id), listRunEvents(latest.id)]);
          setSteps(s);
          setEvents(ev);
          if (!isTerminal(latest.status)) beginStreaming(latest.id);
        }
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to submit answers");
      } finally {
        setBusy(false);
      }
    },
    [missionKey, beginStreaming],
  );

  const onRequestChange = useCallback(
    async (request: string) => {
      setError(null);
      setBusy(true);
      try {
        // Reopen the shipped mission — the engine starts a fresh run that edits the existing
        // project. From here it streams exactly like a normal build (mirrors onRun); the
        // stream's refresh() reloads the mission so the stage flips off "shipped".
        const r = await requestChange(missionKey, request);
        setRun(r);
        setEvents([]);
        beginStreaming(r.id);
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to submit change request");
      } finally {
        setBusy(false);
      }
    },
    [missionKey, beginStreaming],
  );

  const onCancelRun = useCallback(async () => {
    const ok = await confirm({
      title: "Force-stop this run?",
      message:
        "The run stops immediately, but its progress and context are saved — the requirements, the " +
        "on-disk project, and every prior output are kept, so you can Retry to pick up where it left " +
        "off. Tip: change the model in Models first to retry on a different one.",
      confirmLabel: "Force stop",
      tone: "danger",
      icon: "close",
    });
    if (!ok) return;
    setError(null);
    setBusy(true);
    try {
      await cancelRun(missionKey);
      // Reload the mission + latest run so the stopped state shows and the stream detaches — the
      // same reload path onDecide uses; a stopped (terminal) run reconnects to nothing, so we only
      // begin streaming again if the reloaded run is somehow still live.
      const [m, runs] = await Promise.all([
        getMission(missionKey).catch(() => null),
        listMissionRuns(missionKey).catch(() => []),
      ]);
      if (m) setMission(m);
      const latest = runs[runs.length - 1];
      if (latest) {
        setRun(latest);
        const [s, ev] = await Promise.all([listSteps(latest.id), listRunEvents(latest.id)]);
        setSteps(s);
        setEvents(ev);
        if (!isTerminal(latest.status) && latest.status !== "blocked") beginStreaming(latest.id);
        else stopStreaming();
      } else {
        stopStreaming();
      }
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to stop the run");
    } finally {
      setBusy(false);
    }
  }, [confirm, missionKey, beginStreaming, stopStreaming]);

  const onRetry = useCallback(
    async (fromStart: boolean) => {
      setError(null);
      setBusy(true);
      try {
        // Resume the stopped run — the engine continues from the last completed phase (checkpoint),
        // preserving context, unless fromStart redoes the whole pipeline. Returns a Run that streams
        // exactly like a normal build (mirrors onRun); refresh() repopulates the console history.
        const r = await retryRun(missionKey, fromStart);
        setRun(r);
        setEvents([]);
        setRetryOpen(false);
        beginStreaming(r.id);
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to retry the run");
      } finally {
        setBusy(false);
      }
    },
    [missionKey, beginStreaming],
  );

  const onSaveEdit = useCallback(
    async (changes: UpdateMissionInput) => {
      // Nothing was actually edited — just close the editor without a needless round-trip.
      if (Object.keys(changes).length === 0) {
        setEditOpen(false);
        return;
      }
      setError(null);
      setBusy(true);
      try {
        // Send only the changed fields; the API returns the updated Mission (with lastRunStatus).
        // It 422s if a run is active or the title is empty — surface that message as-is.
        const updated = await updateMission(missionKey, changes);
        setMission(updated);
        setEditOpen(false);
      } catch (e) {
        setError(isApiError(e) ? e.message : "Failed to save the mission");
      } finally {
        setBusy(false);
      }
    },
    [missionKey],
  );

  const onDelete = useCallback(async () => {
    const ok = await confirm({
      title: "Delete this mission?",
      message: "This removes the mission and all its runs/logs. This can't be undone.",
      confirmLabel: "Delete mission",
      tone: "danger",
      icon: "close",
    });
    if (!ok) return;
    setError(null);
    setBusy(true);
    try {
      // On success the mission no longer exists — leave `busy` set and navigate to the board so the
      // page can't be re-used against a deleted mission. The API 422s if a run is still active.
      await deleteMission(missionKey);
      stopStreaming();
      router.push("/dashboard/missions");
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to delete the mission");
      setBusy(false);
    }
  }, [confirm, missionKey, router, stopStreaming]);

  useEffect(() => {
    // Auto-scroll to the newest line ONLY when the user is already at the bottom — otherwise leave
    // their scroll position alone so they can read earlier logs.
    if (stickRef.current) consoleRef.current?.scrollTo({ top: consoleRef.current.scrollHeight });
  }, [events]);

  // Load events + steps across ALL the mission's runs (refreshed when the run changes or finishes,
  // not on every streamed event) so the Pipeline/Team/Spec/Code/QA reflect the whole project, not
  // just the current (possibly resumed) run. Runs are ordered oldest→newest for a chronological
  // history.
  useEffect(() => {
    let active = true;
    (async () => {
      const runs = await listMissionRuns(missionKey).catch(() => []);
      const [evPerRun, stepPerRun] = await Promise.all([
        Promise.all(runs.map((r) => listRunEvents(r.id).catch(() => [] as EventItem[]))),
        Promise.all(runs.map((r) => listSteps(r.id).catch(() => [] as Step[]))),
      ]);
      if (active) {
        setMissionEvents(evPerRun.flat());
        setMissionSteps(stepPerRun.flat());
      }
    })();
    return () => {
      active = false;
    };
  }, [missionKey, run?.id, run?.status]);

  // The complete step history (all runs, chronological) with the CURRENT run's live steps overlaid
  // so their status stays fresh. Drives the Pipeline timeline, the Team rail, and the Spec checks.
  const allSteps = useMemo(() => {
    const live = new Map(steps.map((s) => [s.id, s]));
    const seen = new Set<string>();
    const out: Step[] = [];
    for (const s of missionSteps) {
      const use = live.get(s.id) ?? s;
      if (!seen.has(use.id)) {
        seen.add(use.id);
        out.push(use);
      }
    }
    for (const s of steps) {
      if (!seen.has(s.id)) {
        seen.add(s.id);
        out.push(s);
      }
    }
    return out;
  }, [missionSteps, steps]);

  const rows = useMemo(() => toPipelineRows(allSteps), [allSteps]);
  const team = useMemo(() => {
    const liveRows = toPipelineRows(steps); // current run only → live status
    const isShipped = mission?.stage === "shipped" || run?.status === "succeeded";
    const teamMembers = (mission?.teamId ? teams.find((t) => t.id === mission.teamId)?.members : undefined) ?? [];
    return buildTeam(agents, teamMembers, rows, liveRows, [...missionEvents, ...events], isShipped);
  }, [agents, teams, mission?.teamId, rows, steps, mission?.stage, run?.status, missionEvents, events]);
  // Derive Code (diff) + QA from the current run's live events UNIONED with the mission's history
  // (deduped by id, current-run events win as freshest). So a resumed run that skipped build/QA
  // still shows the most recent build's diff + QA instead of an empty tab.
  const derivationEvents = useMemo(() => {
    const seen = new Set<string>();
    const out: EventItem[] = [];
    for (const e of [...missionEvents, ...events]) {
      if (!seen.has(e.id)) {
        seen.add(e.id);
        out.push(e);
      }
    }
    return out;
  }, [missionEvents, events]);
  const diff = useMemo(() => extractDiff(derivationEvents), [derivationEvents]);
  const qa = useMemo(() => deriveQa(rows, derivationEvents), [rows, derivationEvents]);
  // Name of the Team staffing this mission (from the real teams list) — for the Details rail.
  const teamName = useMemo(() => {
    const id = mission?.teamId;
    return id ? (teams.find((t) => t.id === id)?.name ?? null) : null;
  }, [teams, mission?.teamId]);

  const gated = run?.status === "blocked" || hasOpenGate;
  // A run is "active" while it is running or suspended on a gate — that's when Force-stop applies.
  const runActive = run?.status === "running" || run?.status === "blocked";
  // Retry applies to a stopped/terminal run (failed | cancelled) or a gated run the user wants to
  // redo — it resumes from the saved checkpoint, so it's offered whenever there's progress to keep.
  const canRetry =
    run != null && (run.status === "failed" || run.status === "cancelled" || run.status === "blocked");
  // Shipped missions can be reopened with a change request — surfaces the RequestChangesCard.
  const shipped = mission?.stage === "shipped" || run?.status === "succeeded";
  // Progress comes from the mission (the engine's authoritative %); phase counts span the whole
  // history so they match the complete Pipeline.
  const doneSteps = allSteps.filter((s) => s.status === "done").length;
  const progressPct = mission?.progress ?? 0;

  if (!mission) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Live Build</h1>
            <p className="page-desc">Loading mission…</p>
          </div>
        </div>
        {error ? <ErrorBanner message={error} /> : <div className="muted">Loading…</div>}
      </div>
    );
  }

  return (
    <div className="mvlb">
      <style>{SCOPED_CSS}</style>

      <div className="page-header" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row gap-8 mb-8 wrap">
            <span className="mono text-xs faint">{mission.key}</span>
            <Badge tone="neutral">
              <Icon name="external" size={12} /> {mission.source}
            </Badge>
            {mission.isBlocked && (
              <Badge tone="red" dot>
                blocked
              </Badge>
            )}
          </div>
          <h1 className="page-title" style={{ maxWidth: "52ch" }}>
            {mission.title}
          </h1>
          {mission.summary && (
            <p className="page-desc">{mission.summary}</p>
          )}
          <div className="row gap-8 mt-12 wrap">
            <Badge tone={PRIORITY_TONE[mission.priority]}>{PRIORITY_LABEL[mission.priority]}</Badge>
            <Badge tone={STAGE_TONE[mission.stage]} dot>
              {mission.stage}
            </Badge>
            <Badge tone="brand">
              <Icon name="shield" size={12} /> {autonomyLabel(mission.autonomy)}
            </Badge>
            <Badge tone="neutral">
              <Icon name="branch" size={12} /> {mission.branch ?? "no branch yet"}
            </Badge>
            {run && (
              <Badge tone={RUN_TONE[run.status] ?? "neutral"} dot>
                run: {run.status}
              </Badge>
            )}
          </div>
        </div>
        <div className="page-actions">
          {/* Edit + Delete are only offered between builds — the API rejects them while a run is
              active, so we hide them whenever the run is running or waiting at a gate. */}
          {!runActive && (
            <>
              <Button
                variant="subtle"
                onClick={() => setEditOpen((v) => !v)}
                disabled={busy}
                aria-expanded={editOpen}
              >
                <Icon name="pen" size={16} /> Edit mission
              </Button>
              <Button variant="danger" onClick={onDelete} disabled={busy}>
                <Icon name="close" size={16} /> Delete
              </Button>
            </>
          )}
          {runActive && (
            <Button variant="danger" onClick={onCancelRun} disabled={busy}>
              <Icon name="close" size={16} /> Force stop
            </Button>
          )}
          {canRetry && (
            <Button
              variant="subtle"
              onClick={() => setRetryOpen((v) => !v)}
              disabled={busy}
              aria-expanded={retryOpen}
            >
              <Icon name="play" size={16} /> Retry
            </Button>
          )}
          <Button
            variant="primary"
            onClick={onRun}
            disabled={busy || run?.status === "running" || run?.status === "blocked"}
          >
            <Icon name="play" size={16} /> {run ? "Re-run build" : "Run build"}
          </Button>
        </div>
      </div>

      {error && <ErrorBanner message={error} />}

      <div className="card pad glow mb-16">
        <div className="row between mb-8">
          <span className="section-label" style={{ margin: 0 }}>
            Progress
          </span>
          <span className="mono text-sm">{progressPct}%</span>
        </div>
        <div className="progress">
          <div className="bar" style={{ width: `${progressPct}%` }} />
        </div>
        <div className="row gap-20 mt-16 wrap text-xs faint">
          <Fact icon="users">
            {team.length > 0 ? `${team.length} agents assigned` : "team forms at run start"}
          </Fact>
          <Fact icon="clock">{run ? `started ${fmtWhen(run.startedAt)}` : "not started"}</Fact>
          <Fact icon="kanban">
            {allSteps.length > 0 ? `${doneSteps}/${allSteps.length} phases done` : "no phases yet"}
          </Fact>
          <Fact icon="flask">{qa.length > 0 ? `${qa.length} QA checks` : "QA pending"}</Fact>
        </div>
      </div>

      <div className="mv-grid">
        <div className="col gap-16">
          {/* Pinned tab header (mv-tabbar) + a fixed-height scroll region (mv-tabbody) below it.
              The header never scrolls, so no scrollbar sits on the tabs; the Pipeline/Code/QA
              content keeps its own scrollbar, in the content area under the tabs. */}
          <div className="card mv-tabcard">
            <div className="tabs mv-tabbar" style={{ margin: 0, padding: "6px 16px 0", borderBottom: "1px solid var(--line)" }}>
              {TABS.map((t) => (
                <button
                  key={t.key}
                  className={cx("tab", tab === t.key && "active")}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="card-body mv-tabbody">
              {tab === "pipeline" && <PipelineTimeline rows={rows} />}
              {tab === "spec" && <SpecTab mission={mission} rows={rows} />}
              {tab === "diff" && <DiffTab diff={diff} mission={mission} />}
              {tab === "qa" && <QaTab qa={qa} />}
            </div>
          </div>

          <TeamConsole
            events={events}
            run={run}
            consoleRef={consoleRef}
            onScroll={onConsoleScroll}
            atBottom={atBottom}
            onJumpToLatest={jumpToLatest}
            tall={consoleTall}
            onToggleTall={() => setConsoleTall((v) => !v)}
          />
        </div>

        <div className="col gap-16">
          {clarify && <ClarifyCard questions={clarify.questions} busy={busy} onSubmit={onAnswer} />}
          {editOpen && !runActive && (
            <EditMissionCard
              mission={mission}
              busy={busy}
              onSubmit={onSaveEdit}
              onClose={() => setEditOpen(false)}
            />
          )}
          {retryOpen && canRetry && (
            <RetryCard busy={busy} onSubmit={onRetry} onClose={() => setRetryOpen(false)} />
          )}
          {mission.projectPath && mission.projectKind === "app" && (
            <RunAppCard missionKey={mission.key} shipped={shipped} />
          )}
          <TeamCard team={team} />
          <DetailsCard mission={mission} teamName={teamName} />
          <ApprovalGate mission={mission} run={run} gated={gated} busy={busy} onDecide={onDecide} />
          {shipped && <RequestChangesCard busy={busy} onSubmit={onRequestChange} />}
        </div>
      </div>
    </div>
  );
}
