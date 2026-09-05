"use client";

import { Badge, Button, Icon, cx } from "@foundry/ui";
import type { IconName } from "@foundry/ui";
import Link from "next/link";
import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { isApiError } from "@/lib/api";
import { useToast } from "@/components/toast";
import { useConfirm } from "@/components/confirm";
import {
  type Agent,
  type AgentInput,
  type ModelConnection,
  type Team,
  type TeamInput,
  type TeamMember,
  STATUS_TONE,
  createAgent,
  createRole,
  createTeam,
  deleteAgent,
  deleteTeam,
  type RoleInfo,
  type RoleInput,
  listAgents,
  listModelConnections,
  listRoles,
  listTeams,
  updateAgent,
  updateTeam,
} from "@/lib/foundry";

/* ---- roster taxonomy ------------------------------------------------------- */

type Group = "all" | "exec" | "product" | "eng" | "devops" | "quality";

const GROUPS: { key: Group; label: string }[] = [
  { key: "all", label: "All" },
  { key: "exec", label: "Exec" },
  { key: "product", label: "Product" },
  { key: "eng", label: "Eng" },
  { key: "devops", label: "DevOps" },
  { key: "quality", label: "Quality" },
];

/** Which segmented-filter group each specific role belongs to. DevOps is its own group
 *  (platform/operations), separate from application Engineering. */
const ROLE_GROUP: Record<string, Exclude<Group, "all">> = {
  ceo: "exec", cto: "exec",
  pm: "product", ba: "product", designer: "product",
  backend: "eng", frontend: "eng",
  devops: "devops",
  qa: "quality", security: "quality",
};

const ROLE_LABEL: Record<string, string> = {
  ceo: "CEO", cto: "CTO", pm: "Product Manager", ba: "Business Analyst",
  backend: "Backend Engineer", frontend: "Frontend Engineer", qa: "QA Engineer",
  devops: "DevOps Engineer", designer: "Product Designer", security: "Security Engineer",
  custom: "Custom Agent",
};

/** Role accent used for the avatar — token-only so it tracks the theme. */
const ROLE_COLOR: Record<string, string> = {
  ceo: "var(--brand)", cto: "var(--blue)", pm: "var(--cyan)", ba: "var(--brand-2)",
  backend: "var(--green)", frontend: "var(--pink)", qa: "var(--amber)", devops: "var(--cyan)",
  designer: "var(--pink)", security: "var(--red)", custom: "var(--muted)",
};

/** Group → accent color, used to color CUSTOM roles (which have no entry in ROLE_COLOR). */
const GROUP_COLOR: Record<string, string> = {
  exec: "var(--brand)", product: "var(--cyan)", eng: "var(--green)",
  devops: "var(--blue)", quality: "var(--amber)", other: "var(--muted)",
};

/** Live role directory (built-in + workspace custom roles), populated from GET /roles so custom
 *  roles resolve to a real label / group / color everywhere they're rendered. */
let ROLE_DIR: Record<string, RoleInfo> = {};
const setRoleDir = (r: Record<string, RoleInfo>) => { ROLE_DIR = r; };
const roleLabel = (key: string): string => ROLE_DIR[key]?.label ?? ROLE_LABEL[key] ?? titleCase(key);
const roleGroupOf = (key: string): string => ROLE_DIR[key]?.group ?? ROLE_GROUP[key] ?? "other";
const roleColor = (key: string): string => ROLE_COLOR[key] ?? GROUP_COLOR[roleGroupOf(key)] ?? "var(--brand)";

/** Role options for the create/edit-agent dropdown (value → label). */
const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "ceo", label: "CEO" },
  { value: "cto", label: "CTO" },
  { value: "pm", label: "Product Manager" },
  { value: "ba", label: "Business Analyst" },
  { value: "backend", label: "Backend Engineer" },
  { value: "frontend", label: "Frontend Engineer" },
  { value: "qa", label: "QA Engineer" },
  { value: "devops", label: "DevOps Engineer" },
  { value: "designer", label: "Product Designer" },
  { value: "security", label: "Security Engineer" },
  { value: "custom", label: "Custom Agent" },
];

/** Experience levels — MUST match the backend AgentLevel enum (junior/senior/principal/strategic). */
const LEVEL_OPTIONS: string[] = ["junior", "senior", "principal", "strategic"];

const LEVEL_LABEL: Record<string, string> = {
  junior: "Junior", senior: "Senior", principal: "Principal", strategic: "Strategic",
};

const STATUS_COLOR: Record<Agent["status"], string> = {
  working: "var(--green)", review: "var(--blue)", idle: "var(--muted)", blocked: "var(--red)",
};

const STATUS_LABEL: Record<Agent["status"], string> = {
  working: "Working", review: "In review", idle: "Idle", blocked: "Blocked",
};

/** Derived "current work" line — status-based, never a fabricated mission record. */
const WORK_COPY: Record<Agent["status"], { icon: IconName; text: string }> = {
  working: { icon: "bolt", text: "On an active mission" },
  review: { icon: "shield", text: "Reviewing a teammate's work" },
  idle: { icon: "clock", text: "Idle · awaiting a mission" },
  blocked: { icon: "pause", text: "Blocked · needs a decision" },
};

const STAT_FIELDS: { key: string; label: string }[] = [
  { key: "shipped", label: "Shipped" },
  { key: "prs", label: "PRs" },
  { key: "reviews", label: "Reviews" },
];

/* ---- pure helpers ---------------------------------------------------------- */

function initials(name: string): string {
  return name.split(/\s+/).map((w) => w[0] ?? "").join("").slice(0, 2).toUpperCase();
}

function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

/** Order-preserving de-duplication for model / skill id lists. */
function dedupe(list: string[]): string[] {
  return Array.from(new Set(list));
}

/** Split a comma-separated field into trimmed, non-empty, de-duped tokens. */
function parseCsv(value: string): string[] {
  return dedupe(value.split(",").map((s) => s.trim()).filter(Boolean));
}

function countByGroup(agents: Agent[]): Record<Group, number> {
  const counts: Record<Group, number> = { all: agents.length, exec: 0, product: 0, eng: 0, devops: 0, quality: 0 };
  for (const agent of agents) {
    const group = roleGroupOf(agent.roleKey);
    if (group in counts && group !== "all") counts[group as Group] += 1;
  }
  return counts;
}

/* ---- presentational pieces ------------------------------------------------- */

function StatCell({ label, value }: { label: string; value: number }) {
  return (
    <div className="tm-stat">
      <div className="tm-sv">{value.toLocaleString()}</div>
      <div className="tm-sl">{label}</div>
    </div>
  );
}

function SkillChips({ skills }: { skills: string[] }) {
  if (skills.length === 0) {
    return (
      <div className="row wrap gap-6 tm-chips">
        <span className="text-xs faint">No skills recorded</span>
      </div>
    );
  }
  const shown = skills.slice(0, 4);
  const extra = skills.length - shown.length;
  return (
    <div className="row wrap gap-6 tm-chips">
      {shown.map((skill) => (
        <span key={skill} className="chip">{skill}</span>
      ))}
      {extra > 0 && <span className="chip c-muted">+{extra}</span>}
    </div>
  );
}

