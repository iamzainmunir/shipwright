"use client";

/**
 * Command Center — the org pulse (dashboard landing).
 *
 * Reproduces the mockup's mission-control layout with the design-system classes and wires every
 * panel to the real orchestrator API: hero greeting + spend donut, the stat-tile row, an
 * attention strip for open blockers, and a two-column body (active missions + pipeline
 * distribution on the left; a live activity feed + on-shift roster on the right).
 *
 * Live sources: getMetrics, getSettings (budget cap), listMissions, listBlockers, listAgents,
 * and listRecentEvents (the real event log behind the activity feed). The polling loop refreshes
 * everything every 8s so the feed and counters stay current. Panels only ever render values the
 * API actually exposes; where there is no real data we show an honest empty state.
 */

import Link from "next/link";
import type { CSSProperties } from "react";
import { useEffect, useMemo, useState } from "react";
import { Badge, type BadgeTone, Button, Icon } from "@foundry/ui";
import {
  type Agent,
  type AutonomyPolicy,
  type Blocker,
  type EventItem,
  type Metrics,
  type Mission,
  type MissionStage,
  type Priority,
  PRIORITY_LABEL,
  STATUS_TONE,
  getMetrics,
  getSettings,
  listAgents,
  listBlockers,
  listMissions,
  listRecentEvents,
} from "@/lib/foundry";
import { isApiError } from "@/lib/api";
import { MetricsTiles } from "@/components/metrics-tiles";

// ---------------------------------------------------------------------------
// Static maps & helpers
// ---------------------------------------------------------------------------

// "stopped" is deliberately NOT an active stage — a force-stopped mission is paused, not in flight,
// so it drops out of the Active-missions list and the active count (retry moves it back to building).
const ACTIVE_STAGES: MissionStage[] = ["spec", "building", "review", "qa"];
const STAGE_ORDER: MissionStage[] = ["backlog", "spec", "building", "review", "qa", "stopped", "shipped"];

const STAGE_TONE: Record<MissionStage, BadgeTone> = {
  backlog: "neutral", spec: "cyan", building: "brand", qa: "amber", review: "blue", shipped: "green",
  stopped: "neutral",
};
const STAGE_LABEL: Record<MissionStage, string> = {
  backlog: "Backlog", spec: "Spec", building: "Building", qa: "QA", review: "Review", shipped: "Shipped",
  stopped: "Stopped",
};
const STAGE_COLOR: Record<MissionStage, string> = {
  backlog: "var(--faint)", spec: "var(--cyan)", building: "var(--brand)",
  qa: "var(--amber)", review: "var(--blue)", shipped: "var(--green)", stopped: "var(--faint)",
};
const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  p0: "red", p1: "amber", p2: "blue", p3: "neutral",
};
const TONE_VAR: Record<BadgeTone, string> = {
  neutral: "var(--faint)", brand: "var(--brand)", green: "var(--green)", amber: "var(--amber)",
  red: "var(--red)", cyan: "var(--cyan)", blue: "var(--blue)", pink: "var(--pink)",
};

const ROLE_COLOR: Record<string, string> = {
  ceo: "#7c5cff", cto: "#5b8cff", pm: "#34d3ee", ba: "#a78bfa", backend: "#3ad29f",
  frontend: "#ff7ac6", qa: "#f6c454", devops: "#34d3ee", designer: "#ff6b7d",
  security: "#ff6b7d", custom: "#9aa2b8",
};
const ROLE_LABEL: Record<string, string> = {
  ceo: "CEO", cto: "CTO", pm: "Product Manager", ba: "Business Analyst",
  backend: "Backend Engineer", frontend: "Frontend Engineer", qa: "QA Engineer",
  devops: "DevOps Engineer", designer: "Product Designer", security: "Security Engineer",
  custom: "Custom Agent",
};

function initials(name: string): string {
  return name.split(" ").map((w) => w[0] ?? "").join("").slice(0, 2).toUpperCase();
}

