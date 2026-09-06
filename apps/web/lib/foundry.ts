/**
 * Domain client for the Shipwright orchestrator (missions, runs, blockers, approvals).
 *
 * Thin typed wrappers over `api` (lib/api.ts). Paths are the canonical §13.4 routes under
 * `/api/v1`. Types mirror the camelCase wire shapes (Canon §5/§13.3). These local types are
 * the seed of what `@foundry/api-types` will publish generated from the OpenAPI spec (doc 03).
 */
import { api } from "./api";
import { getApiBase } from "./env";

export type MissionStage = "backlog" | "spec" | "building" | "review" | "qa" | "shipped" | "stopped";
export type RunStatus = "queued" | "running" | "paused" | "blocked" | "succeeded" | "failed" | "cancelled";
export type StepStatus = "queued" | "active" | "done" | "blocked" | "gated";
export type Priority = "p0" | "p1" | "p2" | "p3";

export interface Mission {
  id: string;
  key: string;
  workspaceId: string;
  teamId?: string | null; // the Team staffing this mission (null → org roster)
  title: string;
  summary?: string | null;
  source: string;
  priority: Priority;
  stage: MissionStage;
  autonomy: string;
  progress: number;
  branch?: string | null;
  prUrl?: string | null;
  labels: string[];
  isBlocked: boolean;
  projectKind?: string | null; // "app" (build new) | "change" (edit existing) | null (legacy)
  projectPath?: string | null; // where a greenfield app is built on disk
  requirements?: string | null; // full brief (may include uploaded doc + clarifications)
  lastRunStatus?: string | null; // status of the most recent run — drives the "Stopped" column
  repoName?: string | null; // GitHub repo "owner/name" this mission targets
  repoUrl?: string | null; // link to the repo root
  codeUrl?: string | null; // link to see the code (PR/branch if pushed, else the repo)
  updatedAt?: string | null;
}

export interface Run {
  id: string;
  missionId: string;
  workspaceId: string;
  status: RunStatus;
  autonomy: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  costCents: number;
  tokensIn: number;
  tokensOut: number;
  provider?: string | null; // LLM provider this run executed on (recorded at run time)
  model?: string | null; // concrete model id this run used
  error?: string | null;
}

export interface Step {
  id: string;
  runId: string;
  phase: string;
  title: string;
  agentRole?: string | null;
  status: StepStatus;
  detail?: string | null;
}

export interface EventItem {
  id: string;
  runId?: string | null;
  missionId?: string | null;
  agentRole?: string | null;
  type: string;
  text: string;
  payload: Record<string, unknown>;
  ts?: string | null;
}

export interface Blocker {
  id: string;
  missionId: string;
  kind: string;
  severity: string;
  detail: string;
  /** When the blocker was raised (ISO) — drives the "blocked 4m ago" hint in the UI. */
  createdAt?: string | null;
  resolvedAt?: string | null;
}

const V1 = "/api/v1";

export interface CreateMissionInput {
  title: string;
  summary?: string;
  source?: string; // jira | linear | github | manual
  priority?: string; // P0..P3
  autonomy?: string; // manual | assisted | supervised | autonomous
  teamId?: string; // Team that staffs the mission (omit for the org roster)
  projectKind?: string; // "app" to build a new project, "change" to edit an existing one
  projectPath?: string; // where to build/edit (greenfield: the new app directory)
  requirements?: string; // full brief (e.g. from an uploaded .docx/.md)
}

/**
 * Fields the `PATCH /missions/{key}` endpoint accepts — every one optional (a partial edit).
 * Send ONLY the fields that changed. The server rejects the edit with 422 while a run is active
 * ("cannot edit … stop it first") or when `title` is emptied. Values are camelCase on the wire.
 */
export interface UpdateMissionInput {
  title?: string;
  summary?: string;
  priority?: string; // "P0".."P3"
  autonomy?: string; // an AutonomyLevel string (manual | assisted | supervised | autonomous)
  labels?: string[];
  projectKind?: string | null; // "app" | "change" | null
  projectPath?: string | null;
  requirements?: string;
  teamId?: string | null;
  extRef?: string | null;
}