function AgentCard({
  agent,
  onEdit,
  onRemove,
}: {
  agent: Agent;
  onEdit: (agent: Agent) => void;
  onRemove: (agent: Agent) => void;
}) {
  const accent = STATUS_COLOR[agent.status];
  const work = WORK_COPY[agent.status];
  const primary = agent.modelBinding ?? agent.models[0] ?? "unassigned";
  const failover = agent.models.slice(1);
  return (
    <article className="card pad tm-card" style={{ "--acc": accent } as CSSProperties}>
      <div className="row tm-top">
        <span className="avatar lg" style={{ background: roleColor(agent.roleKey) }}>
          {initials(agent.name)}
        </span>
        <div className="tm-id">
          <div className="tm-name truncate">{agent.name}</div>
          <div className="tm-tags">
            <span className="tm-rolechip"
                  style={{ "--rc": roleColor(agent.roleKey) } as CSSProperties}>
              {roleLabel(agent.roleKey)}
            </span>
            <span className={cx("tm-level", `lvl-${agent.level}`)}>
              {LEVEL_LABEL[agent.level] ?? titleCase(agent.level)}
            </span>
          </div>
        </div>
        <div className="tm-top-end">
          <Badge tone={STATUS_TONE[agent.status] ?? "neutral"} dot>{STATUS_LABEL[agent.status]}</Badge>
          <div className="row gap-6 tm-cardactions">
            <Button variant="ghost" size="sm" aria-label={`Edit ${agent.name}`} onClick={() => onEdit(agent)}>
              <Icon name="pen" size={13} /> Edit
            </Button>
            <Button variant="ghost" size="sm" aria-label={`Remove ${agent.name}`} onClick={() => onRemove(agent)}>
              <Icon name="close" size={13} /> Remove
            </Button>
          </div>
        </div>
      </div>

      <div className="row gap-8 tm-task">
        <Icon name={work.icon} size={13} />
        <span className="truncate">{work.text}</span>
      </div>

      <div className="tm-modelrow">
        <span className="chip mono tm-model">
          <Icon name="cpu" size={12} />
          {primary}
        </span>
        {failover.length > 0 && (
          <div className="tm-failover truncate">failover: {failover.join(", ")}</div>
        )}
      </div>

      <SkillChips skills={agent.skills} />

      <div className="tm-stats">
        {STAT_FIELDS.map((field) => (
          <StatCell key={field.key} label={field.label} value={agent.stats[field.key] ?? 0} />
        ))}
      </div>
    </article>
  );
}

/* ---- hierarchy (org-chart) view ------------------------------------------- */

/** Org tiers by ROLE (top → bottom), an org-chart reporting structure. Level is shown per person. */
const TIERS: { key: string; label: string; hint: string; color: string }[] = [
  { key: "exec", label: "Executive · C-Suite", hint: "Sets direction; owns the final call.", color: "var(--brand)" },
  { key: "product", label: "Product & Design", hint: "Shapes what gets built and why.", color: "var(--cyan)" },
  { key: "eng", label: "Engineering", hint: "Designs, builds & ships the code.", color: "var(--green)" },
  { key: "devops", label: "DevOps & Platform", hint: "Ships, scales & runs the infra.", color: "var(--blue)" },
  { key: "quality", label: "Quality & Security", hint: "Verifies correctness & safety.", color: "var(--amber)" },
  { key: "other", label: "Other", hint: "Specialist & custom roles.", color: "var(--muted)" },
];

/** An agent's tier is its ROLE group (exec/product/eng/quality), else "other". */
function tierOf(agent: Agent): string {
  return roleGroupOf(agent.roleKey);
}

/** Seniority order used to sort people WITHIN a role tier (most senior first). */
const LEVEL_RANK: Record<string, number> = { strategic: 0, principal: 1, senior: 2, junior: 3 };

const TIER_ORDER: Record<string, number> = Object.fromEntries(TIERS.map((t, i) => [t.key, i]));

/** Canonical org ordering: by role tier (exec → product → eng → quality), then seniority, then name. */
function orgSortKey(a: Agent): string {
  const tier = String(TIER_ORDER[tierOf(a)] ?? 9).padStart(2, "0");
  const lvl = String(LEVEL_RANK[a.level] ?? 9);
  return `${tier}-${lvl}-${a.roleKey}-${a.name}`;
}

/** Reporting/workflow tiers, ordered by the AI agentic SDLC (plan → build → verify → ship) — the
 *  same flow Shipwright's own pipeline runs (intake/spec = PM → build = Eng → review = CTO → QA →
 *  ship = DevOps). Aligned with the agentic-SDLC role model (Thoughtworks / Hatchworks, 2026):
 *  Leadership/Architecture → Product & Design → Engineering → Quality & Delivery. Peers within a
 *  tier sit on the SAME row (e.g. all Backend + Frontend engineers together). */
const SDLC_TIERS: { key: string; label: string; hint: string; roles: string[]; color: string }[] = [
  { key: "leadership", label: "Leadership", hint: "Architecture & the final call",
    roles: ["ceo", "cto"], color: "var(--brand)" },
  { key: "product", label: "Product & Design", hint: "Defines what to build & why",
    roles: ["pm", "ba", "designer"], color: "var(--cyan)" },
  { key: "engineering", label: "Engineering", hint: "Designs & builds the code",
    roles: ["backend", "frontend", "fullstack"], color: "var(--green)" },
  { key: "delivery", label: "Quality & Delivery", hint: "Verifies, secures & ships",
    roles: ["qa", "security", "devops"], color: "var(--amber)" },
  { key: "other", label: "Specialists", hint: "Custom & specialist roles",
    roles: [], color: "var(--muted)" },
];
const ROLE_TIER: Record<string, string> =
  Object.fromEntries(SDLC_TIERS.flatMap((t) => t.roles.map((r) => [r, t.key])));
/** Custom roles have no built-in tier — place them by their group. */
const GROUP_TIER: Record<string, string> = {
  exec: "leadership", product: "product", eng: "engineering",
  devops: "delivery", quality: "delivery", other: "other",
};
const tierOfRole = (key: string): string =>
  ROLE_TIER[key] ?? GROUP_TIER[roleGroupOf(key)] ?? "other";

/** Compact display of a model id — drop the provider prefix ("openai/gpt-oss-120b" → "gpt-oss-120b")
 *  so the always-visible node chip stays readable; the full id shows in the hover tooltip. */
function shortModel(id: string): string {
  const slash = id.lastIndexOf("/");
  return slash >= 0 ? id.slice(slash + 1) : id;
}

/** The primary model an agent runs on (its binding, else the first of its failover list). */
function primaryModel(agent: Agent): string | null {
  return agent.modelBinding ?? agent.models[0] ?? null;
}

/** One person node in the org chart — avatar, name, role, level, primary model, live status dot.
 *  Hovering the model chip reveals the full failover chain (via the shared portal tooltip). */
