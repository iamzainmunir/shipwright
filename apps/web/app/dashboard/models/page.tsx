"use client";

import { Badge, Button, Icon, type IconName } from "@foundry/ui";
import { useEffect, useId, useMemo, useState } from "react";
import { isApiError } from "@/lib/api";
import { useConfirm } from "@/components/confirm";
import { useToast } from "@/components/toast";
import {
  type ConnectionTestResult,
  type ModelConnection,
  type ProviderOption,
  type ProviderUsage,
  type UsageReport,
  createModelConnection,
  deleteModelConnection,
  getModelUsage,
  listModelConnections,
  listProviders,
  testModelConnection,
  updateModelConnection,
} from "@/lib/foundry";

/* ------------------------------------------------------------------ */
/* Restrictions live in each connection's `config` (real, user-set).  */
/* Usage is the real per-run metering aggregated by GET .../usage —    */
/* there are no fabricated per-provider profiles on this screen.       */
/* ------------------------------------------------------------------ */

interface Limits {
  monthlyBudget: number; // 0 = no cap
  dailyTokens: number; // 0 = no cap
  rpm: number; // 0 = uncapped
  pauseOnCap: boolean;
  fallback: boolean;
}

const DEFAULT_LIMITS: Limits = { monthlyBudget: 0, dailyTokens: 0, rpm: 0, pauseOnCap: true, fallback: false };
const EMPTY_USAGE: ProviderUsage = { runs: 0, tokensIn: 0, tokensOut: 0, costCents: 0, models: {} };

/** Fallback provider list if the catalog endpoint can't be reached (keeps the panel usable). */
const FALLBACK_PROVIDERS: ProviderOption[] = [
  { value: "anthropic", label: "Anthropic (Claude)", kind: "cloud", needsKey: true, defaultModels: ["claude-sonnet-5"] },
  { value: "claude_cli", label: "Claude CLI (your Claude Code login)", kind: "cloud", needsKey: false, cli: true,
    defaultModels: ["auto", "opus", "sonnet", "haiku"], efforts: ["auto", "low", "medium", "high", "xhigh", "max"] },
  { value: "ollama", label: "Ollama (local)", kind: "local", needsKey: false, defaultModels: ["qwen2.5:7b", "gemma2:2b"] },
];

const CLI_MODELS = ["auto", "opus", "sonnet", "haiku"];
const CLI_EFFORTS = ["auto", "low", "medium", "high", "xhigh", "max"];
const CLI_AUTONOMY: { value: string; label: string; hint: string }[] = [
  { value: "safe", label: "Safe — write files only", hint: "Agents create & edit files, no shell. Recommended." },
  { value: "full", label: "Full — run commands", hint: "Fully autonomous in an isolated git worktree: scaffold, install deps, run tests." },
];
const titleCase = (s: string) => (s === "xhigh" ? "XHigh" : s.charAt(0).toUpperCase() + s.slice(1));

/** Where to get an API key for each cloud provider (shown in the connect panel). */
const PROVIDER_DOCS: Record<string, string> = {
  anthropic: "https://console.anthropic.com/settings/keys",
  openai: "https://platform.openai.com/api-keys",
  google: "https://aistudio.google.com/app/apikey",
  xai: "https://console.x.ai",
  groq: "https://console.groq.com/keys",
  mistral: "https://console.mistral.ai/api-keys",
  deepseek: "https://platform.deepseek.com/api_keys",
  together: "https://api.together.ai/settings/api-keys",
  cohere: "https://dashboard.cohere.com/api-keys",
  openrouter: "https://openrouter.ai/keys",
  perplexity: "https://www.perplexity.ai/settings/api",
  fireworks: "https://fireworks.ai/account/api-keys",
  deepinfra: "https://deepinfra.com/dash/api_keys",
  cerebras: "https://cloud.cerebras.ai",
  nebius: "https://studio.nebius.com/settings/api-keys",
};

const PROVIDER_COLOR: Record<string, string> = {
  anthropic: "var(--amber)", claude_cli: "var(--brand)", ollama: "var(--cyan)", openai: "var(--green)",
  google: "var(--blue)", mistral: "var(--amber)", cohere: "var(--cyan)",
  openrouter: "var(--brand)", perplexity: "var(--blue)", fireworks: "var(--red)",
  deepinfra: "var(--cyan)", cerebras: "var(--amber)", nebius: "var(--brand-2)",
};
const colorFor = (c: ModelConnection) =>
  PROVIDER_COLOR[c.provider.toLowerCase()] ?? (c.kind === "local" ? "var(--cyan)" : "var(--brand-2)");

/* ------------------------------------------------------------------ */
/* Formatting                                                         */
/* ------------------------------------------------------------------ */

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const decimal = new Intl.NumberFormat("en-US");

const money = (cents: number) => usd.format(cents / 100);
// Cost is best-effort: exact when the provider/CLI reports a price, but estimated from a default
// price table for others — so a non-zero figure is prefixed "~". Tokens, by contrast, are exact.
const moneyApprox = (cents: number) => (cents > 0 ? `~${money(cents)}` : money(cents));
const money0 = (dollars: number) => usd0.format(dollars);
const short = (n: number) => compact.format(n);
const num = (n: number) => decimal.format(n);
const clamp = (n: number) => Math.min(100, Math.max(0, n));
const pct = (n: number) => `${Math.round(clamp(n))}%`;
const meterColor = (p: number) => (p > 90 ? "var(--red)" : p >= 70 ? "var(--amber)" : "var(--green)");

