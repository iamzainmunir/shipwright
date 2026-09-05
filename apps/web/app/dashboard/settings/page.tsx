"use client";

import { Badge, type BadgeTone, Button, Icon, type IconName, cx } from "@foundry/ui";
import { type ReactNode, useEffect, useId, useState } from "react";
import { isApiError } from "@/lib/api";
import { useToast } from "@/components/toast";
import {
  AUTONOMY_LEVELS,
  type AutonomyLevel,
  type AutonomyPolicy,
  type Features,
  GATE_COPY,
  GUARDRAIL_COPY,
  type Metrics,
  type SettingsPatch,
  getFeatures,
  getMetrics,
  getSettings,
  listAgents,
  updateFeatures,
  updateSettings,
} from "@/lib/foundry";

/* ---- money helpers (the API speaks cents; the UI speaks whole dollars) ---- */
const centsToDollars = (cents: number) => Math.round(cents / 100);
const dollarsToCents = (dollars: number) => Math.max(0, Math.round(dollars)) * 100;
const money = (n: number) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const money0 = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;

/* ---- per-level presentation copy shown live under the selector ---- */
interface LevelInfo {
  icon: IconName;
  tone: BadgeTone;
  tag: string;
  body: string;
}
const LEVEL_INFO: Record<AutonomyLevel, LevelInfo> = {
  manual: {
    icon: "shield",
    tone: "blue",
    tag: "Highest oversight",
    body: "The team proposes every step and waits for your go-ahead. Nothing is written, merged, or sent without you clicking approve. All six approval gates stay on.",
  },
  assisted: {
    icon: "pen",
    tone: "cyan",
    tag: "You steer each phase",
    body: "Agents draft the spec and the code, then pause at every phase boundary for your review. Gates are on by default — loosen the ones you trust.",
  },
  supervised: {
    icon: "users",
    tone: "brand",
    tag: "Recommended for P0",
    body: "The team builds and tests autonomously behind the guardrails below. Merge, deploy, and destructive actions hold for your sign-off; routine work flows on its own.",
  },
  autonomous: {
    icon: "rocket",
    tone: "green",
    tag: "Fully hands-off",
    body: "The org ships end-to-end without stopping for approval. Individual gates are handled automatically — safety comes from the hard guardrails below, which always hold.",
  },
};

/* ---- which line icon fronts each approval gate / guardrail ---- */
const GATE_ICON: Record<string, IconName> = {
  merge: "branch",
  deploy: "rocket",
  spend: "dollar",
  external: "external",
  delete: "close",
  account: "gear",
};
const GUARDRAIL_ICON: Record<string, IconName> = {
  blockCrossTenant: "shield",
  requireTests: "flask",
};

/**
 * ToggleSwitch — the design-system `.switch` visual (input + track + thumb),
 * controlled by the parent so the settings draft stays the single source of truth.
 */
function ToggleSwitch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <label className="switch" style={{ marginLeft: 6, flex: "0 0 44px" }}>
      <input
        type="checkbox"
        checked={checked}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="track" />
      <span className="thumb" />
    </label>
  );
}

/**
 * SwitchRow — an icon, a title + hint, an optional trailing badge, and a switch.
 * Used for both approval gates and guardrails; dims itself when switched off.
 */
function SwitchRow({
  icon,
  title,
  hint,
  checked,
  onChange,
  badge,
  extra,
  last = false,
}: {
  icon: IconName;
  title: string;
  hint: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  badge?: ReactNode;
  extra?: ReactNode;
  last?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "15px 2px",
        borderBottom: last ? "none" : "1px solid var(--line)",
        opacity: checked ? 1 : 0.55,
        transition: "opacity .2s",
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 10,
          display: "grid",
          placeContent: "center",
          background: "var(--panel-2)",
          border: "1px solid var(--line)",
          color: "var(--muted)",
          flex: "0 0 auto",
        }}
      >
        <Icon name={icon} size={17} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="li-title">{title}</div>
        <div className="li-sub">{hint}</div>
        {extra}
      </div>
      {badge}
      <ToggleSwitch checked={checked} onChange={onChange} label={title} />
    </div>
  );
}