function PersonNode({
  agent, accountable, root, onEdit, onModelEnter, onModelLeave,
}: {
  agent: Agent;
  accountable?: boolean;
  root?: boolean;
  onEdit: (a: Agent) => void;
  onModelEnter?: (a: Agent, el: HTMLElement) => void;
  onModelLeave?: () => void;
}) {
  const primary = primaryModel(agent);
  const failoverN = Math.max(0, agent.models.length - 1);
  return (
    <button
      type="button"
      className={cx("og-node", root && "og-node-root")}
      onClick={() => onEdit(agent)}
      aria-label={`Edit ${agent.name}`}
    >
      <span
        className="og-dot"
        title={STATUS_LABEL[agent.status]}
        style={{ background: STATUS_COLOR[agent.status] }}
      />
      <span className="avatar" style={{ background: roleColor(agent.roleKey) }}>
        {initials(agent.name)}
      </span>
      <span className="og-name">
        {agent.name}
        {accountable && <Icon name="star" size={11} style={{ color: "var(--amber)" }} />}
      </span>
      <span className="og-role" style={{ color: roleColor(agent.roleKey) }}>
        {roleLabel(agent.roleKey)}
      </span>
      <span className={cx("tm-level", "tm-level-xs", `lvl-${agent.level}`)}>
        {LEVEL_LABEL[agent.level] ?? titleCase(agent.level)}
      </span>
      {/* rendered as a span (not a nested button — this whole node is a <button>) */}
      <span
        className={cx("og-model", "mono", !primary && "og-model-empty")}
        onMouseEnter={(e) => onModelEnter?.(agent, e.currentTarget)}
        onMouseLeave={onModelLeave}
        onFocus={(e) => onModelEnter?.(agent, e.currentTarget)}
        onBlur={onModelLeave}
        tabIndex={0}
        aria-label={
          primary
            ? `Runs on ${primary}${failoverN ? `, ${failoverN} failover model${failoverN > 1 ? "s" : ""}` : ""}`
            : "No model assigned"
        }
      >
        <Icon name="cpu" size={10} />
        <span className="truncate">{primary ? shortModel(primary) : "no model"}</span>
        {failoverN > 0 && <span className="og-modeln">+{failoverN}</span>}
      </span>
    </button>
  );
}

/** Shared hover tooltip for a person node's model chip — one portal for the whole chart so it is
 *  never clipped by the tier row's horizontal scroll. Shows the primary + the ordered failover chain. */
function ModelTip({ agent, top, left, below }: { agent: Agent; top: number; left: number; below: boolean }) {
  const primary = primaryModel(agent);
  const failover = agent.models.filter((m) => m !== primary); // every model that isn't the primary
  return createPortal(
    <div className={cx("og-tip", below && "og-tip-below")} style={{ top, left }} role="tooltip">
      <div className="og-tip-head">
        <Icon name="cpu" size={12} />
        <span>{agent.name}’s models</span>
      </div>
      {primary ? (
        <>
          <div className="og-tip-row">
            <span className="og-tip-tag og-tip-primary">primary</span>
            <span className="mono og-tip-model">{primary}</span>
          </div>
          {failover.length > 0 ? (
            failover.map((m, i) => (
              <div className="og-tip-row" key={m}>
                <span className="og-tip-tag">{i === 0 ? "failover" : ""}</span>
                <span className="mono og-tip-model">{m}</span>
              </div>
            ))
          ) : (
            <div className="og-tip-none">No failover models</div>
          )}
        </>
      ) : (
        <div className="og-tip-none">No model assigned yet</div>
      )}
      <span className="og-tip-arrow" />
    </div>,
    document.body,
  );
}

/** A team's hierarchy as a top-down org chart, laid out by the agentic SDLC: Leadership → Product →
 *  Engineering → Quality & Delivery. Each tier is one horizontal band, so peers (e.g. all Backend +
 *  Frontend engineers) sit on the SAME level; tiers are linked by a central connector. */
function OrgChart({
  team, agentsById, onEdit,
}: { team: Team; agentsById: Map<string, Agent>; onEdit: (a: Agent) => void }) {
  const members = team.members
    .map((m) => ({ member: m, agent: agentsById.get(m.agentId) }))
    .filter((e): e is { member: TeamMember; agent: Agent } => e.agent !== undefined);
  const senior = (a: Agent) => LEVEL_RANK[a.level] ?? 9;
  const bySenior = (a: { agent: Agent }, b: { agent: Agent }) =>
    senior(a.agent) - senior(b.agent) || a.agent.roleKey.localeCompare(b.agent.roleKey);

  // Shared model tooltip: one portal for the chart, positioned above the hovered chip (fixed coords
  // from its rect) so the tier row's horizontal scroll can never clip it.
  const [tip, setTip] = useState<{ agent: Agent; top: number; left: number; below: boolean } | null>(null);
  const onModelEnter = useCallback((agent: Agent, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const below = r.top < 150; // top-tier nodes have no room above → drop the tip below the chip
    setTip({ agent, top: below ? r.bottom + 8 : r.top - 8, left: r.left + r.width / 2, below });
  }, []);
  const onModelLeave = useCallback(() => setTip(null), []);

  if (members.length === 0) {
    return <p className="muted text-sm">No members assigned to this team yet.</p>;
  }
  const tiers = SDLC_TIERS
    .map((t) => ({
      ...t,
      people: members.filter((e) => tierOfRole(e.agent.roleKey) === t.key).sort(bySenior),
    }))
    .filter((t) => t.people.length > 0);

  return (
    <div className="og-wrap">
      <div className="og-tiers">
        {tiers.map((t, i) => (
          <div key={t.key} className="og-tier" style={{ "--dc": t.color } as CSSProperties}>
            <div className="og-tierhead">
              <span className="og-tierlabel">{t.label}</span>
              <span className="og-tier-n">{t.people.length}</span>
              <span className="og-tierhint">{t.hint}</span>
            </div>
            <div className="og-tierrow">
              {t.people.map((e) => (
                <PersonNode
                  key={e.agent.id}
                  agent={e.agent}
                  accountable={e.member.accountable}
                  root={t.key === "leadership"}
                  onEdit={onEdit}
                  onModelEnter={onModelEnter}
                  onModelLeave={onModelLeave}
                />
              ))}
            </div>
            {i < tiers.length - 1 && <div className="og-connector" />}
          </div>
        ))}
      </div>
      {tip && <ModelTip agent={tip.agent} top={tip.top} left={tip.left} below={tip.below} />}
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="lb-error"
      role="alert"
      style={{
        color: "var(--red)",
        background: "rgba(255, 107, 125, .12)",
        border: "1px solid rgba(255, 107, 125, .35)",
        borderRadius: 12,
        padding: "10px 14px",
        marginBottom: 14,
        fontSize: 13,
      }}
    >
      {message}
    </div>
  );
}

/* ---- create / edit agent --------------------------------------------------- */

interface ModelOption {
  id: string; // the concrete model id (what gets stored)
  provider: string;
  label: string; // "provider · model"
}

/** Flatten every connected connection's models into "provider · model" options. */
function modelOptions(connections: ModelConnection[]): ModelOption[] {
  const seen = new Set<string>();
  const out: ModelOption[] = [];
  for (const conn of connections) {
    for (const model of conn.models) {
      if (seen.has(model)) continue;
      seen.add(model);
      out.push({ id: model, provider: conn.provider, label: `${conn.provider} · ${model}` });
    }
  }
  return out;
}

