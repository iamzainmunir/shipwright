"use client";

import { Badge, Button, Icon, type BadgeTone, type IconName } from "@foundry/ui";
import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { isApiError } from "@/lib/api";
import { useToast } from "@/components/toast";
import {
  type CatalogSkill,
  type Skill,
  type SkillInput,
  createSkill,
  installCatalogSkill,
  listSkillCatalog,
  listSkills,
  updateSkill,
} from "@/lib/foundry";

/** Category → line-icon glyph (mirrors the mockup's CAT_ICON map). */
const CATEGORY_ICON: Record<string, IconName> = {
  Product: "doc",
  Quality: "flask",
  Engineering: "cpu",
  Security: "shield",
};
const categoryIcon = (category: string): IconName => CATEGORY_ICON[category] ?? "sparkles";

/** Source → tint class (icon chip) + badge tone. */
const SOURCE_TINT: Record<string, string> = {
  "built-in": "tint-cyan",
  custom: "tint-brand",
  marketplace: "tint-blue",
};
const SOURCE_TONE: Record<string, BadgeTone> = {
  "built-in": "cyan",
  custom: "brand",
  marketplace: "blue",
};
const sourceTint = (source: string): string => SOURCE_TINT[source] ?? "tint-brand";
const sourceTone = (source: string): BadgeTone => SOURCE_TONE[source] ?? "brand";

type Tab = "installed" | "marketplace";

/** Active category chip — token-only tint (no hardcoded hex). */
const ACTIVE_CHIP: CSSProperties = {
  background: "color-mix(in srgb, var(--brand) 14%, transparent)",
  color: "var(--brand-2)",
  borderColor: "color-mix(in srgb, var(--brand) 30%, transparent)",
};

// Shared card styling — kept identical across installed skills and catalog entries.
const CARD_ITEM: CSSProperties = { display: "flex", flexDirection: "column", gap: 12, padding: 16, minWidth: 0 };
const CARD_ICON: CSSProperties = { width: 40, height: 40, borderRadius: 12, display: "grid", placeContent: "center", flex: "0 0 40px" };
const CARD_NAME: CSSProperties = { fontSize: 14, letterSpacing: "-.01em", wordBreak: "break-word" };
const CARD_DESC: CSSProperties = { fontSize: 12.8, lineHeight: 1.55, margin: 0, flex: 1 };
const CARD_FOOTER: CSSProperties = { borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: "auto", gap: 10 };
const CARD_GRID: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 288px), 1fr))", gap: 16 };

interface SkillTotals {
  installed: number;
  auto: number;
  uses: number;
  market: number;
}

// --- presentational pieces -------------------------------------------------

interface StatCardProps {
  icon: IconName;
  label: string;
  value: string;
  tint: string;
}