/* ------------------------------------------------------------------ */
/* Small presentational pieces                                        */
/* ------------------------------------------------------------------ */

function StatTile({ icon, label, value, note, tint }: { icon: IconName; label: string; value: string; note: string; tint: string }) {
  return (
    <div className="stat">
      <div className={`stat-ic ${tint}`}><Icon name={icon} size={17} /></div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-trend"><span className="faint">{note}</span></div>
    </div>
  );
}

function Swatch({ provider, colorVar }: { provider: string; colorVar: string }) {
  return (
    <div
      style={{
        width: 40, height: 40, flex: "0 0 40px", borderRadius: 12,
        background: "var(--panel-2)", border: "1px solid var(--line)",
        display: "grid", placeContent: "center", color: colorVar,
        fontFamily: "var(--display)", fontWeight: 800, fontSize: 19,
      }}
    >
      {provider.trim().charAt(0).toUpperCase() || "?"}
    </div>
  );
}

function UsageMeter({
  label, used, percent, footL, footR, animate,
}: { label: string; used: string; percent: number; footL: string; footR: string; animate: boolean }) {
  const color = meterColor(percent);
  return (
    <div>
      <div className="row between">
        <span className="text-xs faint fw-6">{label}</span>
        <span className="text-xs fw-6">{used}</span>
      </div>
      <div className="meter mt-8">
        <span style={{ display: "block", height: "100%", width: animate ? `${clamp(percent)}%` : "0%", background: color, transition: "width .8s cubic-bezier(.4,0,.2,1)" }} />
      </div>
      <div className="row between mt-8">
        <span className="text-xs fw-6" style={{ color }}>{footL}</span>
        <span className="text-xs faint">{footR}</span>
      </div>
    </div>
  );
}