interface AgentComposerProps {
  agent: Agent | null; // null → create, otherwise edit
  connections: ModelConnection[];
  roles: Record<string, RoleInfo>;
  onClose: () => void;
  onSubmit: (input: AgentInput, id?: string) => void;
}

/** Create/edit-agent modal. Models are chosen only from real connections. */
function AgentComposer({ agent, connections, roles, onClose, onSubmit }: AgentComposerProps) {
  const editing = agent !== null;
  const options = useMemo(() => modelOptions(connections), [connections]);
  // Role dropdown = built-in roles (in canonical order) + workspace custom roles (marked, last).
  const roleOptions = useMemo(() => {
    const order = ["ceo", "cto", "pm", "ba", "designer", "backend", "frontend", "qa", "security", "devops", "custom"];
    const list = Object.entries(roles).length
      ? Object.entries(roles).map(([value, info]) => ({ value, label: info.label ?? value, custom: !!info.custom }))
      : ROLE_OPTIONS.map((o) => ({ ...o, custom: false }));
    return list.sort((a, b) => {
      const ai = order.indexOf(a.value);
      const bi = order.indexOf(b.value);
      if (ai === -1 && bi === -1) return a.label.localeCompare(b.label);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
  }, [roles]);
  const [name, setName] = useState(agent?.name ?? "");
  const [roleKey, setRoleKey] = useState(agent?.roleKey ?? "custom");
  const [level, setLevel] = useState(agent?.level ?? "senior");
  // Ordered model ids — first is primary, the rest are failover. Drop any STALE models that aren't in
  // a live connection (e.g. seed defaults like qwen2.5:7b/gemma2:2b that were never actually connected),
  // and float the agent's selected binding to the front so the shown "primary" matches what runs.
  const [models, setModels] = useState<string[]>(() => {
    const available = new Set(modelOptions(connections).map((o) => o.id));
    const kept = (agent?.models ?? []).filter((m) => available.has(m));
    const bind = agent?.modelBinding;
    return bind && available.has(bind) ? [bind, ...kept.filter((m) => m !== bind)] : kept;
  });
  // The role's standard skills are auto-assigned + LOCKED; the input holds only ADDITIONAL skills.
  const lockedSkills = roles[roleKey]?.skills ?? [];
  const roleScope = roles[roleKey]?.scope ?? "";
  const [skills, setSkills] = useState(() => {
    const allLocked = new Set(Object.values(roles).flatMap((r) => r.skills.map((s) => s.toLowerCase())));
    return (agent?.skills ?? []).filter((s) => !allLocked.has(s.toLowerCase())).join(", ");
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggleModel = (id: string) =>
    setModels((cur) => (cur.includes(id) ? cur.filter((m) => m !== id) : [...cur, id]));

  const primary = models[0];
  const failover = models.slice(1);
  const nameOk = name.trim().length > 0;

  const save = () => {
    if (!nameOk) return;
    const input: AgentInput = {
      name: name.trim(),
      roleKey,
      level,
      models,
      modelBinding: models[0],
      skills: parseCsv(skills),
    };
    onSubmit(input, agent?.id);
  };

  return (
    <div className="overlay" role="dialog" aria-modal aria-label={editing ? "Edit agent" : "Add agent"}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}>
            <Icon name={editing ? "pen" : "plus"} size={16} /> {editing ? "Edit agent" : "Add agent"}
          </h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label htmlFor="ag-name">Name</label>
            <input id="ag-name" className="input" autoFocus value={name}
              placeholder="e.g. Ada Lovelace" onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid g-2">
            <div className="field">
              <label htmlFor="ag-role">Role</label>
              <select id="ag-role" className="select" value={roleKey} onChange={(e) => setRoleKey(e.target.value)}>
                {roleOptions.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}{r.custom ? " ✦" : ""}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ag-level">Level</label>
              <select id="ag-level" className="select" value={level} onChange={(e) => setLevel(e.target.value)}>
                {LEVEL_OPTIONS.map((l) => <option key={l} value={l}>{LEVEL_LABEL[l] ?? titleCase(l)}</option>)}
              </select>
            </div>
          </div>

          <div className="field" style={{ marginBottom: 0 }}>
            <label>Models</label>
            {options.length === 0 ? (
              <p className="muted text-sm" style={{ margin: "2px 0 0" }}>
                No models connected yet.{" "}
                <Link href="/dashboard/models" className="link-btn" onClick={onClose}>Connect a model first</Link>{" "}
                to staff this agent.
              </p>
            ) : (
              <>
                <div className="tm-modelpick">
                  {options.map((opt) => {
                    const checked = models.includes(opt.id);
                    return (
                      <label key={opt.id} className="tm-modelopt">
                        <input type="checkbox" checked={checked} onChange={() => toggleModel(opt.id)} />
                        <span className="mono text-sm truncate">{opt.label}</span>
                        {checked && opt.id === primary && <Badge tone="brand">primary</Badge>}
                      </label>
                    );
                  })}
                </div>
                <p className="text-xs faint" style={{ margin: "8px 0 0" }}>
                  {primary
                    ? `Primary: ${primary}${failover.length ? ` · Failover: ${failover.join(", ")}` : ""}`
                    : "The first model you check becomes the primary; the rest are failover."}
                </p>
              </>
            )}
          </div>

          {lockedSkills.length > 0 && (
            <div className="field" style={{ marginTop: 16, marginBottom: 0 }}>
              <label>Role skills <span className="faint">· auto-assigned, always included</span></label>
              {roleScope && <p className="text-xs faint" style={{ margin: "0 0 6px" }}>{roleScope}</p>}
              <div className="tm-tags">
                {lockedSkills.map((s) => (
                  <span key={s} className="chip" style={{ opacity: 0.9 }}>
                    <Icon name="check" size={11} /> {s}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="field" style={{ marginTop: 16, marginBottom: 0 }}>
            <label htmlFor="ag-skills">Additional skills <span className="faint">(optional)</span></label>
            <input id="ag-skills" className="input" value={skills}
              placeholder="Comma-separated extras, e.g. graphql, storybook"
              onChange={(e) => setSkills(e.target.value)} />
          </div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!nameOk} onClick={save}>
            <Icon name="check" size={15} /> {editing ? "Save changes" : "Add agent"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---- teams ----------------------------------------------------------------- */

function TeamCard({
  team,
  agentsById,
  onOpen,
  onEdit,
  onRemove,
}: {
  team: Team;
  agentsById: Map<string, Agent>;
  onOpen: (team: Team) => void;
  onEdit: (team: Team) => void;
  onRemove: (team: Team) => void;
}) {
  const members = team.members
    .map((m) => ({ member: m, agent: agentsById.get(m.agentId) }))
    .filter((entry): entry is { member: TeamMember; agent: Agent } => entry.agent !== undefined)
    .sort((a, b) => orgSortKey(a.agent).localeCompare(orgSortKey(b.agent)));  // role → level → name
  const accountable = members.find((m) => m.member.accountable);
  const roleCount = new Set(members.map((m) => roleGroupOf(m.agent.roleKey))).size;
  const shown = members.slice(0, 7);
  const extra = members.length - shown.length;

  return (
    <article
      className="tm-teamcard"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(team)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(team))}
    >
      <div className="row between tm-top" style={{ gap: 12 }}>
        <div className="tm-id">
          <div className="tm-name truncate">{team.name}</div>
          {team.description && <div className="tm-role truncate">{team.description}</div>}
        </div>
        <div className="row gap-6 tm-teamactions" onClick={(e) => e.stopPropagation()}>
          <Button variant="ghost" size="sm" aria-label={`Edit ${team.name}`} onClick={() => onEdit(team)}>
            <Icon name="pen" size={13} />
          </Button>
          <Button variant="ghost" size="sm" aria-label={`Remove ${team.name}`} onClick={() => onRemove(team)}>
            <Icon name="close" size={13} />
          </Button>
        </div>
      </div>

      {members.length === 0 ? (
        <p className="muted text-sm" style={{ margin: "16px 0 0" }}>No members assigned yet.</p>
      ) : (
        <div className="tm-avstack">
          {shown.map(({ agent }) => (
            <span
              key={agent.id}
              className="avatar sm tm-av"
              style={{ background: roleColor(agent.roleKey) }}
              title={`${agent.name} · ${roleLabel(agent.roleKey)}`}
            >
              {initials(agent.name)}
            </span>
          ))}
          {extra > 0 && <span className="avatar sm tm-av tm-av-more">+{extra}</span>}
        </div>
      )}

      <div className="tm-teamfoot">
        <span className="text-xs faint">
          {members.length} member{members.length === 1 ? "" : "s"} · {roleCount} discipline{roleCount === 1 ? "" : "s"}
          {accountable ? ` · ${accountable.agent.name} accountable` : ""}
        </span>
        <span className="tm-team-open">Org chart →</span>
      </div>
    </article>
  );
}

interface TeamComposerProps {
  team: Team | null; // null → create, otherwise edit
  agents: Agent[];
  onClose: () => void;
  onSubmit: (input: TeamInput, id?: string) => void;
}

/** Create/edit-team modal — name, description, and a per-agent member picker. */
function TeamComposer({ team, agents, onClose, onSubmit }: TeamComposerProps) {
  const editing = team !== null;
  const [name, setName] = useState(team?.name ?? "");
  const [description, setDescription] = useState(team?.description ?? "");
  const [members, setMembers] = useState<Record<string, { included: boolean; accountable: boolean }>>(() => {
    const seed: Record<string, { included: boolean; accountable: boolean }> = {};
    for (const m of team?.members ?? []) seed[m.agentId] = { included: true, accountable: m.accountable };
    return seed;
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggleInclude = (id: string) =>
    setMembers((cur) => {
      const prev = cur[id] ?? { included: false, accountable: false };
      const included = !prev.included;
      return { ...cur, [id]: { included, accountable: included ? prev.accountable : false } };
    });

  const toggleAccountable = (id: string) =>
    setMembers((cur) => {
      const prev = cur[id] ?? { included: false, accountable: false };
      if (!prev.included) return cur;
      return { ...cur, [id]: { ...prev, accountable: !prev.accountable } };
    });

  const nameOk = name.trim().length > 0;

  const save = () => {
    if (!nameOk) return;
    const chosen: TeamMember[] = agents
      .filter((a) => members[a.id]?.included)
      .map((a) => ({ agentId: a.id, accountable: Boolean(members[a.id]?.accountable) }));
    const input: TeamInput = {
      name: name.trim(),
      description: description.trim() || undefined,
      members: chosen,
    };
    onSubmit(input, team?.id);
  };

  return (
    <div className="overlay" role="dialog" aria-modal aria-label={editing ? "Edit team" : "Create team"}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}>
            <Icon name="users" size={16} /> {editing ? "Edit team" : "Create team"}
          </h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label htmlFor="tm-name">Name</label>
            <input id="tm-name" className="input" autoFocus value={name}
              placeholder="e.g. Payments squad" onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="tm-desc">Description</label>
            <input id="tm-desc" className="input" value={description}
              placeholder="What this team is staffing (optional)" onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="field" style={{ marginBottom: 0 }}>
            <label>Members</label>
            {agents.length === 0 ? (
              <p className="muted text-sm" style={{ margin: "2px 0 0" }}>
                No agents to add yet — create an agent first.
              </p>
            ) : (
              <div className="tm-memberpick">
                {agents.map((a) => {
                  const state = members[a.id] ?? { included: false, accountable: false };
                  return (
                    <div key={a.id} className="tm-pickrow">
                      <label className="row gap-8" style={{ minWidth: 0, cursor: "pointer" }}>
                        <input type="checkbox" checked={state.included} onChange={() => toggleInclude(a.id)} />
                        <span className="avatar sm" style={{ background: roleColor(a.roleKey) }}>
                          {initials(a.name)}
                        </span>
                        <span className="tm-member-id">
                          <span className="text-sm fw-6 truncate">{a.name}</span>
                          <span className="tm-role truncate">{roleLabel(a.roleKey)}</span>
                        </span>
                      </label>
                      <label
                        className="row gap-6 text-xs"
                        style={{ cursor: state.included ? "pointer" : "not-allowed", opacity: state.included ? 1 : 0.45 }}
                        title="Accountable — the final decision-maker for this role"
                      >
                        <span className="switch">
                          <input
                            type="checkbox"
                            checked={state.accountable}
                            disabled={!state.included}
                            onChange={() => toggleAccountable(a.id)}
                          />
                          <span className="track" /><span className="thumb" />
                        </span>
                        <Icon name="star" size={12} /> Accountable
                      </label>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!nameOk} onClick={save}>
            <Icon name="check" size={15} /> {editing ? "Save changes" : "Create team"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---- create custom role ---------------------------------------------------- */

const ROLE_GROUPS: { key: string; label: string }[] = [
  { key: "exec", label: "Executive" },
  { key: "product", label: "Product & Design" },
  { key: "eng", label: "Engineering" },
  { key: "devops", label: "DevOps & Platform" },
  { key: "quality", label: "Quality & Security" },
  { key: "other", label: "Specialists" },
];

/** Create a workspace custom role (e.g. Product Coordinator) with its own default skills — like a
 *  real org adding a role. It then appears in the agent Role dropdown and the org chart. */
function RoleComposer({ onClose, onSubmit }: { onClose: () => void; onSubmit: (input: RoleInput) => void }) {
  const [label, setLabel] = useState("");
  const [group, setGroup] = useState("product");
  const [skills, setSkills] = useState("");
  const [scope, setScope] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const nameOk = label.trim().length > 0;
  const save = () => {
    if (!nameOk) return;
    onSubmit({ label: label.trim(), group, skills: parseCsv(skills), scope: scope.trim() || undefined });
  };

  return (
    <div className="overlay" role="dialog" aria-modal aria-label="Create role"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}><Icon name="sparkles" size={16} /> Create role</h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label htmlFor="rl-name">Role name</label>
            <input id="rl-name" className="input" autoFocus value={label}
              placeholder="e.g. Product Coordinator" onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="rl-group">Department</label>
            <select id="rl-group" className="select" value={group} onChange={(e) => setGroup(e.target.value)}>
              {ROLE_GROUPS.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="rl-skills">Default skills <span className="faint">(comma-separated)</span></label>
            <input id="rl-skills" className="input" value={skills}
              placeholder="e.g. stakeholder-sync, backlog-grooming, reporting"
              onChange={(e) => setSkills(e.target.value)} />
            <span className="hint">Auto-assigned (and locked) on every agent given this role.</span>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="rl-scope">Scope <span className="faint">(optional)</span></label>
            <input id="rl-scope" className="input" value={scope}
              placeholder="What this role owns — and what it does NOT"
              onChange={(e) => setScope(e.target.value)} />
          </div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!nameOk} onClick={save}>
            <Icon name="check" size={15} /> Create role
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---- screen ---------------------------------------------------------------- */

export default function TeamPage() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [connections, setConnections] = useState<ModelConnection[]>([]);
  const [roles, setRoles] = useState<Record<string, RoleInfo>>({});
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<Group>("all");
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  // null = closed; { agent: null } = create; { agent } = edit. Same shape for teams.
  const [agentModal, setAgentModal] = useState<{ agent: Agent | null } | null>(null);
  const [teamModal, setTeamModal] = useState<{ team: Team | null } | null>(null);
  const [roleModalOpen, setRoleModalOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextAgents, nextTeams, nextConnections, nextRoles] = await Promise.all([
        listAgents(),
        listTeams(),
        listModelConnections(),
        listRoles().catch(() => ({}) as Record<string, RoleInfo>),
      ]);
      setAgents(nextAgents);
      setTeams(nextTeams);
      setConnections(nextConnections);
      setRoles(nextRoles);
      setRoleDir(nextRoles);  // so custom roles resolve to a label / group / color everywhere
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to load team");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => countByGroup(agents ?? []), [agents]);
  const visible = useMemo(
    () => (agents ?? [])
      .filter((a) => group === "all" || roleGroupOf(a.roleKey) === group)
      .sort((a, b) => orgSortKey(a).localeCompare(orgSortKey(b))),  // role → level → name
    [agents, group],
  );
  const agentsById = useMemo(() => {
    const map = new Map<string, Agent>();
    for (const a of agents ?? []) map.set(a.id, a);
    return map;
  }, [agents]);

  const submitAgent = async (input: AgentInput, id?: string) => {
    try {
      if (id) await updateAgent(id, input);
      else await createAgent(input);
      setAgentModal(null);
      await load();
      toast(id ? "Agent updated" : "Agent added", input.name, "ok");
    } catch (e) {
      toast(id ? "Couldn't update agent" : "Couldn't add agent", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const removeAgent = async (agent: Agent) => {
    const ok = await confirm({
      title: "Remove agent",
      message: `Remove ${agent.name} from the roster? This cannot be undone.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await deleteAgent(agent.id);
      await load();
      toast("Agent removed", agent.name, "ok");
    } catch (e) {
      toast("Couldn't remove agent", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const submitTeam = async (input: TeamInput, id?: string) => {
    try {
      if (id) await updateTeam(id, input);
      else await createTeam(input);
      setTeamModal(null);
      await load();
      toast(id ? "Team updated" : "Team created", input.name, "ok");
    } catch (e) {
      toast(id ? "Couldn't update team" : "Couldn't create team", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const submitRole = async (input: RoleInput) => {
    try {
      await createRole(input);
      setRoleModalOpen(false);
      await load();
      toast("Role created", `${input.label} · ${input.skills.length} default skill(s)`, "ok");
    } catch (e) {
      toast("Couldn't create role", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const removeTeam = async (team: Team) => {
    const ok = await confirm({
      title: "Remove team",
      message: `Disband ${team.name}? This cannot be undone.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await deleteTeam(team.id);
      await load();
      toast("Team removed", team.name, "ok");
    } catch (e) {
      toast("Couldn't remove team", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const selectedTeam = teams?.find((t) => t.id === selectedTeamId) ?? null;

  return (
    <div>
      <style>{CARD_CSS}</style>

      {selectedTeam ? (
        /* -------- Team org-chart view -------- */
        <>
          <div className="page-header">
            <div style={{ minWidth: 0 }}>
              <button type="button" className="tm-back" onClick={() => setSelectedTeamId(null)}>
                <span style={{ display: "inline-flex", transform: "rotate(180deg)" }}>
                  <Icon name="arrow" size={14} />
                </span>
                All teams
              </button>
              <h1 className="page-title" style={{ marginTop: 8 }}>{selectedTeam.name}</h1>
              <p className="page-desc">
                {selectedTeam.description || "The team's reporting structure — one accountable lead per role, others advise. Click any person to edit."}
              </p>
            </div>
            <div className="page-actions">
              <Button variant="subtle" onClick={() => setTeamModal({ team: selectedTeam })}>
                <Icon name="pen" size={15} /> Edit team
              </Button>
              <Button variant="primary" onClick={() => setAgentModal({ agent: null })}>
                <Icon name="plus" size={16} /> Add agent
              </Button>
            </div>
          </div>
          {error && <ErrorBanner message={error} />}
          <div className="card pad tm-orgcard">
            <OrgChart team={selectedTeam} agentsById={agentsById} onEdit={(a) => setAgentModal({ agent: a })} />
          </div>
        </>
      ) : (
        /* -------- Teams + roster overview -------- */
        <>
          <div className="page-header">
            <div>
              <h1 className="page-title">Team</h1>
              <p className="page-desc">
                Your always-on AI engineering org — organized into teams. Open a team to see its
                reporting structure; manage individual agents in the roster below.
              </p>
            </div>
            <div className="page-actions">
              <Button variant="subtle" onClick={() => setRoleModalOpen(true)}>
                <Icon name="sparkles" size={15} /> Add role
              </Button>
              <Button variant="subtle" onClick={() => setTeamModal({ team: null })}>
                <Icon name="plus" size={15} /> Create team
              </Button>
              <Button variant="primary" onClick={() => setAgentModal({ agent: null })}>
                <Icon name="plus" size={16} /> Add agent
              </Button>
            </div>
          </div>

          {error && <ErrorBanner message={error} />}
          {!agents && !error && <p className="faint">Loading…</p>}

          {teams && (
            <section className="tm-teams">
              <div className="tm-secthead">
                <div className="section-label" style={{ marginBottom: 4 }}>Teams</div>
                <p className="text-xs faint" style={{ margin: 0, maxWidth: 560 }}>
                  Open a team to view its org chart. One Accountable decision-maker per role has the
                  final verdict; others advise.
                </p>
              </div>
              {teams.length > 0 ? (
                <div className="tm-teamgrid">
                  {teams.map((team) => (
                    <TeamCard
                      key={team.id}
                      team={team}
                      agentsById={agentsById}
                      onOpen={(t) => setSelectedTeamId(t.id)}
                      onEdit={(t) => setTeamModal({ team: t })}
                      onRemove={removeTeam}
                    />
                  ))}
                </div>
              ) : (
                <div className="empty">
                  <div className="em-ic">◈</div>
                  No teams yet — create one to staff a project.
                </div>
              )}
            </section>
          )}

          {agents && (
            <>
              <div className="row between wrap tm-rosterlabel">
                <div className="section-label" style={{ margin: 0 }}>Roster</div>
                <div className="segmented" role="tablist" aria-label="Filter agents by group">
                  {GROUPS.map((g) => (
                    <button
                      key={g.key}
                      type="button"
                      role="tab"
                      aria-selected={g.key === group}
                      className={g.key === group ? "active" : undefined}
                      onClick={() => setGroup(g.key)}
                    >
                      {g.label}
                      <span className="tm-cnt">{counts[g.key]}</span>
                    </button>
                  ))}
                </div>
              </div>
              {visible.length === 0 ? (
                <div className="empty">
                  <div className="em-ic">◈</div>
                  No agents in this group yet.
                </div>
              ) : (
                <div className="grid g-3">
                  {visible.map((agent) => (
                    <AgentCard
                      key={agent.id}
                      agent={agent}
                      onEdit={(a) => setAgentModal({ agent: a })}
                      onRemove={removeAgent}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}

      {agentModal && (
        <AgentComposer
          agent={agentModal.agent}
          connections={connections}
          roles={roles}
          onClose={() => setAgentModal(null)}
          onSubmit={submitAgent}
        />
      )}

      {teamModal && (
        <TeamComposer
          team={teamModal.team}
          agents={agents ?? []}
          onClose={() => setTeamModal(null)}
          onSubmit={submitTeam}
        />
      )}

      {roleModalOpen && (
        <RoleComposer onClose={() => setRoleModalOpen(false)} onSubmit={submitRole} />
      )}
    </div>
  );
}

/* One-off card styling the design system does not express (accent bar, hover
   lift, stat grid). Prefixed `tm-` and token-only so it stays theme-correct. */
const CARD_CSS = `
.tm-cnt { opacity: .55; font-weight: 600; margin-left: 6px; }
.tm-card { position: relative; overflow: hidden; animation: tmFade .35s ease both;
  transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease; }
.tm-card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--acc); opacity: .9; }
.tm-card:hover { transform: translateY(-3px); border-color: var(--acc); box-shadow: var(--shadow); }
.tm-top { align-items: flex-start; gap: 12px; }
.tm-id { min-width: 0; flex: 1; }
.tm-name { font-family: var(--display); font-weight: 700; font-size: 16px; letter-spacing: -.01em; }
.tm-role { font-size: 12.5px; color: var(--muted); margin-top: 1px; }
/* Role + level, clearly visible & highlighted (not truncated muted text). */
.tm-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; }
.tm-rolechip { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
  color: var(--rc); background: color-mix(in srgb, var(--rc) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--rc) 32%, transparent); white-space: nowrap; line-height: 1.4; }
.tm-level { font-size: 10.5px; font-weight: 800; padding: 3px 8px; border-radius: 999px;
  letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; line-height: 1.3;
  border: 1px solid transparent; }
.lvl-junior { color: var(--muted); background: color-mix(in srgb, var(--muted) 14%, transparent);
  border-color: color-mix(in srgb, var(--muted) 26%, transparent); }
.lvl-senior { color: var(--blue); background: color-mix(in srgb, var(--blue) 15%, transparent);
  border-color: color-mix(in srgb, var(--blue) 32%, transparent); }
.lvl-principal { color: var(--brand); background: color-mix(in srgb, var(--brand) 15%, transparent);
  border-color: color-mix(in srgb, var(--brand) 34%, transparent); }
.lvl-strategic { color: var(--amber); background: color-mix(in srgb, var(--amber) 17%, transparent);
  border-color: color-mix(in srgb, var(--amber) 36%, transparent); }
.tm-task { margin-top: 14px; font-size: 12.5px; color: var(--muted); }
.tm-task > .truncate { min-width: 0; }
.tm-modelrow { margin-top: 12px; }
.tm-model { font-weight: 600; }
.tm-chips { margin-top: 12px; min-height: 26px; }
.tm-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.tm-stat { text-align: center; }
.tm-sv { font-family: var(--display); font-weight: 800; font-size: 17px; letter-spacing: -.02em; }
.tm-sl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); margin-top: 3px; }
@keyframes tmFade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

/* agent card: status badge + edit/remove actions stacked at the top-right */
.tm-top-end { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex: 0 0 auto; }
.tm-cardactions { opacity: 0; transition: opacity .16s ease; }
.tm-card:hover .tm-cardactions, .tm-card:focus-within .tm-cardactions { opacity: 1; }
.tm-failover { margin-top: 6px; font-size: 11.5px; color: var(--faint); }

/* teams section */
.tm-teams { margin-bottom: 28px; }
.tm-secthead { gap: 12px; margin-bottom: 14px; align-items: flex-start; }
.tm-rosterlabel { margin-bottom: 12px; align-items: center; }

/* hierarchy (org-chart) view — tiers top→bottom, each with a coloured rail */
.tm-hier { display: flex; flex-direction: column; gap: 14px; }
.tm-tier { position: relative; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--panel); padding: 14px 16px 16px 18px; animation: tmFade .35s ease both; }
.tm-tier::before { content: ""; position: absolute; left: 0; top: 12px; bottom: 12px; width: 3px;
  border-radius: 3px; background: var(--tc); opacity: .85; }
.tm-tier-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.tm-tier-name { font-family: var(--display); font-weight: 700; font-size: 14.5px; color: var(--tc); }
.tm-tier-count { font-size: 11px; font-weight: 800; color: var(--muted);
  background: color-mix(in srgb, var(--muted) 13%, transparent); padding: 1px 8px; border-radius: 999px; }
.tm-tier-hint { font-size: 12px; color: var(--faint); }
.tm-tier-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); gap: 10px; }
.tm-node { display: flex; align-items: center; gap: 10px; text-align: left; width: 100%;
  padding: 8px 11px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel-2, var(--panel));
  cursor: pointer; transition: transform .14s ease, border-color .14s ease, box-shadow .14s ease; }
.tm-node:hover { border-color: var(--tc); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
.tm-node-id { display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 2px; }
.tm-node-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.tm-node-name { font-weight: 700; font-size: 13px; }
.tm-node-role { font-size: 11px; font-weight: 600; }
.tm-node-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.tm-level-xs { font-size: 9px; padding: 1px 5px; letter-spacing: .03em; }
/* team cards → open org chart */
.tm-teamgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.tm-teamcard { position: relative; overflow: hidden; background: var(--panel); border: 1px solid var(--line);
  border-radius: 16px; padding: 16px 17px; cursor: pointer; box-shadow: var(--shadow-sm);
  animation: tmFade .35s ease both; transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease; }
.tm-teamcard::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 3px;
  background: linear-gradient(90deg, var(--brand), var(--cyan) 45%, var(--green) 75%, var(--amber)); opacity: .9; }
.tm-teamcard:hover { transform: translateY(-3px); border-color: var(--line-2); box-shadow: var(--shadow); }
.tm-teamactions { opacity: 0; transition: opacity .16s ease; flex: 0 0 auto; }
.tm-teamcard:hover .tm-teamactions, .tm-teamcard:focus-within .tm-teamactions { opacity: 1; }
.tm-avstack { display: flex; margin-top: 16px; padding-left: 3px; }
.tm-av { margin-left: -8px; box-shadow: 0 0 0 2px var(--panel); }
.tm-av:first-child { margin-left: 0; }
.tm-av-more { background: var(--panel-2) !important; color: var(--muted); font-size: 11px; font-weight: 800;
  border: 1px solid var(--line); }
.tm-teamfoot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 15px;
  padding-top: 12px; border-top: 1px solid var(--line); }
.tm-teamfoot > .faint { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tm-team-open { font-size: 11.5px; font-weight: 700; color: var(--brand); white-space: nowrap; flex: 0 0 auto; }
.tm-back { display: inline-flex; align-items: center; gap: 6px; background: none; border: 0; cursor: pointer;
  color: var(--muted); font-size: 12.5px; font-weight: 700; padding: 0; }
.tm-back:hover { color: var(--brand); }

/* org chart — SDLC tiers (plan → build → verify → ship), peers on the same row per tier */
.tm-orgcard { padding: 22px 18px 26px; overflow-x: auto; }
.og-wrap { min-width: fit-content; display: flex; justify-content: center; width: 100%; }
.og-tiers { display: inline-flex; flex-direction: column; align-items: center; }
.og-tier { display: flex; flex-direction: column; align-items: center; }
.og-tierhead { display: inline-flex; align-items: baseline; gap: 8px; margin-bottom: 13px; padding: 5px 14px;
  border-radius: 999px; color: var(--dc); background: color-mix(in srgb, var(--dc) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--dc) 30%, transparent); }
.og-tierlabel { font-size: 11px; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; }
.og-tier-n { font-size: 10px; font-weight: 800; color: var(--muted); background: var(--panel);
  border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; }
.og-tierhint { font-size: 11px; font-weight: 500; color: var(--faint); letter-spacing: 0; text-transform: none; }
.og-tierrow { display: flex; flex-wrap: nowrap; justify-content: center; gap: 12px; }
.og-connector { width: 0; height: 24px; border-left: 2px solid var(--line-2); margin: 13px 0; }
.og-node { position: relative; display: flex; flex-direction: column; align-items: center; gap: 3px; text-align: center;
  width: 176px; padding: 13px 12px 12px; border: 1px solid var(--line); border-radius: 15px; background: var(--panel);
  box-shadow: var(--shadow-sm); cursor: pointer; transition: transform .14s ease, border-color .14s ease, box-shadow .14s ease; }
.og-node:hover { transform: translateY(-2px); border-color: var(--dc, var(--line-2)); box-shadow: var(--shadow); }
.og-node .avatar { width: 40px; height: 40px; font-size: 15px; margin-bottom: 3px; }
.og-name { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 5px; letter-spacing: -.01em; }
.og-role { font-size: 11.5px; font-weight: 600; }
.og-dot { position: absolute; top: 11px; right: 11px; width: 9px; height: 9px; border-radius: 50%; box-shadow: 0 0 0 2px var(--panel); }
.og-node-root { width: 208px; padding: 16px 14px 14px;
  border-color: color-mix(in srgb, var(--brand) 45%, var(--line));
  background: linear-gradient(180deg, color-mix(in srgb, var(--brand) 8%, var(--panel)), var(--panel));
  box-shadow: 0 12px 30px -16px color-mix(in srgb, var(--brand) 65%, transparent); }
.og-node-root .avatar { width: 46px; height: 46px; font-size: 17px; }
/* primary-model chip on each node + its hover tooltip (portal, so scroll never clips it) */
.og-model { margin-top: 7px; max-width: 100%; display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600; color: var(--muted);
  background: var(--panel-2); border: 1px solid var(--line); cursor: help; transition: border-color .14s ease, color .14s ease; }
.og-model > .truncate { max-width: 108px; }
.og-model:hover { border-color: color-mix(in srgb, var(--brand) 45%, var(--line)); color: var(--text); }
.og-model svg { color: var(--brand-2); flex: 0 0 auto; }
.og-model-empty { color: var(--faint); font-style: italic; }
.og-modeln { font-family: var(--mono); font-size: 9.5px; font-weight: 800; color: var(--brand-2);
  background: color-mix(in srgb, var(--brand) 15%, transparent); border-radius: 999px; padding: 0 5px; }
.og-tip { position: fixed; transform: translate(-50%, -100%); z-index: 1000; pointer-events: none;
  min-width: 200px; max-width: 320px; padding: 10px 12px; border-radius: 12px;
  background: var(--panel); border: 1px solid var(--line-2); box-shadow: var(--shadow);
  display: flex; flex-direction: column; gap: 6px; animation: og-tip-in .12s ease; }
.og-tip-below { transform: translate(-50%, 0); animation: og-tip-in-below .12s ease; }
@keyframes og-tip-in { from { opacity: 0; transform: translate(-50%, calc(-100% + 4px)); } }
@keyframes og-tip-in-below { from { opacity: 0; transform: translate(-50%, -4px); } }
.og-tip-head { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700;
  color: var(--muted); padding-bottom: 5px; border-bottom: 1px solid var(--line); }
.og-tip-head svg { color: var(--brand-2); }
.og-tip-row { display: flex; align-items: center; gap: 8px; }
.og-tip-tag { flex: 0 0 54px; font-size: 9.5px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; color: var(--faint); }
.og-tip-primary { color: var(--brand-2); }
.og-tip-model { font-size: 11.5px; color: var(--text); word-break: break-all; }
.og-tip-none { font-size: 11px; color: var(--faint); font-style: italic; }
.og-tip-arrow { position: absolute; bottom: -5px; left: 50%; width: 9px; height: 9px; transform: translateX(-50%) rotate(45deg);
  background: var(--panel); border-right: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }
.og-tip-below .og-tip-arrow { bottom: auto; top: -5px;
  border-right: none; border-bottom: none; border-left: 1px solid var(--line-2); border-top: 1px solid var(--line-2); }
.tm-members { display: flex; flex-direction: column; gap: 2px; margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--line); }
.tm-member { display: flex; align-items: center; gap: 12px; min-width: 0; padding: 9px 8px; border-radius: 12px; transition: background .14s ease; }
.tm-member:hover { background: color-mix(in srgb, var(--muted) 8%, transparent); }
.tm-member-id { display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 5px; }

/* model picker (agent modal) */
.tm-modelpick { display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto; padding: 4px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel-2); }
.tm-modelopt { display: flex; align-items: center; gap: 10px; padding: 7px 9px; border-radius: 9px; cursor: pointer; }
.tm-modelopt:hover { background: var(--panel); }
.tm-modelopt > .mono { flex: 1; min-width: 0; }

/* member picker (team modal) */
.tm-memberpick { display: flex; flex-direction: column; gap: 2px; max-height: 300px; overflow-y: auto; padding: 4px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel-2); }
.tm-pickrow { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 9px; border-radius: 9px; }
.tm-pickrow:hover { background: var(--panel); }
.tm-pickrow > label:first-child { flex: 1; min-width: 0; }
`;
