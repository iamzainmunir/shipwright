"use client";

import { Badge, Button, Icon } from "@foundry/ui";
import { useCallback, useEffect, useState } from "react";
import { BrandIcon } from "@/components/brand-icon";
import { isApiError } from "@/lib/api";
import {
  type ConnectionStatus,
  type Integration,
  connectIntegration,
  disconnectIntegration,
  listIntegrations,
} from "@/lib/foundry";

/* ------------------------------------------------------------------ *
 * Presentation metadata
 *
 * The API models an integration as { kind, name, category, status }.
 * Glyphs, blurbs and category tints are pure presentation, so they live
 * here — keyed by the real `kind` — rather than being invented per row.
 * ------------------------------------------------------------------ */

type BadgeTone = "green" | "neutral" | "red" | "amber" | "brand" | "blue" | "cyan" | "pink";

const CONNECTION_TONE: Record<ConnectionStatus, BadgeTone> = {
  connected: "green",
  disconnected: "neutral",
  error: "red",
  expired: "amber",
};

const CATEGORY_TINT: Record<string, BadgeTone> = {
  Ticketing: "blue",
  Code: "brand",
  "QA & Runtime": "cyan",
  Comms: "green",
  Design: "pink",
  Observability: "amber",
  Data: "cyan",
  Deploy: "brand",
};

// One-line blurb per integration, keyed by the real `kind`. Icons come from <BrandIcon>.
const INTEGRATION_DESC: Record<string, string> = {
  jira: "Import epics & tickets; sync status back on ship.",
  linear: "Two-way issue sync with cycles & projects.",
  github: "Branches, PRs, checks & squash-merge on approval.",
  gitlab: "MRs, pipelines & protected-branch rules.",
  chrome: "Real browser session for QA to run acceptance stories.",
  slack: "Approvals, standups & escalations in-channel.",
  figma: "Pull frames & tokens into the designer's context.",
  sentry: "Feed prod errors back as candidate missions.",
  postgres: "Read-only verification queries against prod replicas.",
  mongo: "Read-only verification queries against prod replicas.",
  vercel: "Preview deploys per PR, gated behind approval.",
};

function descFor(item: Integration): string {
  return (
    INTEGRATION_DESC[item.kind] ??
    `Connect ${item.name} so the org can act through it across every mission.`
  );
}

function tintFor(category: string): BadgeTone {
  return CATEGORY_TINT[category] ?? "brand";
}

/** Group integrations by category, preserving first-seen order. */
function groupByCategory(items: Integration[]): [string, Integration[]][] {
  const groups = new Map<string, Integration[]>();
  for (const item of items) {
    const list = groups.get(item.category) ?? [];
    list.push(item);
    groups.set(item.category, list);
  }
  return [...groups.entries()];
}

/* ------------------------------------------------------------------ *
 * Small, single-responsibility building blocks
 * ------------------------------------------------------------------ */

interface ToggleProps {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (next: boolean) => void;
}

/** Design-system `.switch` markup (input + track + thumb), controlled. */
function Toggle({ checked, disabled = false, label, onChange }: ToggleProps) {
  return (
    <label className="switch" aria-label={label}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="track" />
      <span className="thumb" />
    </label>
  );
}

function ConnectedBadge() {
  return (
    <span className="badge tint-green">
      <span className="dot bg-green" /> Connected
    </span>
  );
}

interface ConnectFootProps {
  connected: boolean;
  disabled: boolean;
  onConnect: () => void;
}

/** Card footer: a "Connected" badge, or a "Connect →" affordance when off. */
function ConnectFoot({ connected, disabled, onConnect }: ConnectFootProps) {
  if (connected) return <ConnectedBadge />;
  return (
    <button
      type="button"
      className="row gap-6 clickable"
      disabled={disabled}
      onClick={onConnect}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        font: "inherit",
        fontSize: 12.5,
        fontWeight: 600,
        color: "var(--brand-2)",
      }}
    >
      Connect <Icon name="arrow" size={13} />
    </button>
  );
}

/* ------------------------------------------------------------------ *
 * Connection summary — live-derived meter across all integrations
 * ------------------------------------------------------------------ */

interface SummaryProps {
  items: Integration[];
}

function ConnectionSummary({ items }: SummaryProps) {
  const total = items.length;
  const connected = items.filter((i) => i.status === "connected").length;
  const categories = new Set(items.map((i) => i.category)).size;
  const pct = total ? Math.round((connected / total) * 100) : 0;

  return (
    <div className="row wrap gap-20" style={{ alignItems: "center", marginBottom: 22 }}>
      <div
        style={{
          fontFamily: "var(--display)",
          fontWeight: 800,
          fontSize: 34,
          letterSpacing: "-0.02em",
          lineHeight: 1,
        }}
      >
        {connected}
        <span className="faint" style={{ fontSize: 20, fontWeight: 600, marginLeft: 2 }}>
          {" "}
          / {total}
        </span>
      </div>
      <div className="flex-1" style={{ minWidth: 180 }}>
        <div className="fw-7">Connected integrations</div>
        <div className="text-sm faint">
          Across {categories} categories · your org acts through these tools
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 200, maxWidth: 360 }}>
        <div className="meter">
          <span style={{ width: `${pct}%`, background: "var(--grad)" }} />
        </div>
        <div className="text-xs faint mt-4">{pct}% connected</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Featured Chrome runtime card — the connect/disconnect flow with a
 * static description of what the browser QA integration is for.
 * ------------------------------------------------------------------ */