/** Inline "Threshold: $__ per run" control that lives inside the spend gate row. */
function ThresholdInput({
  cents,
  onChange,
}: {
  cents: number;
  onChange: (cents: number) => void;
}) {
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        marginTop: 8,
        color: "var(--muted)",
        fontSize: 12,
      }}
    >
      Threshold: $
      <input
        className="input"
        type="number"
        min={0}
        step={5}
        aria-label="Spend gate threshold in dollars per run"
        value={centsToDollars(cents)}
        onChange={(e) => onChange(dollarsToCents(Number(e.target.value)))}
        style={{ height: 30, width: 104, padding: "0 10px", fontSize: 12.5 }}
      />
      per run
    </div>
  );
}

/** A `.field` with a leading "$" and a whole-dollar number input backed by cents. */
function MoneyField({
  label,
  cents,
  step,
  onChange,
  children,
}: {
  label: string;
  cents: number;
  step: number;
  onChange: (cents: number) => void;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="field" style={{ marginBottom: 0 }}>
      <label htmlFor={id}>{label}</label>
      <div className="row gap-8">
        <span className="text-sm faint">$</span>
        <input
          id={id}
          className="input"
          type="number"
          min={0}
          step={step}
          value={centsToDollars(cents)}
          onChange={(e) => onChange(dollarsToCents(Number(e.target.value)))}
        />
      </div>
      {children}
    </div>
  );
}

/**
 * Live meter of cumulative spend-to-date against the drafted budget cap.
 * spendCents is all-time, so the copy says "to date" rather than implying a month window.
 * When no cap is set (capCents <= 0) there is nothing to measure against — show the honest
 * spend-to-date total instead of a fabricated "$0 of $0" percentage.
 */
function BudgetMeter({ spentCents, capCents }: { spentCents: number; capCents: number }) {
  const spent = spentCents / 100;
  if (capCents <= 0) {
    return (
      <span className="hint" style={{ marginTop: 6 }}>
        {money(spent)} spent to date · <b>No cap set</b>
      </span>
    );
  }
  const cap = capCents / 100;
  const pct = Math.min(100, (spent / cap) * 100);
  const color = pct > 90 ? "var(--red)" : pct > 70 ? "var(--amber)" : "var(--green)";
  const toneClass = pct > 90 ? "c-red" : pct > 70 ? "c-amber" : "c-green";
  return (
    <>
      <div className="meter mt-8">
        <span style={{ width: `${Math.round(pct)}%`, background: color }} />
      </div>
      <span className="hint" style={{ marginTop: 6 }}>
        {money(spent)} spent to date of {money0(cap)} ·{" "}
        <b className={toneClass}>{Math.round(pct)}% used</b>
      </span>
    </>
  );
}

/**
 * Features — additive product toggles (plan 05 §3). Self-contained: reads/writes the dedicated
 * `/features` endpoint, independent of the autonomy-policy Save button, so flipping the ticket
 * board applies immediately (and backfills Epics server-side on enable).
 */