function money(n: number): string {
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

function greeting(d: Date = new Date()): string {
  const h = d.getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** Relative time for an ISO timestamp; passes through non-ISO human strings unchanged. */
function timeAgo(value?: string | null): string {
  if (!value) return "";
  const t = Date.parse(value);
  if (Number.isNaN(t)) return value;
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function severityTone(severity: string): BadgeTone {
  const s = severity.toLowerCase();
  if (s === "critical" || s === "high") return "red";
  if (s === "medium") return "amber";
  return "blue";
}

function kindLabel(kind: string): string {
  const map: Record<string, string> = {
    approval: "Approval needed", question: "Open question", limit: "Model limit reached",
    token: "Model token / key issue", spend: "Spend approval", review: "Review sign-off",
  };
  return map[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

interface DashboardData {
  missions: Mission[] | null;
  blockers: Blocker[];
  agents: Agent[];
  metrics: Metrics | null;
  settings: AutonomyPolicy | null;
  events: EventItem[];
  error: string | null;
}

const EMPTY: DashboardData = { missions: null, blockers: [], agents: [], metrics: null, settings: null, events: [], error: null };

function useDashboardData(): DashboardData {
  const [data, setData] = useState<DashboardData>(EMPTY);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.allSettled([listMissions(), listBlockers(), listAgents(), getMetrics(), getSettings(), listRecentEvents(20)]).then(
        ([m, b, a, mx, s, e]) => {
          if (!alive) return;
          setData((prev) => ({
            missions: m.status === "fulfilled" ? m.value : prev.missions,
            blockers: b.status === "fulfilled" ? b.value : prev.blockers,
            agents: a.status === "fulfilled" ? a.value : prev.agents,
            metrics: mx.status === "fulfilled" ? mx.value : prev.metrics,
            settings: s.status === "fulfilled" ? s.value : prev.settings,
            events: e.status === "fulfilled" ? e.value : prev.events,
            error:
              m.status === "rejected"
                ? isApiError(m.reason)
                  ? m.reason.message
                  : "Failed to load the command center"
                : null,
          }));
        },
      );
    };
    load();
    const id = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return data;
}

// ---------------------------------------------------------------------------
// Panels
// ---------------------------------------------------------------------------

function Hero({ metrics, openCount }: { metrics: Metrics | null; openCount: number }) {
  // Greeting + date follow the VIEWER's local clock (computed on the client) and roll over the day.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);
  const clock = now ?? new Date();
  const dateLabel = clock.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
  const working = metrics?.agentsWorking ?? 0;
  const active = metrics?.activeMissions ?? 0;
  const shipped = metrics?.shipped ?? 0;
  const allClear = openCount === 0;

  return (
    <div className="card glow" style={{ padding: 26, display: "flex", flexDirection: "column" }}>
      <div className="row between mb-16 wrap">
        <Badge tone={allClear ? "green" : "amber"} dot>
          {allClear ? "All systems operational" : `${openCount} ${openCount === 1 ? "item needs" : "items need"} attention`}
        </Badge>
        <span className="text-xs faint mono">{dateLabel}</span>
      </div>
      <h1 style={{ fontSize: 26 }}>
        {greeting(clock)}. {shipped > 0 ? "Your org is shipping." : "Here's your org at a glance."}
      </h1>
      <p className="page-desc mt-8">
        {working} {working === 1 ? "agent" : "agents"} on shift across {active} active{" "}
        {active === 1 ? "mission" : "missions"}. {shipped} shipped so far
        {allClear ? "." : `, ${openCount} awaiting your call.`}
      </p>
      <div className="row gap-8 mt-20 wrap">
        <span className="pill"><Icon name="users" size={14} /> {working} on shift</span>
        <span className="pill"><Icon name="kanban" size={14} /> {active} in flight</span>
        <span className="pill"><Icon name="shield" size={14} /> {openCount} gates open</span>
      </div>
    </div>
  );
}

function SpendDonut({ spentCents, budgetCents }: { spentCents: number; budgetCents: number }) {
  const spent = spentCents / 100;
  const budget = budgetCents / 100;
  const hasBudget = budget > 0;
  const pct = hasBudget ? Math.min(100, Math.round((spent / budget) * 100)) : 0;
  const r = 52;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - pct / 100);
  const remaining = Math.max(0, budget - spent);

  const status = !hasBudget
    ? { label: "No cap set", cls: "c-muted" }
    : pct < 80
      ? { label: "On track", cls: "c-green" }
      : pct < 100
        ? { label: "Near cap", cls: "c-amber" }
        : { label: "Over cap", cls: "c-red" };

  return (
    <div className="card pad">
      <div className="section-label">Spend to date</div>
      <div style={{ position: "relative", width: 132, height: 132, margin: "0 auto" }}>
        <svg viewBox="0 0 132 132" width={132} height={132} role="img" aria-label={`Spend ${money(spent)}${hasBudget ? ` of ${money(budget)}` : ""}`}>
          <defs>
            <linearGradient id="cc-spend-grad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="var(--brand)" />
              <stop offset="1" stopColor="var(--cyan)" />
            </linearGradient>
          </defs>
          <circle cx="66" cy="66" r={r} fill="none" stroke="var(--panel-2)" strokeWidth={12} />
          <circle
            cx="66" cy="66" r={r} fill="none" stroke="url(#cc-spend-grad)" strokeWidth={12}
            strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
            transform="rotate(-90 66 66)" style={{ transition: "stroke-dashoffset .6s ease" }}
          />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "grid", placeContent: "center", textAlign: "center" }}>
          <div style={{ fontFamily: "var(--display)", fontWeight: 800, fontSize: 22 }}>{spent > 0 ? "~" : ""}{money(spent)}</div>
          <div className="text-xs faint">{hasBudget ? `of ${money(budget)}` : "est. to date"}</div>
        </div>
      </div>
      <div className="row between mt-16 text-sm">
        <span className="faint">Remaining</span>
        <span className="fw-6">{hasBudget ? money(remaining) : "—"}</span>
      </div>
      <div className="row between mt-8 text-sm">
        <span className="faint">Utilization</span>
        <span className={`fw-6 ${status.cls}`}>{hasBudget ? `${pct}% · ${status.label}` : status.label}</span>
      </div>
    </div>
  );
}

function AttentionCard({ blockers, missionById }: { blockers: Blocker[]; missionById: Map<string, Mission> }) {
  return (
    <div className="card mb-20" style={{ borderColor: "color-mix(in srgb, var(--amber) 40%, var(--line))" }}>
      <div className="card-head">
        <h3><Icon name="bell" size={16} /> Needs your attention</h3>
        <Badge tone="amber">{blockers.length} open</Badge>
      </div>
      <div className="card-body" style={{ padding: "4px 18px" }}>
        <div className="list">
          {blockers.slice(0, 5).map((b) => {
            const mission = missionById.get(b.missionId);
            return (
              <div key={b.id} className="list-item" style={{ alignItems: "flex-start" }}>
                <Badge tone={severityTone(b.severity)} style={{ minWidth: 62, justifyContent: "center" }}>{b.severity}</Badge>
                <div className="li-main">
                  <div className="row gap-8 wrap">
                    <span className="li-title">{kindLabel(b.kind)}</span>
                    {mission && <span className="mono text-xs faint">{mission.key}</span>}
                  </div>
                  <div className="li-sub">{b.detail}</div>
                </div>
                {mission && (
                  <Link href={`/dashboard/missions/${mission.key}`} className="text-xs c-brand clickable" style={{ whiteSpace: "nowrap" }}>
                    Open →
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ActiveMissions({ missions }: { missions: Mission[] }) {
  const rows = missions.slice(0, 6);
  return (
    <div className="card">
      <div className="card-head">
        <h3><Icon name="kanban" size={16} /> Active missions</h3>
        <Link href="/dashboard/missions" className="text-xs c-brand clickable">View board →</Link>
      </div>
      <div className="card-body" style={{ padding: "4px 18px" }}>
        {rows.length === 0 ? (
          <div className="empty">
            <div className="em-ic"><Icon name="check" size={28} /></div>
            No missions in flight — the backlog is clear.
          </div>
        ) : (
          <div className="list">
            {rows.map((m) => (
              <Link key={m.id} href={`/dashboard/missions/${m.key}`} className="list-item clickable">
                <Badge tone={PRIORITY_TONE[m.priority]} style={{ minWidth: 38, justifyContent: "center" }}>
                  {PRIORITY_LABEL[m.priority]}
                </Badge>
                <div className="li-main">
                  <div className="row gap-8">
                    <span className="mono text-xs faint">{m.key}</span>
                    <span className="li-title truncate">{m.title}</span>
                  </div>
                  <div className="row gap-8 mt-8 wrap">
                    <div className="progress thin" style={{ width: 130 }}>
                      <div className="bar" style={{ width: `${m.progress}%` }} />
                    </div>
                    <span className="text-xs faint">{m.progress}%</span>
                    <Badge tone={STAGE_TONE[m.stage]} dot>{STAGE_LABEL[m.stage]}</Badge>
                    {m.isBlocked && <Badge tone="amber">blocked</Badge>}
                  </div>
                </div>
                <span className="text-xs faint">{timeAgo(m.updatedAt)}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PipelineDistribution({ missions }: { missions: Mission[] }) {
  const total = missions.length || 1;
  const shipped = missions.filter((m) => m.stage === "shipped").length;
  return (
    <div className="card pad">
      <div className="row between mb-16">
        <div>
          <div className="section-label" style={{ margin: 0 }}>Pipeline distribution</div>
          <div className="text-xs faint">Where all {missions.length} missions sit right now</div>
        </div>
        <Badge tone="green">{shipped} shipped</Badge>
      </div>
      <div className="col gap-12">
        {STAGE_ORDER.map((stage) => {
          const n = missions.filter((m) => m.stage === stage).length;
          return (
            <div key={stage}>
              <div className="row between mb-8">
                <span className="text-sm fw-6">{STAGE_LABEL[stage]}</span>
                <span className="text-xs faint">{n}</span>
              </div>
              <div className="meter">
                <span style={{ width: `${(n / total) * 100}%`, background: STAGE_COLOR[stage] }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Map a real event type to a display tone using only existing tone tokens. */
function eventTone(type: string): BadgeTone {
  const t = type.toLowerCase();
  if (/(ship|merge|deploy|pass|done|complete|resolve)/.test(t)) return "green";
  if (/(block|error|fail|reject|limit)/.test(t)) return "red";
  if (/review/.test(t)) return "blue";
  if (/qa|test/.test(t)) return "amber";
  if (/spec|plan/.test(t)) return "cyan";
  if (/build|code|commit|pr/.test(t)) return "brand";
  // Generic progress pings (status/update/heartbeat/note…) previously fell to the dark, low-contrast
  // neutral chip — give them a bright, readable informational cyan instead of muted gray.
  if (/status|update|heartbeat|progress|working|note|active|idle|thinking/.test(t)) return "cyan";
  return "neutral";
}

/** Condense an event's text into one clean glanceable line — strip markdown/code and
 *  collapse whitespace. The full output lives in the run's console, not this feed. */
function summarize(text: string): string {
  const clean = text
    .replace(/```[\s\S]*?```/g, " ") // fenced code blocks
    .replace(/`[^`]*`/g, " ") // inline code
    .replace(/[#*_>~`]/g, "") // markdown markers
    .replace(/\s+/g, " ") // newlines → single spaces
    .trim();
  return clean.length > 160 ? `${clean.slice(0, 160)}…` : clean;
}

const CLAMP_2: CSSProperties = {
  display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
};

function ActivityFeed({ events }: { events: EventItem[] }) {
  // The API returns newest last; reverse so the freshest event is on top.
  const feed = [...events].reverse();
  return (
    <div className="card">
      <div className="card-head">
        <h3><span className="dot pulse bg-green" /> Live activity</h3>
        <span className="text-xs faint">auto-refreshing</span>
      </div>
      <div className="card-body" style={{ padding: "4px 18px", maxHeight: 360, overflow: "auto" }}>
        {feed.length === 0 ? (
          <div className="empty">
            <div className="em-ic"><Icon name="bolt" size={28} /></div>
            Nothing happening yet.
          </div>
        ) : (
          <div className="list">
            {feed.map((e) => {
              const tone = eventTone(e.type);
              const rc = e.agentRole ? (ROLE_COLOR[e.agentRole] ?? "var(--muted)") : "var(--muted)";
              const who = e.agentRole ? (ROLE_LABEL[e.agentRole] ?? e.agentRole) : null;
              const when = timeAgo(e.ts);
              // The build events read "Ada · did X · thinking (step 2)" — bold the leading actor name.
              const summary = summarize(e.text);
              const parts = summary.split(" · ");
              const lead = parts[0] ?? "";
              const named = parts.length > 1 && lead.length <= 24 && !/[.!?]/.test(lead);
              return (
                <div key={e.id} className="list-item" style={{ alignItems: "flex-start" }}>
                  <span className="dot" style={{ background: TONE_VAR[tone], marginTop: 7,
                    boxShadow: `0 0 0 3px color-mix(in srgb, ${TONE_VAR[tone]} 18%, transparent)` }} />
                  <div className="li-main" style={{ minWidth: 0 }}>
                    <div className="text-sm" style={CLAMP_2}>
                      {named ? (<><b>{lead}</b>{` · ${parts.slice(1).join(" · ")}`}</>) : summary}
                    </div>
                    <div className="li-sub row gap-6 wrap" style={{ alignItems: "center", marginTop: 5 }}>
                      <Badge tone={tone} style={{ height: 18 }}>{e.type}</Badge>
                      {who && (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, height: 18,
                          padding: "0 8px", borderRadius: 999, fontSize: 10.5, fontWeight: 700,
                          color: rc, background: `color-mix(in srgb, ${rc} 13%, transparent)`,
                          border: `1px solid color-mix(in srgb, ${rc} 30%, var(--line))` }}>
                          <span style={{ width: 6, height: 6, borderRadius: "50%", background: rc }} />
                          {who}
                        </span>
                      )}
                      <span className="faint text-xs">{when}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function OnShift({ agents }: { agents: Agent[] }) {
  const rows = agents.slice(0, 6);
  return (
    <div className="card">
      <div className="card-head">
        <h3><Icon name="users" size={16} /> On shift</h3>
        <Link href="/dashboard/team" className="text-xs c-brand clickable">Team →</Link>
      </div>
      <div className="card-body" style={{ padding: "4px 18px" }}>
        {rows.length === 0 ? (
          <div className="empty">
            <div className="em-ic"><Icon name="users" size={28} /></div>
            Everyone is idle right now.
          </div>
        ) : (
          <div className="list">
            {rows.map((a) => (
              <div key={a.id} className="list-item">
                <span className="avatar sm" style={{ background: ROLE_COLOR[a.roleKey] ?? "var(--brand)" }}>
                  {initials(a.name)}
                </span>
                <div className="li-main">
                  <div className="li-title truncate">
                    {a.name}{" "}
                    <span className="faint" style={{ fontWeight: 500 }}>· {ROLE_LABEL[a.roleKey] ?? a.roleKey}</span>
                  </div>
                  <div className="li-sub truncate">
                    {[a.level, a.modelBinding, a.skills[0]].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <span
                  className="dot"
                  role="img"
                  aria-label={a.status}
                  title={a.status}
                  style={{ background: TONE_VAR[STATUS_TONE[a.status] ?? "neutral"] }}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const { missions, blockers, agents, metrics, settings, events, error } = useDashboardData();

  const openBlockers = useMemo(() => blockers.filter((b) => !b.resolvedAt), [blockers]);
  const activeMissions = useMemo(
    () => (missions ?? []).filter((m) => ACTIVE_STAGES.includes(m.stage)),
    [missions],
  );
  const onShift = useMemo(() => agents.filter((a) => a.status !== "idle"), [agents]);
  const missionById = useMemo(() => new Map((missions ?? []).map((m) => [m.id, m])), [missions]);
  const featured = activeMissions.find((m) => m.stage !== "spec") ?? activeMissions[0];

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Command Center</h1>
          <p className="page-desc">
            Your always-on AI engineering org at a glance — every mission, agent, and gate in one view.
          </p>
        </div>
        <div className="page-actions">
          {/* "New mission" lives in the top bar — no duplicate here. */}
          {featured && (
            <Button variant="subtle" asChild>
              <Link href={`/dashboard/missions/${featured.key}`}>
                <Icon name="play" size={15} /> Watch live build
              </Link>
            </Button>
          )}
        </div>
      </div>

      {error && <div className="lb-error">{error}</div>}
      {!missions && !error && <p className="faint">Loading…</p>}

      {missions && (
        <>
          <div className="grid g-2 mb-20">
            <Hero metrics={metrics} openCount={openBlockers.length} />
            <SpendDonut spentCents={metrics?.spendCents ?? 0} budgetCents={settings?.budgetCapCents ?? 0} />
          </div>

          <MetricsTiles />

          {openBlockers.length > 0 && <AttentionCard blockers={openBlockers} missionById={missionById} />}

          <div className="grid g-2">
            <div className="col gap-16">
              <ActiveMissions missions={activeMissions} />
              <PipelineDistribution missions={missions} />
            </div>
            <div className="col gap-16">
              <ActivityFeed events={events} />
              <OnShift agents={onShift} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