/** Extract plain-text requirements from an uploaded .docx/.md/.txt (for the New Mission form). */
export interface ExtractedRequirements {
  filename: string;
  text: string;
  chars: number;
}
export async function extractRequirements(file: File): Promise<ExtractedRequirements> {
  const { getApiBase } = await import("./env");
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${getApiBase()}${V1}/missions/extract-requirements`, {
    method: "POST", body: form, headers: { Accept: "application/json" },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = (body as { error?: { message?: string } })?.error?.message ?? "Could not read the file";
    throw new Error(msg);
  }
  return body as ExtractedRequirements;
}

/** Answer the AI's clarifying questions so a paused app build can continue. */
export interface ClarifyAnswer { question: string; answer: string }
export const submitClarification = (key: string, answers: ClarifyAnswer[]) =>
  api.post<{ ok: boolean; blockerId: string; answered: number }>(
    `${V1}/missions/${key}/clarify`, { answers },
  );

export const listMissions = () => api.get<Mission[]>(`${V1}/missions`);
export const getMission = (key: string) => api.get<Mission>(`${V1}/missions/${key}`);
export const createMission = (body: CreateMissionInput) => api.post<Mission>(`${V1}/missions`, body);

/** A registered/built codebase (local git repo) in the project registry. */
export interface Project {
  id: string;
  workspaceId: string;
  name: string;
  slug: string;
  path: string;
  source: "built" | "registered";
  createdAt?: string | null;
  lastActivityAt?: string | null;
}

export const listProjects = () => api.get<Project[]>(`${V1}/projects`);
export const registerProject = (body: { name: string; path: string }) =>
  api.post<Project>(`${V1}/projects`, body);
export const deleteProject = (id: string) => api.del<void>(`${V1}/projects/${id}`);
/** Start one coordinated change across the selected projects; returns the created mission. */
export const startProjectChange = (body: { projectIds: string[]; title: string; request: string }) =>
  api.post<Mission>(`${V1}/projects/change`, body);

/**
 * Edit a mission between builds — pass only the fields that changed (see `UpdateMissionInput`).
 * Returns the updated Mission (with `lastRunStatus`). Throws an `ApiError` with the server's 422
 * message if a run is active or the title is empty.
 */
export async function updateMission(key: string, changes: UpdateMissionInput): Promise<Mission> {
  return api.patch<Mission>(`${V1}/missions/${key}`, changes);
}

/**
 * Delete a mission and all its runs/logs (204, no body). Throws an `ApiError` with the server's
 * 422 message when a run is active — the run must be stopped first.
 */
export async function deleteMission(key: string): Promise<void> {
  return api.del<void>(`${V1}/missions/${key}`);
}

/** Import external tickets (e.g. Jira issues) as real missions. `text` = one issue per line
 * ("PROJ-123 Title") or a pasted JSON export. Returns how many were created. */
export const importMissions = (text: string, source = "jira") =>
  api.post<{ ok: boolean; imported: number; missions: { key: string; title: string; extRef?: string | null }[] }>(
    `${V1}/missions/import`, { source, text },
  );

export const startRun = (key: string) => api.post<Run>(`${V1}/missions/${key}/run`);

/** Live preview: run the built app on a localhost port so it can be opened + tested in a browser. */
export interface PreviewStatus {
  running: boolean;
  url?: string;
  port?: number;
  kind?: string;
}
export const getPreview = (key: string) => api.get<PreviewStatus>(`${V1}/missions/${key}/preview`);
export const startPreview = (key: string) => api.post<PreviewStatus>(`${V1}/missions/${key}/preview`, {});
export const stopPreview = (key: string) => api.del<void>(`${V1}/missions/${key}/preview`);

export const getRun = (id: string) => api.get<Run>(`${V1}/runs/${id}`);
export const listMissionRuns = (key: string) => api.get<Run[]>(`${V1}/missions/${key}/runs`);
export const listSteps = (runId: string) => api.get<Step[]>(`${V1}/runs/${runId}/steps`);
export const listRunEvents = (runId: string) => api.get<EventItem[]>(`${V1}/runs/${runId}/events`);
export const listBlockers = () => api.get<Blocker[]>(`${V1}/blockers`);

export interface Metrics {
  activeMissions: number;
  shipped: number;
  agents: number;
  agentsWorking: number;
  runs: number;
  spendCents: number;
  tokensIn: number;
  tokensOut: number;
  openBlockers: number;
}
export const getMetrics = () => api.get<Metrics>(`${V1}/metrics`);

/** Recent real events across the workspace (newest last) — the Command Center feed source. */
export const listRecentEvents = (limit = 30) =>
  api.get<EventItem[]>(`${V1}/events/recent`, { query: { limit } });

// ---- reference collections (Team / Models / Skills) ----

export interface Agent {
  id: string;
  workspaceId: string;
  name: string;
  roleKey: string;
  modelBinding?: string | null;
  models: string[]; // ordered failover list (primary + fallbacks)
  status: "working" | "review" | "idle" | "blocked";
  level: string;
  skills: string[];
  stats: Record<string, number>;
}

export interface AgentInput {
  name: string;
  roleKey?: string;
  modelBinding?: string;
  models?: string[];
  level?: string;
  skills?: string[];
}
export const createAgent = (body: AgentInput) => api.post<Agent>(`${V1}/agents`, body);
export const updateAgent = (id: string, patch: Partial<AgentInput> & { status?: string }) =>
  api.patch<Agent>(`${V1}/agents/${id}`, patch);
export const deleteAgent = (id: string) => api.del<void>(`${V1}/agents/${id}`);

/** Per-role catalog entry: standard skills (auto-assigned + locked on an agent), scope, a display
 *  label, the org-taxonomy group, and whether it's a user-defined custom role. */
export interface RoleInfo {
  skills: string[];
  scope: string;
  label: string;
  group: string; // exec | product | eng | devops | quality | other
  custom: boolean;
}
export const listRoles = () => api.get<Record<string, RoleInfo>>(`${V1}/roles`);

/** Create a workspace custom role (e.g. Product Coordinator) with its own default skills. */
export interface RoleInput {
  label: string;
  group: string;
  skills: string[];
  scope?: string;
}
export const createRole = (body: RoleInput) => api.post<{ key: string }>(`${V1}/roles`, body);
export const deleteRole = (key: string) => api.del<void>(`${V1}/roles/${key}`);

/** A Team = a named role→agent group staffing a project. One Accountable member per role. */
export interface TeamMember {
  agentId: string;
  accountable: boolean;
}
export interface Team {
  id: string;
  workspaceId: string;
  name: string;
  description?: string | null;
  members: TeamMember[];
  updatedAt?: string | null;
}
export interface TeamInput {
  name: string;
  description?: string;
  members?: TeamMember[];
}
export const listTeams = () => api.get<Team[]>(`${V1}/teams`);
export const createTeam = (body: TeamInput) => api.post<Team>(`${V1}/teams`, body);
export const updateTeam = (id: string, patch: Partial<TeamInput>) =>
  api.patch<Team>(`${V1}/teams/${id}`, patch);
export const deleteTeam = (id: string) => api.del<void>(`${V1}/teams/${id}`);

export interface ModelConnection {
  id: string;
  workspaceId: string;
  provider: string;
  kind: "cloud" | "local";
  models: string[];
  endpoint?: string | null;
  status: "connected" | "disconnected" | "error" | "expired";
  isPrimary: boolean;
  config?: Record<string, unknown>;
}

export interface ModelConnectionPatch {
  status?: string;
  isPrimary?: boolean;
  models?: string[];
  endpoint?: string;
  config?: Record<string, unknown>;
}
export const updateModelConnection = (id: string, patch: ModelConnectionPatch) =>
  api.patch<ModelConnection>(`${V1}/model-connections/${id}`, patch);
export const deleteModelConnection = (id: string) =>
  api.del<void>(`${V1}/model-connections/${id}`);

export interface ModelConnectionInput {
  provider: string;
  kind?: "cloud" | "local";
  models?: string[];
  endpoint?: string;
  config?: Record<string, unknown>;
}
export const createModelConnection = (body: ModelConnectionInput) =>
  api.post<ModelConnection>(`${V1}/model-connections`, body);

/** Providers the engine can run — for the connect UI's provider picker. */
export interface ProviderOption {
  value: string;
  label: string;
  kind: "cloud" | "local";
  needsKey: boolean;
  defaultModels: string[];
  needsEndpoint?: boolean; // generic OpenAI-compatible provider: user supplies the base URL
  cli?: boolean;           // Claude CLI: uses the machine's logged-in seat (no key), picks effort
  efforts?: string[];      // effort levels the CLI accepts (auto + low/medium/high/xhigh/max)
}
export const listProviders = () => api.get<ProviderOption[]>(`${V1}/model-connections/providers`);

export interface ConnectionTestInput {
  provider: string;
  model?: string;
  endpoint?: string;
  apiKey?: string; // transient — used only to test; never stored
}
export interface ConnectionTestResult {
  ok: boolean;
  reason: string;
  detail?: string;
  models?: string[];
}
/** Live connectivity probe for a provider before saving (Ollama /api/tags, Anthropic key check). */
export const testModelConnection = (body: ConnectionTestInput) =>
  api.post<ConnectionTestResult>(`${V1}/model-connections/test`, body);

/** Real per-provider usage, aggregated from what each run actually executed on. */
export interface UsageBucket {
  runs: number;
  tokensIn: number;
  tokensOut: number;
  costCents: number;
}
export interface ProviderUsage extends UsageBucket {
  models: Record<string, UsageBucket>;
}
export interface UsageReport {
  providers: Record<string, ProviderUsage>;
  totals: UsageBucket;
}
export const getModelUsage = () => api.get<UsageReport>(`${V1}/model-connections/usage`);

export interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;
  source: string;
  autoInvoke: boolean;
  installed: boolean;
  uses: number;
}

export interface MemoryItem {
  id: string;
  type: "project" | "feedback" | "reference" | "user";
  title: string;
  body: string;
  links: string[];
  updatedAt?: string | null;
}

export const listAgents = () => api.get<Agent[]>(`${V1}/agents`);
export const listModelConnections = () => api.get<ModelConnection[]>(`${V1}/model-connections`);
export const listSkills = () => api.get<Skill[]>(`${V1}/skills`);

export interface SkillInput {
  name: string;
  description: string;
  category?: string;
  trigger?: string;
  instructions?: string;
  autoInvoke?: boolean;
  source?: string; // "marketplace" for catalog installs
}
export const createSkill = (body: SkillInput) => api.post<Skill>(`${V1}/skills`, body);

/** A curated, installable skill from the marketplace catalog. */
export interface CatalogSkill {
  name: string;
  description: string;
  category: string;
  trigger?: string;
  instructions?: string;
  installed: boolean;
}
export const listSkillCatalog = () => api.get<CatalogSkill[]>(`${V1}/skills/catalog`);
/** Install a catalog skill into the workspace (creates a real Skill record). */
export const installCatalogSkill = (entry: CatalogSkill) =>
  createSkill({
    name: entry.name,
    description: entry.description,
    category: entry.category,
    trigger: entry.trigger,
    instructions: entry.instructions,
    source: "marketplace",
  });
export const updateSkill = (id: string, patch: Partial<SkillInput> & { installed?: boolean }) =>
  api.patch<Skill>(`${V1}/skills/${id}`, patch);
export const listMemory = () => api.get<MemoryItem[]>(`${V1}/memory`);

export interface MemoryInput {
  type: string; // project | feedback | reference | user
  title: string;
  body: string;
  links?: string[];
}
/** Semantic search over workspace memory (the same retrieval the agents use). */
export const searchMemory = (q: string, k = 8) =>
  api.get<MemoryItem[]>(`${V1}/memory/search`, { query: { q, k } });
export const createMemory = (body: MemoryInput) => api.post<MemoryItem>(`${V1}/memory`, body);
export const updateMemory = (id: string, patch: Partial<MemoryInput>) =>
  api.patch<MemoryItem>(`${V1}/memory/${id}`, patch);
export const deleteMemory = (id: string) => api.del<void>(`${V1}/memory/${id}`);

// ---- integrations (3rd-party apps: Jira, GitHub, Chrome for QA, …) ----

export type ConnectionStatus = "connected" | "disconnected" | "error" | "expired";

export interface Integration {
  id: string;
  workspaceId: string;
  kind: string;
  name: string;
  category: string;
  status: ConnectionStatus;
  config: Record<string, unknown>;
}

export const listIntegrations = () => api.get<Integration[]>(`${V1}/integrations`);
/** Connect an integration. Chrome needs no credential; GitHub verifies the token live;
 *  other providers require a credential (not echoed back). */
export const connectIntegration = (kind: string, credential?: string) =>
  api.post<Integration>(`${V1}/integrations/${kind}/connect`, credential ? { credential } : {});
export const disconnectIntegration = (kind: string) =>
  api.post<Integration>(`${V1}/integrations/${kind}/disconnect`);

// ---- GitHub repo / branch pickers (for the merge gate) ----
// The token comes from the connected GitHub integration (stored server-side, never echoed).

export interface GithubRepo {
  fullName: string;
  defaultBranch: string;
  private: boolean;
  pushedAt?: string | null;
}

/** Repos the connected GitHub token can push to. Errors (422) if GitHub isn't connected. */
export const listGithubRepos = () => api.get<GithubRepo[]>(`${V1}/integrations/github/repos`);
/** Branch names for one repo ("owner/name"). Errors (422) if GitHub isn't connected. */
export const listGithubBranches = (repo: string) =>
  api.get<string[]>(`${V1}/integrations/github/branches?repo=${encodeURIComponent(repo)}`);

// ---- tickets (built-in board, plan 05) ----

export type TicketKind = "epic" | "story" | "bug";
export type TicketStatus =
  | "todo" | "in_progress" | "in_review" | "qa" | "done" | "reopened" | "blocked";
export type TicketEventKind =
  | "created" | "transitioned" | "commented" | "evidence_attached"
  | "reopened" | "linked" | "reworked" | "shipped" | "sync_error";
export type TicketLinkType = "blocks" | "relates" | "fixes";

export interface Ticket {
  id: string;
  workspaceId: string;
  key: string;
  kind: TicketKind;
  parentId?: string | null;
  missionId: string;
  subtaskId?: string | null;
  runId?: string | null;
  title: string;
  description: Record<string, unknown>; // {goal, acceptance[], dod[], impl_notes, owned_paths[]}
  status: TicketStatus;
  agentName?: string | null;
  agentRole?: string | null;
  labels: string[];
  priority?: string | null;
  reopenCount: number;
  jiraKey?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface TicketEvent {
  id: string;
  workspaceId: string;
  ticketId: string;
  kind: TicketEventKind;
  fromStatus?: string | null;
  toStatus?: string | null;
  actorName?: string | null;
  actorRole?: string | null;
  body: Record<string, unknown>;
  createdAt?: string | null;
}

export interface TicketLink {
  id: string;
  workspaceId: string;
  fromTicket: string;
  toTicket: string;
  linkType: TicketLinkType;
}

export interface TicketDetail {
  ticket: Ticket;
  events: TicketEvent[];
  links: TicketLink[];
}

export interface Features {
  tickets: boolean;
  jira: boolean;
}

export const listTickets = (missionId?: string) =>
  api.get<Ticket[]>(`${V1}/tickets`, missionId ? { query: { missionId } } : undefined);
export const getTicket = (id: string) => api.get<TicketDetail>(`${V1}/tickets/${id}`);
export const getFeatures = () => api.get<Features>(`${V1}/features`);
export const updateFeatures = (patch: Partial<Features>) =>
  api.put<Features>(`${V1}/features`, patch);

/** Absolute URL to stream an artifact's bytes (screenshots/video/logs) for the evidence gallery. */
export const artifactContentUrl = (artifactId: string) =>
  `${getApiBase()}${V1}/artifacts/${artifactId}/content`;

// ---- autonomy policy (Settings) ----

export type AutonomyLevel = "manual" | "assisted" | "supervised" | "autonomous";

export interface AutonomyPolicy {
  id: string;
  workspaceId: string;
  autonomy: AutonomyLevel;
  gates: Record<string, boolean>;
  spendThresholdCents: number;
  budgetCapCents: number;
  maxParallelAgents: number;
  guardrails: Record<string, boolean>;
  /** Mission-key prefix (e.g. "M" ⇒ M-151); empty ⇒ the server default. */
  missionKeyPrefix: string;
  /** Directory where greenfield apps are built; empty ⇒ the server default (~/ShipwrightProjects). */
  projectsDir: string;
  /** Notifications (email / WhatsApp / Slack) — off by default; credentials live server-side. */
  notifyEnabled: boolean;
  notifyChannels: Record<string, boolean>; // email · whatsapp_twilio · whatsapp_meta · slack
  notifyEvents: Record<string, boolean>; // blocker · completed · failed · approval
  notifyEmail: string;
  notifyWhatsapp: string;
}

/** Fields the PATCH /settings endpoint accepts; each is optional (partial update). */
export type SettingsPatch = Partial<
  Pick<
    AutonomyPolicy,
    | "autonomy"
    | "gates"
    | "spendThresholdCents"
    | "budgetCapCents"
    | "maxParallelAgents"
    | "guardrails"
    | "missionKeyPrefix"
    | "projectsDir"
    | "notifyEnabled"
    | "notifyChannels"
    | "notifyEvents"
    | "notifyEmail"
    | "notifyWhatsapp"
  >
>;

export const getSettings = () => api.get<AutonomyPolicy>(`${V1}/settings`);
export const updateSettings = (patch: SettingsPatch) =>
  api.patch<AutonomyPolicy>(`${V1}/settings`, patch);

/** Ordered autonomy levels with human copy for the Settings dial. */
export const AUTONOMY_LEVELS: { key: AutonomyLevel; label: string; hint: string }[] = [
  { key: "manual", label: "Manual", hint: "Agents draft; a human runs every step." },
  { key: "assisted", label: "Assisted", hint: "Agents act; a human confirms each phase." },
  { key: "supervised", label: "Supervised", hint: "Agents run; humans approve the risky gates." },
  { key: "autonomous", label: "Autonomous", hint: "Agents run end-to-end within guardrails." },
];

/** Approval gates, in display order, with the copy shown next to each toggle. */
export const GATE_COPY: { key: string; label: string; hint: string }[] = [
  { key: "merge", label: "Merge to main", hint: "Require approval before merging a PR." },
  { key: "deploy", label: "Deploy", hint: "Require approval before shipping to an environment." },
  { key: "spend", label: "Spend over threshold", hint: "Ask before a run exceeds the spend threshold." },
  { key: "external", label: "External calls", hint: "Ask before contacting third-party services." },
  { key: "delete", label: "Destructive actions", hint: "Require approval before deletes." },
  { key: "account", label: "Account changes", hint: "Require approval to change account settings." },
];

export const GUARDRAIL_COPY: { key: string; label: string; hint: string }[] = [
  { key: "blockCrossTenant", label: "Block cross-tenant access", hint: "Refuse reads/writes outside the caller's tenant." },
  { key: "requireTests", label: "Require passing tests", hint: "A run cannot ship unless tests pass." },
];

export const MEMORY_TONE: Record<string, "brand" | "amber" | "cyan" | "green"> = {
  project: "brand", feedback: "amber", reference: "cyan", user: "green",
};

export const STATUS_TONE: Record<string, "green" | "blue" | "neutral" | "red"> = {
  working: "green",
  review: "blue",
  idle: "neutral",
  blocked: "red",
};

export interface MergeOptions { note?: string; repo?: string; branch?: string; force?: boolean }
export const decideGate = (key: string, decision: "approve" | "reject", opts: MergeOptions = {}) =>
  api.post<{ ok: boolean; decision: string; gate: string; blockerId: string }>(
    `${V1}/missions/${key}/approvals`,
    { gate: "merge", decision, note: opts.note, repo: opts.repo, branch: opts.branch, force: opts.force },
  );

/**
 * Reopen a shipped mission with a change request. The engine starts a fresh run that iterates
 * on the existing project, so this returns a Run in the same shape as `startRun` (see above) —
 * the caller streams it exactly like a normal build.
 */
export async function requestChange(key: string, request: string): Promise<Run> {
  return api.post<Run>(`${V1}/missions/${key}/change-request`, { request });
}

/**
 * Force-stop the mission's active run. Progress + context (requirements, on-disk project, prior
 * outputs) are preserved so the run can be resumed with `retryRun`. Returns how many runs were
 * stopped. The intended flow: cancel → change the model in Models → retry to continue on it.
 */
export async function cancelRun(key: string): Promise<{ ok: boolean; missionKey: string; stopped: number }> {
  return api.post<{ ok: boolean; missionKey: string; stopped: number }>(`${V1}/missions/${key}/cancel`, {});
}

/**
 * Retry a stopped/terminal run. Resumes from the last completed phase (checkpoint), preserving
 * context, unless `fromStart` redoes the whole pipeline. Returns a Run in the same shape as
 * `startRun` — the caller streams it exactly like a normal build.
 */
export async function retryRun(key: string, fromStart = false): Promise<Run> {
  return api.post<Run>(`${V1}/missions/${key}/retry`, { from_start: fromStart });
}

/** URL for an SSE `EventSource` scoped to one run (Canon §13.4 / doc 03 §18). */
export const runStreamUrl = (runId: string) =>
  `${getApiBase()}${V1}/events/stream?run=${encodeURIComponent(runId)}`;

/** Direct download URL for a run's full JSON bundle (mission + run + steps + events). */
export const runExportUrl = (runId: string) =>
  `${getApiBase()}${V1}/runs/${encodeURIComponent(runId)}/export`;

export const PRIORITY_LABEL: Record<Priority, string> = { p0: "P0", p1: "P1", p2: "P2", p3: "P3" };