function SubGrid({ items }: { items: { k: string; v: string }[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${items.length}, 1fr)`, gap: 1, background: "var(--line)", border: "1px solid var(--line)", borderRadius: 11, overflow: "hidden" }}>
      {items.map((it) => (
        <div key={it.k} style={{ background: "var(--panel)", padding: "10px 12px" }}>
          <div style={{ fontSize: 10.5, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--faint)", fontWeight: 600 }}>{it.k}</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 3 }}>{it.v}</div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Restrictions editor (persists to the connection's config)          */
/* ------------------------------------------------------------------ */

function NumberField({ label, hint, value, step, onChange }: { label: string; hint: string; value: number; step: number; onChange: (n: number) => void }) {
  const id = useId();
  return (
    <div className="field" style={{ marginBottom: 0 }}>
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" type="number" min={0} step={step} value={value} onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))} />
      <div className="hint">{hint}</div>
    </div>
  );
}

function SwitchRow({ title, hint, checked, onChange }: { title: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="row between" style={{ gap: 16, padding: "12px 0", borderTop: "1px solid var(--line)" }}>
      <div>
        <div className="fw-6 text-sm">{title}</div>
        <div className="text-xs faint mt-4">{hint}</div>
      </div>
      <label className="switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="track" />
        <span className="thumb" />
      </label>
    </div>
  );
}

function RestrictionsPanel({ limits, spendCents, isLocal, onCancel, onSave }: { limits: Limits; spendCents: number; isLocal: boolean; onCancel: () => void; onSave: (next: Limits) => void }) {
  const [draft, setDraft] = useState<Limits>(limits);
  const set = (patch: Partial<Limits>) => setDraft((d) => ({ ...d, ...patch }));
  const overCap = !isLocal && draft.monthlyBudget > 0 && spendCents / 100 > draft.monthlyBudget;

  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 16, display: "flex", flexDirection: "column", gap: 15 }}>
      <div className="row gap-8">
        <Icon name="shield" size={14} />
        <span className="fw-7 text-sm">Restrictions</span>
      </div>

      {isLocal ? (
        <div className="text-sm faint">This model runs on your own hardware — there is no budget or rate limit to set.</div>
      ) : (
        <>
          {overCap && (
            <div className="row gap-8" style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--amber)", background: "rgba(246,196,84,.10)", border: "1px solid color-mix(in srgb, var(--amber) 32%, var(--line))", borderRadius: 11, padding: "11px 13px" }}>
              <Icon name="shield" size={14} />
              <span>This cap is below current spend of <b>{money(spendCents)}</b> — the model pauses immediately on save.</span>
            </div>
          )}
          <div className="grid g-2">
            <NumberField label="Monthly budget cap ($)" hint="0 = no cap" value={draft.monthlyBudget} step={10} onChange={(monthlyBudget) => set({ monthlyBudget })} />
            <NumberField label="Daily token cap" hint="0 = no cap" value={draft.dailyTokens} step={1_000_000} onChange={(dailyTokens) => set({ dailyTokens })} />
          </div>
          <NumberField label="Requests per minute (RPM)" hint="Throttle bursts to protect your rate limit. 0 = uncapped." value={draft.rpm} step={100} onChange={(rpm) => set({ rpm })} />
          <SwitchRow title="Pause when the cap is hit" hint="Stop routing work here once the monthly budget is reached." checked={draft.pauseOnCap} onChange={(pauseOnCap) => set({ pauseOnCap })} />
          <SwitchRow title="Fall back to another model on limit" hint="Automatically reroute to the next connected model." checked={draft.fallback} onChange={(fallback) => set({ fallback })} />
        </>
      )}

      <div className="row gap-8 wrap">
        <Button variant="primary" size="sm" onClick={() => onSave(draft)}>Save restrictions</Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Add / connect a model — with a live connectivity test              */
/* ------------------------------------------------------------------ */

function AddConnectionPanel({ existing, providers, editing, onCancel, onDone }: {
  existing: ModelConnection[];
  providers: ProviderOption[];
  editing?: ModelConnection | null;   // when set, edit this connection instead of creating one
  onCancel: () => void;
  onDone: (msg: string) => void;
}) {
  const opts = providers.length ? providers : FALLBACK_PROVIDERS;
  // In edit mode the provider is fixed to the connection being edited.
  const [provider, setProvider] = useState(editing?.provider ?? opts[0]!.value);
  const meta = opts.find((s) => s.value === provider) ?? opts[0]!;
  const isCli = Boolean(meta.cli);
  const isLocal = meta.kind === "local";
  // A generic OpenAI-compatible cloud provider needs a user-supplied base URL, just like local Ollama.
  const needsEndpoint = Boolean(meta.needsEndpoint);
  const showEndpoint = (isLocal || needsEndpoint) && !isCli;
  const savedKey = Boolean((editing?.config as { hasApiKey?: boolean } | undefined)?.hasApiKey);
  const editCfg = (editing?.config ?? {}) as { activeModel?: string; effort?: string; autonomy?: string };

  // Claude CLI settings (driven by the machine's logged-in seat — no API key).
  const [cliModel, setCliModel] = useState(editCfg.activeModel ?? "auto");
  const [cliEffort, setCliEffort] = useState(editCfg.effort ?? "auto");
  const [cliAutonomy, setCliAutonomy] = useState(editCfg.autonomy ?? "safe");

  const [endpoint, setEndpoint] = useState(
    editing?.endpoint ?? (meta.kind === "local" ? "http://localhost:11434" : ""),
  );
  const [apiKey, setApiKey] = useState("");
  const [modelsText, setModelsText] = useState(
    (editing?.models?.length ? editing.models : opts[0]!.defaultModels).join(", "),
  );
  const [budget, setBudget] = useState(
    Number((editing?.config as { monthlyBudget?: number } | undefined)?.monthlyBudget ?? 0),
  );
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ConnectionTestResult | null>(null);
  const [saving, setSaving] = useState(false);

  const pickProvider = (value: string) => {
    setProvider(value);
    setResult(null);
    const next = opts.find((o) => o.value === value);
    setModelsText(next?.defaultModels.join(", ") ?? "");
    // Reset the endpoint to a sensible default for the newly-picked provider's kind.
    setEndpoint(next?.kind === "local" ? "http://localhost:11434" : "");
  };

  // A disconnected connection for this provider is reactivated rather than duplicated.
  const match = existing.find((c) => c.provider.toLowerCase() === provider);
  const firstModel = modelsText.split(",").map((m) => m.trim()).filter(Boolean)[0];

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const r = await testModelConnection({
        provider,
        model: isCli ? undefined : firstModel,
        endpoint: showEndpoint ? endpoint : undefined,
        apiKey: isLocal || isCli ? undefined : apiKey || undefined,
      });
      setResult(r);
      // Ollama reports the models it actually has — offer to fill them in.
      if (r.ok && isLocal && r.models?.length && !modelsText.trim()) {
        setModelsText(r.models.join(", "));
      }
    } catch (e) {
      setResult({ ok: false, reason: isApiError(e) ? e.message : "test failed" });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    // Claude CLI: all four aliases stay bindable per-agent; the picked one is the default (activeModel).
    // Effort + autonomy are the CLI's own knobs. No API key — it uses the machine's logged-in seat.
    if (isCli) {
      const cfg = { activeModel: cliModel, effort: cliEffort, autonomy: cliAutonomy, monthlyBudget: 0, pauseOnCap: false };
      setSaving(true);
      try {
        const target = editing ?? match;
        if (target) {
          await updateModelConnection(target.id, { status: "connected", models: CLI_MODELS, config: cfg });
          onDone(editing ? "Claude CLI updated" : "Claude CLI reconnected");
        } else {
          await createModelConnection({ provider, kind: "cloud", models: CLI_MODELS, config: cfg });
          onDone("Claude CLI connected");
        }
      } catch (e) {
        setResult({ ok: false, reason: isApiError(e) ? e.message : "could not save connection" });
      } finally {
        setSaving(false);
      }
      return;
    }
    const models = modelsText.split(",").map((m) => m.trim()).filter(Boolean);
    const config: Record<string, unknown> = { monthlyBudget: isLocal ? 0 : budget, pauseOnCap: true };
    if (models[0]) config.activeModel = models[0];
    // Persist the API key on the connection so cloud runs can actually use it. The server never
    // echoes it back (redacted from reads → exposed only as `hasApiKey`); it is write-only here.
    if (!isLocal && apiKey.trim()) config.apiKey = apiKey.trim();
    setSaving(true);
    try {
      const target = editing ?? match;  // edit the given connection, else reactivate a match
      if (target) {
        await updateModelConnection(target.id, {
          status: "connected",
          ...(models.length ? { models } : {}),
          ...(showEndpoint ? { endpoint } : {}),
          config,
        });
        onDone(editing ? `${target.provider} updated` : `${target.provider} reconnected`);
      } else {
        await createModelConnection({
          provider, kind: meta.kind, models, endpoint: showEndpoint ? endpoint : undefined, config,
        });
        onDone(`${provider} connected`);
      }
    } catch (e) {
      setResult({ ok: false, reason: isApiError(e) ? e.message : "could not save connection" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card pad mb-20" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="row between">
        <div className="row gap-8"><Icon name="plug" size={16} />
          <span className="fw-7">{editing ? `Edit ${editing.provider} connection` : "Connect a model"}</span>
        </div>
        <Button variant="ghost" size="sm" aria-label="Close" onClick={onCancel}><Icon name="close" size={14} /></Button>
      </div>

      <div className="grid g-3">
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="mc-provider">Provider</label>
          <select id="mc-provider" className="select" value={provider} disabled={Boolean(editing)}
                  onChange={(e) => pickProvider(e.target.value)}>
            {opts.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <div className="hint">{isCli ? "Uses your logged-in Claude Code seat — no API key." : isLocal ? "Runs on your machine — no key." : "Cloud provider — the API key you save here is used for runs (stored write-only, never shown again)."}</div>
        </div>

        {showEndpoint && (
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="mc-endpoint">{isLocal ? "Endpoint URL" : "Base URL"}</label>
            <input id="mc-endpoint" className="input mono" type="text" value={endpoint}
                   placeholder={isLocal ? "http://localhost:11434" : "https://your-host/v1"}
                   onChange={(e) => { setEndpoint(e.target.value); setResult(null); }} />
            <div className="hint">{isLocal ? "e.g. http://localhost:11434" : "OpenAI-compatible base URL, e.g. https://host/v1 (no /chat/completions)"}</div>
          </div>
        )}
        {!isLocal && !isCli && (
          <div className="field" style={{ marginBottom: 0 }}>
            <div className="row between" style={{ marginBottom: 6 }}>
              <label htmlFor="mc-key" style={{ margin: 0 }}>API key</label>
              {PROVIDER_DOCS[provider] && (
                <a className="link-btn text-xs fw-6" href={PROVIDER_DOCS[provider]} target="_blank" rel="noopener noreferrer">
                  <Icon name="external" size={12} /> Get an API key
                </a>
              )}
            </div>
            <input id="mc-key" className="input" type="password"
                   placeholder={savedKey ? "•••••••• saved — leave blank to keep" : "gsk_•••• / sk-••••"}
                   value={apiKey} onChange={(e) => { setApiKey(e.target.value); setResult(null); }} />
            <div className="hint">{savedKey ? "A key is saved. Enter a new one to replace it, or leave blank to keep it." : "Saved on this connection and used for runs (write-only — never shown again)."}</div>
          </div>
        )}

        {isCli ? (
          <>
            <div className="field" style={{ marginBottom: 0 }}>
              <label htmlFor="mc-cli-model">Default model</label>
              <select id="mc-cli-model" className="select" value={cliModel} onChange={(e) => setCliModel(e.target.value)}>
                {(meta.defaultModels?.length ? meta.defaultModels : CLI_MODELS).map((m) => (
                  <option key={m} value={m}>{m === "auto" ? "Auto (recommended)" : titleCase(m)}</option>
                ))}
              </select>
              <div className="hint">All aliases stay assignable per-agent; this is the default. Auto lets Claude Code pick.</div>
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label htmlFor="mc-cli-effort">Effort</label>
              <select id="mc-cli-effort" className="select" value={cliEffort} onChange={(e) => setCliEffort(e.target.value)}>
                {(meta.efforts?.length ? meta.efforts : CLI_EFFORTS).map((eff) => (
                  <option key={eff} value={eff}>{eff === "auto" ? "Auto (recommended)" : titleCase(eff)}</option>
                ))}
              </select>
              <div className="hint">How hard the model thinks per task. Auto adapts to the work.</div>
            </div>
          </>
        ) : (
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="mc-models">Model(s)</label>
            <input id="mc-models" className="input" type="text"
                   placeholder={isLocal ? "qwen2.5:7b, gemma2:2b" : "claude-haiku-4-5, claude-sonnet-5"}
                   value={modelsText} onChange={(e) => setModelsText(e.target.value)} />
            <div className="hint">Comma-separated. {isLocal ? "Test to auto-fill installed models." : ""}</div>
          </div>
        )}
      </div>

      {isCli && (
        <>
          <div className="field" style={{ marginBottom: 0, maxWidth: 420 }}>
            <label htmlFor="mc-cli-autonomy">Build autonomy</label>
            <select id="mc-cli-autonomy" className="select" value={cliAutonomy} onChange={(e) => setCliAutonomy(e.target.value)}>
              {CLI_AUTONOMY.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <div className="hint">{CLI_AUTONOMY.find((a) => a.value === cliAutonomy)?.hint}</div>
          </div>
          <div className="row gap-8" style={{
            fontSize: 12.5, lineHeight: 1.5, color: "var(--muted)", background: "var(--panel-2)",
            border: "1px solid var(--line)", borderRadius: 11, padding: "11px 13px",
          }}>
            <Icon name="shield" size={14} />
            <span>Runs the official <b>claude</b> binary with your subscription seat — no API key stored or sent.
              Test the connection to confirm you&rsquo;re signed in; if not, run <code>claude auth login</code> in a terminal.</span>
          </div>
        </>
      )}

      {!isLocal && !isCli && (
        <div className="field" style={{ marginBottom: 0, maxWidth: 260 }}>
          <label htmlFor="mc-budget">Monthly budget cap ($)</label>
          <input id="mc-budget" className="input" type="number" min={0} step={10} value={budget}
                 onChange={(e) => setBudget(Math.max(0, Number(e.target.value) || 0))} />
          <div className="hint">0 = no cap.</div>
        </div>
      )}

      {match && (
        <div className="text-xs faint row gap-6">
          <Icon name="shield" size={13} />
          {match.status === "connected"
            ? `${match.provider} is already connected — saving updates its models/endpoint.`
            : `${match.provider} exists but is disconnected — saving reconnects it.`}
        </div>
      )}

      {result && (
        <div className="row gap-8" style={{
          fontSize: 12.5, lineHeight: 1.5, borderRadius: 11, padding: "11px 13px",
          color: result.ok ? "var(--green)" : "var(--red)",
          background: result.ok ? "rgba(74,222,128,.10)" : "rgba(248,113,113,.10)",
          border: `1px solid color-mix(in srgb, ${result.ok ? "var(--green)" : "var(--red)"} 32%, var(--line))`,
        }}>
          <Icon name={result.ok ? "check" : "close"} size={14} />
          <span><b>{result.ok ? "Connection OK" : "Test failed"}</b> · {result.reason}{result.detail ? ` — ${result.detail}` : ""}</span>
        </div>
      )}

      <div className="row gap-8 wrap">
        <Button variant="subtle" size="sm" onClick={runTest} disabled={testing}>
          <Icon name="bolt" size={14} /> {testing ? "Testing…" : "Test connection"}
        </Button>
        <Button variant="primary" size="sm" onClick={save} disabled={saving}>
          <Icon name="plug" size={14} /> {saving ? "Saving…" : editing ? "Save changes" : match ? "Reconnect" : "Connect model"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Connected provider card                                            */
/* ------------------------------------------------------------------ */

const STATUS_META: Record<ModelConnection["status"], { tone: "green" | "amber" | "red" | "neutral"; label: string; pulse: boolean }> = {
  connected: { tone: "green", label: "Connected", pulse: true },
  expired: { tone: "amber", label: "Key expired", pulse: false },
  error: { tone: "red", label: "Error", pulse: false },
  disconnected: { tone: "neutral", label: "Disconnected", pulse: false },
};

function restrictionSummary(isLocal: boolean, limits: Limits): string {
  if (isLocal) return "Runs free · local runtime · no budget or rate cap";
  const parts = [
    limits.dailyTokens > 0 ? `${short(limits.dailyTokens)} tokens/day` : "no token cap",
    limits.rpm > 0 ? `${num(limits.rpm)} RPM` : "no rate cap",
    limits.pauseOnCap ? "pauses at cap" : "runs past cap",
  ];
  return parts.join(" · ");
}

interface CardProps {
  conn: ModelConnection;
  usage: ProviderUsage;
  limits: Limits;
  colorVar: string;
  isPrimary: boolean;
  animate: boolean;
  isOpen: boolean;
  onToggle: () => void;
  onSetPrimary: () => void;
  onSaveLimits: (next: Limits) => void;
  onResetUsage: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function ConnectedCard({ conn, usage, limits, colorVar, isPrimary, animate, isOpen, onToggle, onSetPrimary, onSaveLimits, onResetUsage, onEdit, onDelete }: CardProps) {
  const isLocal = conn.kind === "local";
  const isCli = conn.provider === "claude_cli";
  const cliCfg = (conn.config ?? {}) as { activeModel?: string; effort?: string; autonomy?: string };
  const freeRuntime = isLocal || isCli; // no dollar budget: local hardware, or the subscription seat
  const status = STATUS_META[conn.status];
  const hasRuns = usage.runs > 0;

  const budgetPct = limits.monthlyBudget > 0 ? (usage.costCents / 100 / limits.monthlyBudget) * 100 : 0;
  const remaining = limits.monthlyBudget - usage.costCents / 100;

  return (
    <div className={`card${isPrimary ? " glow" : ""}`} style={{ padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
      {/* header */}
      <div className="row between" style={{ alignItems: "flex-start", gap: 12 }}>
        <div className="row" style={{ gap: 13, alignItems: "flex-start", minWidth: 0 }}>
          <Swatch provider={conn.provider} colorVar={colorVar} />
          <div style={{ minWidth: 0 }}>
            <div className="row gap-8 wrap">
              <h3 style={{ fontSize: 15.5, fontWeight: 700, letterSpacing: "-.01em" }}>{conn.provider}</h3>
              {isPrimary && (
                <span className="pill" style={{ color: "var(--amber)", borderColor: "color-mix(in srgb, var(--amber) 40%, var(--line))", background: "rgba(246,196,84,.10)" }}>
                  <Icon name="star" size={12} /> Primary
                </span>
              )}
            </div>
            <div className="li-sub truncate">{conn.models.join(" · ") || "—"}</div>
          </div>
        </div>
        <div className="col" style={{ alignItems: "flex-end", textAlign: "right", flex: "0 0 auto" }}>
          <div className="row gap-6 wrap" style={{ justifyContent: "flex-end" }}>
            {isCli ? (
              <Badge tone="brand"><Icon name="bolt" size={12} /> Claude CLI</Badge>
            ) : isLocal ? (
              <Badge tone="cyan"><Icon name="cpu" size={12} /> Local</Badge>
            ) : (
              <Badge><Icon name="plug" size={12} /> Cloud</Badge>
            )}
            <Badge tone={status.tone}>
              <span className={`dot ${status.pulse ? "pulse " : ""}bg-${status.tone}`} /> {status.label}
            </Badge>
          </div>
          <div className="text-xs faint mt-8">{conn.models.length} model{conn.models.length === 1 ? "" : "s"}</div>
        </div>
      </div>

      {/* real usage / limits */}
      <div style={{ display: "flex", flexDirection: "column", gap: 15 }}>
        {freeRuntime ? (
          <>
            <div>
              <div className="row between">
                <Badge tone={isCli ? "brand" : "green"}>
                  <span className={`dot ${isCli ? "bg-brand" : "bg-green"}`} /> {isCli ? "Runs on your Claude seat" : "Runs free · local"}
                </Badge>
                <span className="text-xs faint">no budget cap</span>
              </div>
              <div className="mono mt-8" style={{ fontSize: 12, color: "var(--muted)", display: "flex", alignItems: "center", gap: 7, background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 9, padding: "8px 10px" }}>
                <Icon name={isCli ? "bolt" : "plug"} size={13} />
                {isCli
                  ? `default ${cliCfg.activeModel || "auto"} · effort ${cliCfg.effort || "auto"} · ${cliCfg.autonomy === "full" ? "full autonomy" : "safe"}`
                  : conn.endpoint || "local runtime"}
              </div>
            </div>
            <SubGrid items={[
              { k: "Runs", v: hasRuns ? num(usage.runs) : "—" },
              { k: "Tokens out", v: hasRuns ? short(usage.tokensOut) : "—" },
              { k: "Tokens in", v: hasRuns ? short(usage.tokensIn) : "—" },
            ]} />
          </>
        ) : (
          <>
            {limits.monthlyBudget > 0 ? (
              <UsageMeter
                label="Spend vs cap" used={`${moneyApprox(usage.costCents)} / ${money0(limits.monthlyBudget)}`} percent={budgetPct}
                footL={`${pct(budgetPct)} used`} footR={remaining >= 0 ? `${money0(remaining)} left` : `${money0(-remaining)} over`} animate={animate}
              />
            ) : (
              <div>
                <div className="row between">
                  <span className="text-xs faint fw-6">Spend to date</span>
                  <span className="text-xs fw-6">{moneyApprox(usage.costCents)}</span>
                </div>
                <div className="text-xs faint mt-8">No cap set — spend is uncapped.</div>
              </div>
            )}
            <SubGrid items={[
              { k: "Runs", v: hasRuns ? num(usage.runs) : "—" },
              { k: "Tokens", v: hasRuns ? `${short(usage.tokensIn)} in · ${short(usage.tokensOut)} out` : "—" },
              { k: "RPM cap", v: limits.rpm > 0 ? num(limits.rpm) : "—" },
            ]} />
          </>
        )}

        {!hasRuns && (
          <div className="text-xs faint row gap-6"><Icon name="shield" size={13} /> No runs on this provider yet — usage appears once a mission runs on it.</div>
        )}

        <div className="row gap-8" style={{ color: "var(--muted)", fontSize: 12 }}>
          <Icon name="shield" size={13} />
          <span className="truncate">{isCli
            ? `Default ${cliCfg.activeModel || "auto"} · effort ${cliCfg.effort || "auto"} · ${cliCfg.autonomy === "full" ? "full autonomy" : "safe (files only)"}`
            : restrictionSummary(isLocal, limits)}</span>
        </div>
      </div>

      {/* actions / restrictions */}
      {isOpen && !isCli ? (
        <RestrictionsPanel limits={limits} spendCents={usage.costCents} isLocal={isLocal} onCancel={onToggle} onSave={onSaveLimits} />
      ) : (
        <div className="row wrap gap-8" style={{ marginTop: "auto", borderTop: "1px solid var(--line)", paddingTop: 14 }}>
          {/* A subscription seat has no dollar budget/RPM to cap — edit its model/effort/autonomy instead. */}
          {!isCli && <Button variant="subtle" size="sm" onClick={onToggle}><Icon name="shield" size={14} /> Set restrictions</Button>}
          <Button variant="ghost" size="sm" onClick={onEdit}><Icon name="pen" size={14} /> Edit</Button>
          {hasRuns && (
            <Button variant="ghost" size="sm" onClick={onResetUsage} data-tip="Zero this provider's cost & token counters"><Icon name="clock" size={14} /> Reset usage</Button>
          )}
          {!isPrimary && (
            <Button variant="ghost" size="sm" onClick={onSetPrimary}><Icon name="star" size={14} /> Set as primary</Button>
          )}
          <Button variant="ghost" size="sm" onClick={onDelete} style={{ marginLeft: "auto", color: "var(--red)" }}>
            <Icon name="close" size={14} /> Remove
          </Button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Disconnected provider = dashed connect card                        */
/* ------------------------------------------------------------------ */

function ConnectCard({ conn, onConnect, onDelete }: { conn: ModelConnection; onConnect: () => void; onDelete: () => void }) {
  return (
    <div className="card" style={{ minHeight: 200, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", gap: 0, padding: 20, borderStyle: "dashed", borderWidth: 1.5, background: "transparent" }}>
      <div style={{ width: 46, height: 46, borderRadius: 14, background: "var(--panel-2)", border: "1px solid var(--line)", display: "grid", placeContent: "center", color: "var(--brand-2)", marginBottom: 14 }}>
        <Icon name="plus" size={22} />
      </div>
      <div style={{ fontSize: 15.5, fontWeight: 700 }}>{conn.provider}</div>
      <div className="text-xs faint mt-8" style={{ maxWidth: "32ch" }}>Not connected · reconnect {conn.provider} to bring it online</div>
      <div className="row gap-8 mt-16">
        <Button variant="subtle" size="sm" onClick={onConnect}><Icon name="plug" size={13} /> Connect provider</Button>
        <Button variant="ghost" size="sm" onClick={onDelete} style={{ color: "var(--red)" }}><Icon name="close" size={13} /> Remove</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Active-model picker                                                */
/* ------------------------------------------------------------------ */

function ActiveModelPicker({ conns, activeConnId, activeModel, onSelect }: {
  conns: ModelConnection[];
  activeConnId: string | null;
  activeModel: string;
  onSelect: (connId: string, model: string) => void;
}) {
  const connected = conns.filter((c) => c.status === "connected");
  const value = activeConnId && activeModel ? `${activeConnId}::${activeModel}` : "";
  return (
    <div className="card pad row between wrap gap-12" style={{ marginBottom: 16 }}>
      <div className="row gap-12">
        <span className="stat-ic" style={{ position: "static", width: 40, height: 40 }}>
          <Icon name="cpu" size={19} />
        </span>
        <div>
          <div className="section-label" style={{ margin: 0 }}>Active model</div>
          <div className="text-sm faint">The model every new mission runs on.</div>
        </div>
      </div>
      <select
        className="select"
        style={{ maxWidth: 340 }}
        value={value}
        aria-label="Active model"
        onChange={(e) => {
          const [connId, model] = e.target.value.split("::");
          if (connId && model) onSelect(connId, model);
        }}
      >
        {connected.length === 0 && <option value="">No connected models</option>}
        {connected.map((c) => (
          <optgroup key={c.id} label={`${c.provider}${c.kind === "local" ? " (local)" : ""}`}>
            {c.models.map((m) => (
              <option key={`${c.id}::${m}`} value={`${c.id}::${m}`}>{m}</option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                               */
/* ------------------------------------------------------------------ */

const limitsFor = (c: ModelConnection): Limits => ({ ...DEFAULT_LIMITS, ...((c.config as Partial<Limits>) ?? {}) });

export default function ModelsPage() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [conns, setConns] = useState<ModelConnection[] | null>(null);
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [primaryId, setPrimaryId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [editingConn, setEditingConn] = useState<ModelConnection | null>(null);
  const [animate, setAnimate] = useState(false);

  const load = () =>
    Promise.all([listModelConnections(), getModelUsage().catch(() => null)])
      .then(([data, use]) => {
        setConns(data);
        setUsage(use);
        setPrimaryId(data.find((c) => c.isPrimary)?.id ?? null);
      })
      .catch((e) => setError(isApiError(e) ? e.message : "Failed to load connections"));

  useEffect(() => {
    void load();
    listProviders().then(setProviders).catch(() => setProviders([]));
  }, []);

  useEffect(() => {
    if (!conns) return;
    const t = setTimeout(() => setAnimate(true), 70);
    return () => clearTimeout(t);
  }, [conns]);

  const usageFor = (conn: ModelConnection): ProviderUsage =>
    usage?.providers[conn.provider.toLowerCase()] ?? usage?.providers[conn.provider] ?? EMPTY_USAGE;

  const totals = useMemo(() => {
    const active = (conns ?? []).filter((c) => c.status === "connected");
    const local = active.filter((c) => c.kind === "local").length;
    return {
      spendCents: usage?.totals.costCents ?? 0,
      tokensIn: usage?.totals.tokensIn ?? 0,
      tokensOut: usage?.totals.tokensOut ?? 0,
      runs: usage?.totals.runs ?? 0,
      connected: active.length,
      local,
    };
  }, [conns, usage]);

  const setPrimary = async (id: string) => {
    setPrimaryId(id);
    setConns((prev) => prev?.map((c) => ({ ...c, isPrimary: c.id === id })) ?? prev);
    try {
      await updateModelConnection(id, { isPrimary: true });
    } catch (e) {
      toast("Couldn't set primary", isApiError(e) ? e.message : "Try again", "err");
      void load();
    }
  };

  const saveLimits = async (id: string, next: Limits) => {
    setConns((prev) => prev?.map((c) => (c.id === id ? { ...c, config: { ...(c.config ?? {}), ...next } } : c)) ?? prev);
    setOpenId(null);
    try {
      await updateModelConnection(id, { config: next as unknown as Record<string, unknown> });
    } catch (e) {
      toast("Couldn't save restrictions", isApiError(e) ? e.message : "Try again", "err");
      void load();
    }
  };

  const resetUsage = async (conn: ModelConnection) => {
    const ok = await confirm({
      title: `Reset usage for ${conn.provider}?`,
      message: "Zeroes this provider's cost and token counters from now on. Runs stay in history; this only resets the displayed totals.",
      confirmLabel: "Reset usage",
    });
    if (!ok) return;
    try {
      // The PATCH merges config server-side, so the saved API key and limits are preserved.
      await updateModelConnection(conn.id, { config: { usageResetAt: new Date().toISOString() } });
      toast("Usage reset", `${conn.provider} counters cleared.`, "ok");
      void load();
    } catch (e) {
      toast("Couldn't reset usage", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const activeConn = (conns ?? []).find((c) => c.id === primaryId && c.status === "connected") ?? null;
  const activeModel = activeConn ? ((activeConn.config?.activeModel as string) ?? activeConn.models[0] ?? "") : "";

  const setActiveModel = async (connId: string, model: string) => {
    const conn = conns?.find((c) => c.id === connId);
    if (!conn) return;
    const nextConfig = { ...(conn.config ?? {}), activeModel: model };
    setPrimaryId(connId);
    setConns((prev) => prev?.map((c) =>
      c.id === connId ? { ...c, isPrimary: true, config: nextConfig } : { ...c, isPrimary: false },
    ) ?? prev);
    try {
      await updateModelConnection(connId, { isPrimary: true, config: nextConfig });
      toast("Active model set", `${conn.provider} · ${model}`, "ok");
    } catch (e) {
      toast("Couldn't set active model", isApiError(e) ? e.message : "Try again", "err");
      void load();
    }
  };

  const removeConn = async (conn: ModelConnection) => {
    const ok = await confirm({
      title: `Remove ${conn.provider}?`,
      message: `This deletes the ${conn.provider} connection from the workspace. You can reconnect it later.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    setConns((prev) => prev?.filter((c) => c.id !== conn.id) ?? prev);
    try {
      await deleteModelConnection(conn.id);
      toast("Connection removed", conn.provider, "ok");
      void load();
    } catch (e) {
      toast("Couldn't remove connection", isApiError(e) ? e.message : "Try again", "err");
      void load();
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Models &amp; Connections</h1>
          <p className="page-desc">
            Connect cloud providers and local models, watch real spend and token usage from your runs, and set hard per-model limits. Local models run free.
          </p>
        </div>
        <div className="page-actions">
          <Button variant="primary" onClick={() => setConnecting(true)}><Icon name="plus" size={16} /> Connect model</Button>
        </div>
      </div>

      {error && <div className="lb-error">{error}</div>}
      {!conns && !error && <p style={{ color: "var(--faint)" }}>Loading…</p>}

      {conns && (
        <>
          <div className="grid g-3 mb-20">
            <StatTile icon="dollar" label="Spend to date" value={moneyApprox(totals.spendCents)} note={`estimated · across ${totals.runs} run${totals.runs === 1 ? "" : "s"}`} tint="tint-green" />
            <StatTile icon="bolt" label="Tokens out" value={short(totals.tokensOut)} note={`${short(totals.tokensIn)} in`} tint="tint-brand" />
            <StatTile icon="plug" label="Connected" value={String(totals.connected)} note={`${totals.local} local · runs free`} tint="tint-cyan" />
          </div>

          <ActiveModelPicker conns={conns} activeConnId={primaryId} activeModel={activeModel} onSelect={setActiveModel} />

          {(connecting || editingConn) && (
            <AddConnectionPanel
              existing={conns}
              providers={providers}
              editing={editingConn}
              onCancel={() => { setConnecting(false); setEditingConn(null); }}
              onDone={(msg) => {
                const wasEditing = Boolean(editingConn);
                setConnecting(false); setEditingConn(null);
                toast(wasEditing ? "Updated" : "Connected", msg, "ok");
                void load();
              }}
            />
          )}

          <div className="section-label">Providers</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 460px), 1fr))", gap: 16, alignItems: "stretch" }}>
            {conns.map((c) =>
              c.status === "disconnected" ? (
                <ConnectCard key={c.id} conn={c} onConnect={() => setConnecting(true)} onDelete={() => removeConn(c)} />
              ) : (
                <ConnectedCard
                  key={c.id}
                  conn={c}
                  usage={usageFor(c)}
                  limits={limitsFor(c)}
                  colorVar={colorFor(c)}
                  isPrimary={primaryId === c.id}
                  animate={animate}
                  isOpen={openId === c.id}
                  onToggle={() => setOpenId((id) => (id === c.id ? null : c.id))}
                  onSetPrimary={() => setPrimary(c.id)}
                  onSaveLimits={(next) => saveLimits(c.id, next)}
                  onResetUsage={() => resetUsage(c)}
                  onEdit={() => { setConnecting(false); setEditingConn(c); }}
                  onDelete={() => removeConn(c)}
                />
              ),
            )}
          </div>
        </>
      )}
    </div>
  );
}