function FeaturesCard() {
  const { toast } = useToast();
  const [features, setFeatures] = useState<Features | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    getFeatures()
      .then(setFeatures)
      .catch(() => undefined);
  }, []);

  const flip = async (key: keyof Features, next: boolean) => {
    setBusy(key);
    try {
      const updated = await updateFeatures({ [key]: next });
      setFeatures(updated);
      if (key === "tickets") {
        toast(next ? "Ticket board on" : "Ticket board off",
              next ? "Epics backfilled for existing missions" : "The board is hidden", "ok");
      } else if (key === "jira") {
        toast(next ? "Jira mirror on" : "Jira mirror off",
              next ? "Connect Jira on Integrations to activate" : undefined, "ok");
      }
    } catch (e) {
      toast("Couldn't update feature", isApiError(e) ? e.message : "Try again", "err");
    } finally {
      setBusy(null);
    }
  };

  if (!features) return null;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h3>
          <Icon name="ticket" size={16} /> Features
        </h3>
        <span className="text-xs faint">Additive product surfaces you can turn on or off</span>
      </div>
      <div className="card-body" style={{ padding: "6px 18px" }}>
        <SwitchRow
          icon="ticket"
          title="Ticket board"
          hint="A Jira-like board of Epics, Stories and Bugs derived from every run — with QA evidence and a full activity trail. On by default; needs zero setup."
          checked={features.tickets}
          onChange={(next) => void flip("tickets", next)}
          badge={busy === "tickets" ? <span className="text-xs faint">saving…</span> : undefined}
        />
        <SwitchRow
          icon="external"
          title="Jira mirror"
          hint="Mirror those tickets to your company Jira (Epics / Stories / Bugs, with transitions, comments and evidence). Off by default; delivery is fully out-of-band, so Jira never blocks a run. Connect Jira on Integrations to activate."
          checked={features.jira}
          onChange={(next) => void flip("jira", next)}
          badge={busy === "jira" ? <span className="text-xs faint">saving…</span> : undefined}
          last
        />
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [saved, setSaved] = useState<AutonomyPolicy | null>(null);
  const [draft, setDraft] = useState<AutonomyPolicy | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [agentCount, setAgentCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    getSettings()
      .then((policy) => {
        setSaved(policy);
        setDraft(policy);
      })
      .catch((e) => setError(isApiError(e) ? e.message : "Failed to load settings"));
    // Enrichment: the live budget meter and the "team has N agents" hint. Best-effort —
    // the page still works without them, so a failure here never blocks settings.
    getMetrics()
      .then(setMetrics)
      .catch(() => undefined);
    listAgents()
      .then((agents) => setAgentCount(agents.length))
      .catch(() => undefined);
  }, []);

  // Until the policy loads, keep the page-header (consistency with every other screen) and show
  // Loading — or a fatal load error. Save-time errors are shown inline below without unmounting.
  if (!draft || !saved) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Settings</h1>
            <p className="page-desc">Control how much the org does on its own.</p>
          </div>
        </div>
        {error ? <div className="lb-error">{error}</div> : <p style={{ color: "var(--faint)" }}>Loading…</p>}
      </div>
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const activeLevel = AUTONOMY_LEVELS.find((l) => l.key === draft.autonomy);
  const activeIndex = AUTONOMY_LEVELS.findIndex((l) => l.key === draft.autonomy);
  const info = LEVEL_INFO[draft.autonomy];
  // AUTONOMY_LEVELS covers every AutonomyLevel, so this only guards the type.
  if (!activeLevel) return <div className="lb-error">Unknown autonomy level.</div>;
  const gatesOn = GATE_COPY.filter((g) => draft.gates[g.key]).length;
  const gateCountLabel =
    gatesOn === 0
      ? "Handled automatically within guardrails"
      : `${gatesOn} of ${GATE_COPY.length} approval gates required`;
  const maxAgents = Math.max(agentCount ?? 0, draft.maxParallelAgents, 1);

  function edit(changes: Partial<AutonomyPolicy>) {
    setDraft((d) => (d ? { ...d, ...changes } : d));
    setJustSaved(false);
  }

  function discard() {
    setDraft(saved);
    setJustSaved(false);
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    const body: SettingsPatch = {
      autonomy: draft.autonomy,
      gates: draft.gates,
      spendThresholdCents: draft.spendThresholdCents,
      budgetCapCents: draft.budgetCapCents,
      maxParallelAgents: draft.maxParallelAgents,
      guardrails: draft.guardrails,
    };
    try {
      const updated = await updateSettings(body);
      setSaved(updated);
      setDraft(updated);
      setJustSaved(true);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-desc">
            Control how much the org does on its own. New missions inherit this posture; each
            mission can still override it.
          </p>
        </div>
        <div className="page-actions">
          {dirty && !saving && <Badge tone="amber" dot>Unsaved changes</Badge>}
          {!dirty && justSaved && (
            <Badge tone="green">
              <Icon name="check" size={13} /> Saved
            </Badge>
          )}
          <Button variant="ghost" disabled={!dirty || saving} onClick={discard}>
            Discard
          </Button>
          <Button variant="primary" disabled={!dirty || saving} onClick={save}>
            <Icon name="check" size={16} /> {saving ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>

      {error && <div className="lb-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* Section 1 — Global autonomy */}
      <div className="card glow pad" style={{ marginBottom: 16 }}>
        <div className="row between wrap gap-10">
          <div>
            <div className="section-label" style={{ margin: 0 }}>Global autonomy</div>
            <div className="text-xs faint">
              The default posture for every new mission. Individual missions can still override this.
            </div>
          </div>
          <Badge tone={info.tone} dot>{activeLevel.label}</Badge>
        </div>

        <div className="segmented mt-16" style={{ width: "100%" }}>
          {AUTONOMY_LEVELS.map((level) => (
            <button
              key={level.key}
              type="button"
              className={cx(draft.autonomy === level.key && "active")}
              aria-pressed={draft.autonomy === level.key}
              onClick={() => edit({ autonomy: level.key as AutonomyLevel })}
              style={{ flex: 1 }}
            >
              {level.label}
            </button>
          ))}
        </div>

        <div
          style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6, marginTop: 20 }}
        >
          {AUTONOMY_LEVELS.map((level, i) => (
            <div
              key={level.key}
              style={{
                height: 8,
                borderRadius: 20,
                background: i <= activeIndex ? "var(--grad)" : "var(--panel-2)",
                border: i <= activeIndex ? "1px solid transparent" : "1px solid var(--line)",
                transition: ".35s cubic-bezier(.4,0,.2,1)",
              }}
            />
          ))}
        </div>
        <div
          style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6, marginTop: 8 }}
        >
          {AUTONOMY_LEVELS.map((level, i) => (
            <span
              key={level.key}
              style={{
                fontSize: 10,
                letterSpacing: ".08em",
                textTransform: "uppercase",
                textAlign: "center",
                color: i === activeIndex ? "var(--brand-2)" : "var(--faint)",
                fontWeight: i === activeIndex ? 700 : 400,
              }}
            >
              {level.label}
            </span>
          ))}
        </div>

        <p className="text-sm muted" style={{ marginTop: 16, marginBottom: 0 }}>
          {activeLevel.hint}
        </p>

        <div
          style={{
            display: "flex",
            gap: 15,
            padding: "16px 17px",
            borderRadius: 14,
            background: "var(--bg-2)",
            border: "1px solid var(--line)",
            marginTop: 16,
          }}
        >
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: 12,
              display: "grid",
              placeContent: "center",
              color: "var(--brand-2)",
              background: "var(--grad-soft)",
              border: "1px solid var(--line)",
              flex: "0 0 auto",
            }}
          >
            <Icon name={info.icon} size={22} />
          </div>
          <div style={{ flex: 1 }}>
            <div className="row gap-8 wrap">
              <Badge tone={info.tone}>{info.tag}</Badge>
              <Badge>{gateCountLabel}</Badge>
            </div>
            <div
              style={{
                color: "var(--muted)",
                fontSize: 13,
                lineHeight: 1.55,
                marginTop: 7,
                maxWidth: "64ch",
              }}
            >
              {info.body}
            </div>
          </div>
        </div>
      </div>

      {/* Section 2 — Approval gates */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h3>
            <Icon name="shield" size={16} /> Approval gates
          </h3>
          <span className="text-xs faint">Require human sign-off before…</span>
        </div>
        <div className="card-body" style={{ padding: "6px 18px" }}>
          {GATE_COPY.map((gate, i) => (
            <SwitchRow
              key={gate.key}
              icon={GATE_ICON[gate.key] ?? "shield"}
              title={gate.label}
              hint={gate.hint}
              checked={Boolean(draft.gates[gate.key])}
              onChange={(next) => edit({ gates: { ...draft.gates, [gate.key]: next } })}
              last={i === GATE_COPY.length - 1}
              extra={
                gate.key === "spend" ? (
                  <ThresholdInput
                    cents={draft.spendThresholdCents}
                    onChange={(cents) => edit({ spendThresholdCents: cents })}
                  />
                ) : undefined
              }
            />
          ))}
        </div>
      </div>

      {/* Section 3 — Guardrails */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h3>
            <Icon name="bolt" size={16} /> Guardrails
          </h3>
          <span className="text-xs faint">Hard limits that always hold — even on autonomous</span>
        </div>
        <div className="card-body" style={{ padding: "6px 18px" }}>
          {GUARDRAIL_COPY.map((guard, i) => (
            <SwitchRow
              key={guard.key}
              icon={GUARDRAIL_ICON[guard.key] ?? "bolt"}
              title={guard.label}
              hint={guard.hint}
              checked={Boolean(draft.guardrails[guard.key])}
              onChange={(next) => edit({ guardrails: { ...draft.guardrails, [guard.key]: next } })}
              badge={guard.key === "blockCrossTenant" ? <Badge tone="red">Critical</Badge> : undefined}
              last={i === GUARDRAIL_COPY.length - 1}
            />
          ))}
        </div>
      </div>

      {/* Section 4 — Budget */}
      <div className="card">
        <div className="card-head">
          <h3>
            <Icon name="dollar" size={16} /> Budget
          </h3>
          <span className="text-xs faint">Spend limits and how many agents run at once</span>
        </div>
        <div className="card-body pad">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "18px 26px",
            }}
          >
            <MoneyField
              label="Spend threshold (per run)"
              cents={draft.spendThresholdCents}
              step={5}
              onChange={(cents) => edit({ spendThresholdCents: cents })}
            >
              <span className="hint" style={{ marginTop: 6 }}>
                {draft.spendThresholdCents > 0
                  ? "A single run projected over this pauses for approval."
                  : "No threshold set — runs never pause on projected spend."}
              </span>
            </MoneyField>

            <MoneyField
              label="Monthly budget cap"
              cents={draft.budgetCapCents}
              step={50}
              onChange={(cents) => edit({ budgetCapCents: cents })}
            >
              {metrics ? (
                <BudgetMeter spentCents={metrics.spendCents} capCents={draft.budgetCapCents} />
              ) : (
                <span className="hint" style={{ marginTop: 6 }}>
                  The team stops spending once the workspace reaches this for the month.
                </span>
              )}
            </MoneyField>
          </div>

          <div className="divider" />

          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="set-max-agents">Max parallel agents</label>
            <div className="row gap-16">
              <input
                id="set-max-agents"
                type="range"
                min={1}
                max={maxAgents}
                aria-label="Max parallel agents"
                value={draft.maxParallelAgents}
                onChange={(e) => edit({ maxParallelAgents: Number(e.target.value) })}
                style={{ flex: 1, accentColor: "var(--brand)" }}
              />
              <span
                className="c-brand"
                style={{ fontWeight: 800, fontSize: 20, minWidth: 28, textAlign: "right" }}
              >
                {draft.maxParallelAgents}
              </span>
            </div>
            <span className="hint">
              Cap on how many agents run at once across all missions.
              {agentCount != null ? ` The team has ${agentCount} agents.` : ""}
            </span>
          </div>
        </div>
      </div>

      {/* Section 5 — Features (independent of the autonomy-policy Save button) */}
      <div style={{ marginTop: 16 }}>
        <FeaturesCard />
      </div>
    </div>
  );
}
