/**
 * @foundry/api-types — core API shapes for Shipwright.
 *
 * HAND-AUTHORED SEED. These are the manually-maintained wire types the web BFF and
 * client SDK import today. They are the deliberate seed of what will later be
 * **generated from the orchestrator's OpenAPI spec** (see plan doc 03,
 * `03-api-and-events.md`). Until that generator lands, keep these in sync with
 * Canon §5 (entities) and §13.3 (locked enums) by hand.
 *
 * Conventions (Canon §8 / §13):
 *   - JSON on the wire is **camelCase** (the DB is snake_case; the BFF maps).
 *   - IDs are **ULIDs** (opaque sortable strings); user-facing mission keys are `FND-<n>`.
 *   - Timestamps are ISO-8601 UTC strings (`*At`).
 *   - `priority` is exposed as `P0..P3` in JSON (stored `p0..p3`; the BFF maps).
 */

/* =============================================================
   Primitive aliases
   ============================================================= */

/** A ULID (Canon §8) — sortable opaque identifier. */
export type Ulid = string;
/** Per-workspace mission key, `FND-<n>` (Canon §5/§8). */
export type MissionKey = string;
/** ISO-8601 UTC timestamp. */
export type IsoDateTime = string;

/* =============================================================
   Locked enums (Canon §13.3 + §5/§6)
   ============================================================= */

/** Run lifecycle status (Canon §13.3 `run_status`). */
export type RunStatus =
  | "queued"
  | "running"
  | "paused"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled";

/** Step lifecycle status (Canon §13.3 `step_status`; matches the UI). */
export type StepStatus = "queued" | "active" | "done" | "blocked" | "gated";

/** Mission board stage / kanban column (Canon §6). */
export type MissionStage = "backlog" | "spec" | "building" | "qa" | "review" | "shipped";

/** Blocker kind — must match the UI (Canon §6). */
export type BlockerKind =
  | "approval"
  | "question"
  | "limit"
  | "token"
  | "budget"
  | "error"
  | "dependency";

/** Blocker severity (Canon §6): warn (soft) vs block (hard stop). */
export type BlockerSeverity = "warn" | "block";

/** Priority as exposed in JSON (Canon §13.3; stored lowercase `p0..p3`). */
export type Priority = "P0" | "P1" | "P2" | "P3";

/** Mission autonomy level (Canon §1/§6). */
export type Autonomy = "manual" | "assisted" | "supervised" | "autonomous";

/** Where a mission originated (Canon §13.3 `mission_source`). */
export type MissionSource = "jira" | "linear" | "github" | "manual" | "sentry" | "gitlab";

/** Agent role template key (Canon §5). */
export type AgentRoleKey =
  | "ceo"
  | "cto"
  | "pm"
  | "ba"
  | "backend"
  | "frontend"
  | "qa"
  | "devops"
  | "designer"
  | "security"
  | "custom";

/** Live agent status (Canon §5). */
export type AgentStatus = "working" | "review" | "idle" | "blocked";

/** Model provider (Canon §5). */
export type ModelProvider = "anthropic" | "openai" | "google" | "ollama" | "azure" | "custom";

/** Model connection deployment kind (Canon §5). */
export type ModelKind = "cloud" | "local";

/** Provider/integration connection status (Canon §13.3 `connection_status`). */
export type ConnectionStatus = "connected" | "disconnected" | "error" | "expired";

/** Model capability tier (Canon §13.3 `model_tier`). */
export type ModelTier = "frontier" | "balanced" | "fast" | "local";

/** Skill category (Canon §13.3 `skill_category`). */
export type SkillCategory =
  | "product"
  | "engineering"
  | "quality"
  | "security"
  | "devops"
  | "design"
  | "other";

/** Where a skill came from (Canon §5). */
export type SkillSource = "built-in" | "custom" | "marketplace";

/** Memory fact type (Canon §5). */
export type MemoryType = "project" | "feedback" | "reference" | "user";

/* =============================================================
   Core entities (Canon §5) — camelCase wire shapes
   ============================================================= */

/** A unit of work — ticket/task (Canon §5). */
export interface Mission {
  id: Ulid;
  key: MissionKey;
  workspaceId: Ulid;
  title: string;
  summary: string | null;
  source: MissionSource;
  /** External reference (e.g. Jira issue key) when imported. */
  extRef: string | null;
  priority: Priority;
  stage: MissionStage;
  autonomy: Autonomy;
  /** 0–100 completion of the active run. */
  progress: number;
  repoId: Ulid | null;
  branch: string | null;
  prUrl: string | null;
  labels: string[];
  createdAt: IsoDateTime;
  updatedAt: IsoDateTime;
}

/** A pause on a mission needing human action (Canon §5/§6). */
export interface Blocker {
  id: Ulid;
  missionId: Ulid;
  kind: BlockerKind;
  detail: string;
  /** Set for `limit`/`token`/`budget` blockers tied to a provider (Canon §13.11). */
  modelConnectionId: Ulid | null;
  severity: BlockerSeverity;
  createdAt: IsoDateTime;
  resolvedAt: IsoDateTime | null;
  resolvedBy: Ulid | null;
}

/** Aggregate stats surfaced on an agent card. */
export interface AgentStats {
  missionsShipped: number;
  successRate: number;
  avgCycleHours: number;
}

/** A team-member instance (Canon §5). */
export interface Agent {
  id: Ulid;
  workspaceId: Ulid;
  name: string;
  roleKey: AgentRoleKey;
  /** Id of the bound ModelConnection (its `modelBinding` in the mockup). */
  modelBinding: Ulid | null;
  status: AgentStatus;
  /** Skill ids installed on this agent. */
  skills: Ulid[];
  systemPrompt: string | null;
  level: number;
  stats: AgentStats;
}

/** A provider configuration (Canon §5). Credentials never cross the wire — only a ref. */
export interface ModelConnection {
  id: Ulid;
  workspaceId: Ulid;
  provider: ModelProvider;
  kind: ModelKind;
  /** Model ids exposed by this connection. */
  models: string[];
  /** Present for local/self-hosted (e.g. Ollama) connections. */
  endpoint: string | null;
  /** Vault pointer (Canon §13.7) — never the secret itself. */
  credentialRef: string | null;
  status: ConnectionStatus;
  isPrimary: boolean;
}

/** A reusable capability (Canon §5). */
export interface Skill {
  id: Ulid;
  workspaceId: Ulid;
  name: string;
  description: string;
  category: SkillCategory;
  source: SkillSource;
  /** Natural-language trigger describing when to auto-invoke. */
  trigger: string | null;
  instructions: string;
  autoInvoke: boolean;
  installed: boolean;
  /** Lifetime invocation count. */
  uses: number;
}

/** A persisted fact with an optional RAG embedding (Canon §5/§13.10). */
export interface Memory {
  id: Ulid;
  workspaceId: Ulid;
  type: MemoryType;
  title: string;
  body: string;
  /** `vector(1536)` — omitted from most API responses; present on detail reads. */
  embedding?: number[] | null;
  /** Ids of linked memories (Canon §13.11 `memory_links`). */
  links: Ulid[];
  updatedAt: IsoDateTime;
}

/* =============================================================
   Envelope + pagination (Canon §13.5)
   ============================================================= */

/** The single error shape for every error response body (Canon §13.5). */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    requestId: string;
    retryable: boolean;
  };
}

/** Cursor-paginated list wrapper (09-frontend.md A17: `?cursor=&limit=`). */
export interface Paginated<T> {
  items: T[];
  /** Opaque cursor for the next page, or `null` at the end. */
  nextCursor: string | null;
}