interface ChromeCardProps {
  integration: Integration;
  busy: boolean;
  onToggle: (next: boolean) => void;
}

function ChromeRuntimeCard({ integration, busy, onToggle }: ChromeCardProps) {
  const connected = integration.status === "connected";

  return (
    <div className="card glow" style={{ padding: 24, marginBottom: 28 }}>
      <div className="row gap-20" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <div
          aria-hidden
          style={{
            width: 64,
            height: 64,
            flex: "0 0 64px",
            borderRadius: 16,
            display: "grid",
            placeContent: "center",
            background: "var(--panel-2)",
            border: "1px solid var(--line)",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          <BrandIcon kind={integration.kind} name={integration.name} size={36} />
        </div>

        <div className="flex-1" style={{ minWidth: 260 }}>
          <div className="row between wrap gap-10">
            <div className="row gap-10 wrap" style={{ alignItems: "center" }}>
              <h3 style={{ fontSize: 18, fontFamily: "var(--display)", fontWeight: 800 }}>
                {integration.name} — QA runtime
              </h3>
              <span className="badge tint-cyan">QA &amp; Runtime</span>
              {connected && <ConnectedBadge />}
            </div>
            <Toggle
              checked={connected}
              disabled={busy}
              label={`${connected ? "Disconnect" : "Connect"} ${integration.name}`}
              onChange={onToggle}
            />
          </div>

          <p
            style={{
              color: "var(--muted)",
              fontSize: 13.5,
              lineHeight: 1.6,
              margin: "12px 0 0",
              maxWidth: "66ch",
            }}
          >
            Connect Chrome so QA agents can drive a real browser session to walk acceptance
            stories end-to-end — navigating the staging build, filling forms, and stepping
            through each Given/When/Then scenario, then filing any failure as a defect before
            merge.
          </p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Integration grid card
 * ------------------------------------------------------------------ */

interface IntegrationCardProps {
  item: Integration;
  busy: boolean;
  onToggle: (next: boolean) => void;
}

function IntegrationCard({ item, busy, onToggle }: IntegrationCardProps) {
  const connected = item.status === "connected";
  return (
    <div
      className="card pad"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        position: "relative",
        overflow: "hidden",
      }}
    >
      {connected && (
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: 3,
            background: "var(--grad)",
          }}
        />
      )}
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div
          aria-hidden
          style={{
            width: 44,
            height: 44,
            borderRadius: 12,
            display: "grid",
            placeContent: "center",
            background: "var(--panel-2)",
            border: "1px solid var(--line)",
          }}
        >
          <BrandIcon kind={item.kind} name={item.name} size={26} />
        </div>
        <div className="row gap-8" style={{ alignItems: "center" }}>
          <Badge tone={CONNECTION_TONE[item.status]} dot>
            {item.status}
          </Badge>
          <Toggle
            checked={connected}
            disabled={busy}
            label={`${connected ? "Disconnect" : "Connect"} ${item.name}`}
            onChange={onToggle}
          />
        </div>
      </div>

      <div>
        <div style={{ fontFamily: "var(--display)", fontWeight: 700, fontSize: 15.5 }}>
          {item.name}
        </div>
        <span className={`badge tint-${tintFor(item.category)}`} style={{ marginTop: 6 }}>
          {item.category}
        </span>
      </div>

      <p
        style={{
          fontSize: 12.5,
          color: "var(--muted)",
          lineHeight: 1.55,
          margin: 0,
          flex: 1,
        }}
      >
        {descFor(item)}
      </p>

      <div style={{ marginTop: "auto", minHeight: 24, display: "flex", alignItems: "center" }}>
        <ConnectFoot
          connected={connected}
          disabled={busy}
          onConnect={() => onToggle(true)}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Connect modal — collects a real credential (no more cosmetic toggle)
 * ------------------------------------------------------------------ */

const CRED_HINT: Record<string, { label: string; note: string; docs: string; docsLabel: string }> = {
  github: {
    label: "GitHub personal access token",
    note: "Verified live against the GitHub API; used for real commits & PRs. Needs Repository → Contents: Read and write.",
    docs: "https://github.com/settings/personal-access-tokens/new", docsLabel: "Create a fine-grained token",
  },
  jira: {
    label: "Jira API token", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://id.atlassian.com/manage-profile/security/api-tokens", docsLabel: "Get a Jira API token",
  },
  linear: {
    label: "Linear API key", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://linear.app/settings/api", docsLabel: "Get a Linear API key",
  },
  slack: {
    label: "Slack bot/signing secret", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://api.slack.com/apps", docsLabel: "Get Slack app credentials",
  },
  sentry: {
    label: "Sentry auth token", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://sentry.io/settings/account/api/auth-tokens/", docsLabel: "Get a Sentry auth token",
  },
  figma: {
    label: "Figma personal access token", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://www.figma.com/developers/api#access-tokens", docsLabel: "Get a Figma token",
  },
  vercel: {
    label: "Vercel API token", note: "Stored for this workspace (not live-verified in this scaffold).",
    docs: "https://vercel.com/account/tokens", docsLabel: "Get a Vercel token",
  },
};

function ConnectIntegrationModal({ integration, busy, onClose, onSubmit }: {
  integration: Integration;
  busy: boolean;
  onClose: () => void;
  onSubmit: (credential: string) => void;
}) {
  const [cred, setCred] = useState("");
  const hint = CRED_HINT[integration.kind] ?? { label: "API credential", note: "Required to connect.", docs: "", docsLabel: "" };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" role="dialog" aria-modal aria-label={`Connect ${integration.name}`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card" style={{ maxWidth: 460 }}>
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}><Icon name="plug" size={16} /> Connect {integration.name}</h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}><Icon name="close" size={16} /></button>
        </div>
        <div className="modal-body">
          <div className="field" style={{ marginBottom: 0 }}>
            <div className="row between" style={{ marginBottom: 6 }}>
              <label htmlFor="int-cred" style={{ margin: 0 }}>{hint.label}</label>
              {hint.docs && (
                <a className="link-btn text-xs fw-6" href={hint.docs} target="_blank" rel="noopener noreferrer">
                  <Icon name="external" size={12} /> {hint.docsLabel}
                </a>
              )}
            </div>
            <input id="int-cred" className="input" type="password" autoFocus value={cred}
                   placeholder="Paste your token…" onChange={(e) => setCred(e.target.value)} />
            <span className="hint">{hint.note} The secret is never echoed back by the API.</span>
          </div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy || !cred.trim()} onClick={() => onSubmit(cred.trim())}>
            <Icon name="plug" size={15} /> {busy ? "Connecting…" : "Connect"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export default function IntegrationsPage() {
  const [items, setItems] = useState<Integration[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKind, setBusyKind] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<Integration | null>(null);

  useEffect(() => {
    listIntegrations()
      .then(setItems)
      .catch((e) => setError(isApiError(e) ? e.message : "Failed to load integrations"));
  }, []);

  const toggle = useCallback(async (item: Integration, connect: boolean) => {
    // Connecting a cloud tool needs a real credential → open the modal. Chrome is a local
    // runtime (no key), and disconnecting is always immediate.
    if (connect && item.kind !== "chrome") {
      setConnecting(item);
      return;
    }
    setBusyKind(item.kind);
    setError(null);
    try {
      const updated = connect
        ? await connectIntegration(item.kind)
        : await disconnectIntegration(item.kind);
      setItems((prev) => prev?.map((x) => (x.id === updated.id ? updated : x)) ?? null);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to update integration");
    } finally {
      setBusyKind(null);
    }
  }, []);

  const doConnect = useCallback(async (item: Integration, credential: string) => {
    setBusyKind(item.kind);
    setError(null);
    try {
      const updated = await connectIntegration(item.kind, credential);
      setItems((prev) => prev?.map((x) => (x.id === updated.id ? updated : x)) ?? null);
      setConnecting(null);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to connect");
    } finally {
      setBusyKind(null);
    }
  }, []);

  const chrome = items?.find((i) => i.kind === "chrome") ?? null;
  const others = items?.filter((i) => i.kind !== "chrome") ?? [];

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Integrations</h1>
          <p className="page-desc">
            Connect the tools your AI org works through — ticketing, code, comms, and the real
            browser QA drives to verify flows end-to-end.
          </p>
        </div>
      </div>

      {error && (
        <div
          style={{
            color: "var(--red)",
            background: "color-mix(in srgb, var(--red) 12%, transparent)",
            border: "1px solid color-mix(in srgb, var(--red) 35%, transparent)",
            borderRadius: 12,
            marginBottom: 14,
            padding: "10px 14px",
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {!items && !error && <p className="faint">Loading…</p>}

      {items && (
        <>
          <ConnectionSummary items={items} />

          {chrome && (
            <ChromeRuntimeCard
              integration={chrome}
              busy={busyKind === chrome.kind}
              onToggle={(next) => toggle(chrome, next)}
            />
          )}

          {groupByCategory(others).map(([category, group]) => (
            <section key={category} style={{ marginBottom: 26 }}>
              <div className="section-label">{category}</div>
              <div className="grid g-3">
                {group.map((item) => (
                  <IntegrationCard
                    key={item.id}
                    item={item}
                    busy={busyKind === item.kind}
                    onToggle={(next) => toggle(item, next)}
                  />
                ))}
              </div>
            </section>
          ))}
        </>
      )}

      {connecting && (
        <ConnectIntegrationModal
          integration={connecting}
          busy={busyKind === connecting.kind}
          onClose={() => setConnecting(null)}
          onSubmit={(cred) => doConnect(connecting, cred)}
        />
      )}
    </div>
  );
}