function StatCard({ icon, label, value, tint }: StatCardProps) {
  return (
    <div className="stat">
      <div className={`stat-ic ${tint}`}>
        <Icon name={icon} size={17} />
      </div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

interface SkillCardProps {
  skill: Skill;
  onToggleAuto: (id: string) => void;
}

/** Card footer for an installed skill: real usage count + auto-invoke switch. */
function SkillFooter({ skill, onToggleAuto }: SkillCardProps) {
  return (
    <div className="row between" style={CARD_FOOTER}>
      <span className="text-xs faint">
        {skill.uses === 0
          ? "Not invoked yet"
          : `${skill.uses.toLocaleString()} ${skill.uses === 1 ? "invocation" : "invocations"}`}
      </span>
      <div className="row gap-8">
        <span className="text-xs faint">Auto</span>
        <label className="switch" aria-label={`Auto-invoke ${skill.name}`}>
          <input
            type="checkbox"
            checked={skill.autoInvoke}
            onChange={() => onToggleAuto(skill.id)}
          />
          <span className="track" />
          <span className="thumb" />
        </label>
      </div>
    </div>
  );
}

function SkillCard({ skill, onToggleAuto }: SkillCardProps) {
  return (
    <div className="card hover" style={CARD_ITEM}>
      <div className="row wrap" style={{ alignItems: "flex-start", gap: 12 }}>
        <div className={sourceTint(skill.source)} style={CARD_ICON}>
          <Icon name={categoryIcon(skill.category)} size={19} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row wrap gap-8">
            <span className="mono fw-7" style={CARD_NAME}>
              {skill.name}
            </span>
            <Badge tone={sourceTone(skill.source)}>{skill.source}</Badge>
          </div>
          <div style={{ marginTop: 8 }}>
            <span className="chip">
              <Icon name={categoryIcon(skill.category)} size={12} /> {skill.category}
            </span>
          </div>
        </div>
      </div>
      <p className="muted" style={CARD_DESC}>
        {skill.description}
      </p>
      <SkillFooter skill={skill} onToggleAuto={onToggleAuto} />
    </div>
  );
}

interface CatalogCardProps {
  entry: CatalogSkill;
  onInstall: (entry: CatalogSkill) => void;
  onDetails: (entry: CatalogSkill) => void;
}

/** Marketplace card — a curated catalog skill the user can install or inspect. */
function CatalogCard({ entry, onInstall, onDetails }: CatalogCardProps) {
  return (
    <div className="card hover" style={CARD_ITEM}>
      <div className="row wrap" style={{ alignItems: "flex-start", gap: 12 }}>
        <div className={sourceTint("marketplace")} style={CARD_ICON}>
          <Icon name={categoryIcon(entry.category)} size={19} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row wrap gap-8">
            <button
              type="button"
              className="mono fw-7 link-btn"
              style={{ ...CARD_NAME, textAlign: "left", cursor: "pointer" }}
              onClick={() => onDetails(entry)}
              title={`View ${entry.name} details`}
            >
              {entry.name}
            </button>
            <Badge tone={sourceTone("marketplace")}>marketplace</Badge>
          </div>
          <div style={{ marginTop: 8 }}>
            <span className="chip">
              <Icon name={categoryIcon(entry.category)} size={12} /> {entry.category}
            </span>
          </div>
        </div>
      </div>
      <p className="muted" style={CARD_DESC}>
        {entry.description}
      </p>
      <div className="row between" style={CARD_FOOTER}>
        <button type="button" className="link-btn text-xs fw-6" style={{ cursor: "pointer" }} onClick={() => onDetails(entry)}>
          <Icon name="doc" size={13} /> View details
        </button>
        <Button variant="primary" size="sm" onClick={() => onInstall(entry)}>
          <Icon name="plus" size={14} /> Install
        </Button>
      </div>
    </div>
  );
}

/** Detail modal for a marketplace skill — its real trigger + full instructions. */
function SkillDetailModal({ entry, installed, onClose, onInstall }: {
  entry: CatalogSkill;
  installed: boolean;
  onClose: () => void;
  onInstall: (entry: CatalogSkill) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" role="dialog" aria-modal aria-label={`${entry.name} details`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}>
            <Icon name={categoryIcon(entry.category)} size={16} /> <span className="mono">{entry.name}</span>
          </h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="row wrap gap-8" style={{ marginBottom: 14 }}>
            <Badge tone={sourceTone("marketplace")}>marketplace</Badge>
            <span className="chip"><Icon name={categoryIcon(entry.category)} size={12} /> {entry.category}</span>
            {installed && <Badge tone="green"><span className="dot bg-green" /> Installed</Badge>}
          </div>
          <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6, marginTop: 0 }}>{entry.description}</p>
          {entry.trigger && (
            <div style={{ marginTop: 16 }}>
              <div className="section-label" style={{ margin: "0 0 6px" }}>When it triggers</div>
              <p className="text-sm" style={{ margin: 0, lineHeight: 1.6 }}>{entry.trigger}</p>
            </div>
          )}
          {entry.instructions && (
            <div style={{ marginTop: 16 }}>
              <div className="section-label" style={{ margin: "0 0 6px" }}>What the agent does</div>
              <p className="text-sm" style={{ margin: 0, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{entry.instructions}</p>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {installed ? (
            <Button variant="subtle" disabled><Icon name="check" size={15} /> Installed</Button>
          ) : (
            <Button variant="primary" onClick={() => { onInstall(entry); onClose(); }}>
              <Icon name="plus" size={15} /> Install skill
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

const SEG_COUNT: CSSProperties = { fontVariantNumeric: "tabular-nums", opacity: 0.7, marginLeft: 5 };

interface FiltersProps {
  tab: Tab;
  cat: string;
  query: string;
  categories: string[];
  totals: SkillTotals;
  onTab: (tab: Tab) => void;
  onCat: (cat: string) => void;
  onQuery: (query: string) => void;
}

function SkillFilters({ tab, cat, query, categories, totals, onTab, onCat, onQuery }: FiltersProps) {
  return (
    <div className="row wrap mb-20" style={{ gap: 12 }}>
      <div className="segmented" role="group" aria-label="Skill source">
        <button type="button" aria-pressed={tab === "installed"} className={tab === "installed" ? "active" : ""} onClick={() => onTab("installed")}>
          Installed<span style={SEG_COUNT}>{totals.installed}</span>
        </button>
        <button type="button" aria-pressed={tab === "marketplace"} className={tab === "marketplace" ? "active" : ""} onClick={() => onTab("marketplace")}>
          Marketplace<span style={SEG_COUNT}>{totals.market}</span>
        </button>
      </div>

      <div className="row wrap" style={{ gap: 8 }}>
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            className="chip"
            aria-pressed={cat === c}
            onClick={() => onCat(c)}
            style={{ cursor: "pointer", ...(cat === c ? ACTIVE_CHIP : undefined) }}
          >
            {c !== "All" && <Icon name={categoryIcon(c)} size={12} />} {c}
          </button>
        ))}
      </div>

      <div style={{ position: "relative", marginLeft: "auto", flex: 1, minWidth: 180, maxWidth: 300 }}>
        <span
          style={{
            position: "absolute",
            left: 12,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--faint)",
            display: "grid",
            placeContent: "center",
            pointerEvents: "none",
          }}
        >
          <Icon name="search" size={15} />
        </span>
        <input
          className="input"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search skills…"
          aria-label="Search skills"
          autoComplete="off"
          style={{ paddingLeft: 38 }}
        />
      </div>
    </div>
  );
}

// --- page ------------------------------------------------------------------

export default function SkillsPage() {
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogSkill[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("installed");
  const [cat, setCat] = useState<string>("All");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<CatalogSkill | null>(null);

  const load = useCallback(
    () =>
      Promise.all([listSkills(), listSkillCatalog()])
        .then(([sk, ct]) => {
          setSkills(sk);
          setCatalog(ct);
        })
        .catch((e) => setError(isApiError(e) ? e.message : "Failed to load skills")),
    [],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const categories = useMemo(
    () => [
      "All",
      ...Array.from(
        new Set([...(skills ?? []).map((s) => s.category), ...(catalog ?? []).map((c) => c.category)]),
      ),
    ],
    [skills, catalog],
  );

  const totals = useMemo<SkillTotals>(() => {
    const all = skills ?? [];
    return {
      installed: all.filter((s) => s.installed).length,
      auto: all.filter((s) => s.installed && s.autoInvoke).length,
      uses: all.reduce((sum, s) => sum + s.uses, 0),
      market: (catalog ?? []).filter((c) => !c.installed).length,
    };
  }, [skills, catalog]);

  const installedVisible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (skills ?? [])
      .filter((s) => s.installed)
      .filter((s) => cat === "All" || s.category === cat)
      .filter((s) => !q || `${s.name} ${s.description} ${s.category}`.toLowerCase().includes(q));
  }, [skills, cat, query]);

  const marketVisible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (catalog ?? [])
      .filter((c) => !c.installed)
      .filter((c) => cat === "All" || c.category === cat)
      .filter((c) => !q || `${c.name} ${c.description} ${c.category}`.toLowerCase().includes(q));
  }, [catalog, cat, query]);

  const toggleAuto = async (id: string) => {
    const current = skills?.find((s) => s.id === id);
    if (!current) return;
    const next = !current.autoInvoke;
    setSkills((prev) => prev?.map((s) => (s.id === id ? { ...s, autoInvoke: next } : s)) ?? prev); // optimistic
    try {
      await updateSkill(id, { autoInvoke: next });
    } catch {
      void load(); // revert to server truth on failure
    }
  };

  const installFromCatalog = async (entry: CatalogSkill) => {
    try {
      await installCatalogSkill(entry);
      await load();
      toast("Skill installed", entry.name, "ok");
    } catch (e) {
      toast("Couldn't install skill", isApiError(e) ? e.message : "Try again", "err");
    }
  };

  const createFromDraft = async (draft: SkillInput) => {
    try {
      await createSkill(draft);
      await load();
      setTab("installed");
      setCreating(false);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to create skill");
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Skills</h1>
          <p className="page-desc">Reusable capabilities your agents invoke by name.</p>
        </div>
        <div className="page-actions">
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={16} /> Create skill
          </Button>
        </div>
      </div>

      {error && <div className="lb-error">{error}</div>}
      {!skills && !error && <p style={{ color: "var(--faint)" }}>Loading…</p>}

      {skills && (
        <>
          <div className="grid g-4 mb-20">
            <StatCard icon="sparkles" label="Installed" value={String(totals.installed)} tint="tint-brand" />
            <StatCard icon="bolt" label="Auto-invoked" value={String(totals.auto)} tint="tint-green" />
            <StatCard icon="play" label="Invocations" value={totals.uses.toLocaleString()} tint="tint-cyan" />
            <StatCard icon="plug" label="In marketplace" value={String(totals.market)} tint="tint-blue" />
          </div>

          <SkillFilters
            tab={tab}
            cat={cat}
            query={query}
            categories={categories}
            totals={totals}
            onTab={setTab}
            onCat={setCat}
            onQuery={setQuery}
          />

          {tab === "installed" ? (
            installedVisible.length === 0 ? (
              <div className="empty">
                <div className="em-ic">◈</div>
                No installed skills match your filters.
              </div>
            ) : (
              <div style={CARD_GRID}>
                {installedVisible.map((s) => (
                  <SkillCard key={s.id} skill={s} onToggleAuto={toggleAuto} />
                ))}
              </div>
            )
          ) : marketVisible.length === 0 ? (
            <div className="empty">
              <div className="em-ic">◈</div>
              No marketplace skills match your filters.
            </div>
          ) : (
            <div style={CARD_GRID}>
              {marketVisible.map((entry) => (
                <CatalogCard key={entry.name} entry={entry} onInstall={installFromCatalog} onDetails={setDetail} />
              ))}
            </div>
          )}
        </>
      )}

      {detail && (
        <SkillDetailModal
          entry={detail}
          installed={(skills ?? []).some((s) => s.name === detail.name && s.installed)}
          onClose={() => setDetail(null)}
          onInstall={installFromCatalog}
        />
      )}

      {creating && (
        <SkillComposer
          categories={categories.filter((c) => c !== "All")}
          onClose={() => setCreating(false)}
          onSubmit={createFromDraft}
        />
      )}
    </div>
  );
}

interface ComposerProps {
  categories: string[];
  onClose: () => void;
  onSubmit: (draft: SkillInput) => void;
}

/** Create-skill modal — name, description, category, trigger, instructions, auto-invoke. */
function SkillComposer({ categories, onClose, onSubmit }: ComposerProps) {
  const options = categories.length ? categories : ["engineering", "product", "quality", "security"];
  const [draft, setDraft] = useState<SkillInput>({
    name: "", description: "", category: options[0], trigger: "", instructions: "", autoInvoke: false,
  });
  const patch = <K extends keyof SkillInput>(key: K, value: SkillInput[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = () => {
    if (!draft.name.trim() || !draft.description.trim()) return;
    onSubmit({ ...draft, name: draft.name.trim(), description: draft.description.trim() });
  };

  return (
    <div className="overlay" role="dialog" aria-modal aria-label="Create skill"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}><Icon name="sparkles" size={16} /> Create skill</h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="grid g-2">
            <div className="field">
              <label htmlFor="sk-name">Name</label>
              <input id="sk-name" className="input" autoFocus value={draft.name}
                placeholder="e.g. reconcile-ledger" onChange={(e) => patch("name", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="sk-cat">Category</label>
              <select id="sk-cat" className="select" value={draft.category}
                onChange={(e) => patch("category", e.target.value)}>
                {options.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="sk-desc">Description</label>
            <input id="sk-desc" className="input" value={draft.description}
              placeholder="What this skill does, in one line" onChange={(e) => patch("description", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="sk-trigger">Trigger</label>
            <input id="sk-trigger" className="input" value={draft.trigger ?? ""}
              placeholder="When the agent should reach for it (optional)" onChange={(e) => patch("trigger", e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="sk-inst">Instructions</label>
            <textarea id="sk-inst" className="textarea" value={draft.instructions ?? ""}
              placeholder="Step-by-step guidance the agent follows (optional)"
              onChange={(e) => patch("instructions", e.target.value)} />
          </div>
          <label className="row gap-10 mt-16" style={{ cursor: "pointer" }}>
            <span className="switch">
              <input type="checkbox" checked={draft.autoInvoke}
                onChange={(e) => patch("autoInvoke", e.target.checked)} />
              <span className="track" /><span className="thumb" />
            </span>
            <span className="text-sm">Auto-invoke when the trigger matches</span>
          </label>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!draft.name.trim() || !draft.description.trim()} onClick={save}>
            <Icon name="check" size={15} /> Create skill
          </Button>
        </div>
      </div>
    </div>
  );
}
