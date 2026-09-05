"use client";

import { Badge, Button, Icon, type IconName } from "@foundry/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { isApiError } from "@/lib/api";
import {
  MEMORY_TONE,
  type MemoryItem,
  createMemory,
  deleteMemory,
  listMemory,
  searchMemory,
  updateMemory,
} from "@/lib/foundry";

type MemType = MemoryItem["type"];
type Filter = "all" | MemType;

interface TypeMeta {
  label: string;
  tint: string;
  icon: IconName;
  color: string;
  blurb: string;
}

/** Visual language per memory type — mirrors the mockup's colour + icon mapping. */
const TYPE_META: Record<MemType, TypeMeta> = {
  project: { label: "Project", tint: "tint-brand", icon: "doc", color: "var(--brand-2)", blurb: "Facts & rules that govern how the org works." },
  feedback: { label: "Feedback", tint: "tint-amber", icon: "pen", color: "var(--amber)", blurb: "Corrections learned from past reviews." },
  reference: { label: "Reference", tint: "tint-cyan", icon: "external", color: "var(--cyan)", blurb: "Pointers to repos, docs & sources." },
  user: { label: "User", tint: "tint-green", icon: "users", color: "var(--green)", blurb: "Owner preferences & standing choices." },
};

const TYPE_ORDER: MemType[] = ["project", "feedback", "reference", "user"];

const metaFor = (type: MemType): TypeMeta => TYPE_META[type] ?? TYPE_META.project;

/** Render a stored timestamp as compact relative text, tolerating pre-humanised strings. */
function formatUpdated(value: string): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return value;
  const diff = Date.now() - then;
  const min = 60_000;
  const hour = 60 * min;
  const day = 24 * hour;
  const week = 7 * day;
  if (diff < min) return "just now";
  if (diff < hour) return `${Math.floor(diff / min)}m ago`;
  if (diff < day) return `${Math.floor(diff / hour)}h ago`;
  if (diff < week) return `${Math.floor(diff / day)}d ago`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const normaliseTitle = (title: string): string => title.trim().toLowerCase();

// ---- filter chips -----------------------------------------------------------

interface ChipsProps {
  active: Filter;
  counts: Record<MemType, number>;
  total: number;
  onSelect: (next: Filter) => void;
}

function TypeChips({ active, counts, total, onSelect }: ChipsProps) {
  return (
    <div className="mem-chips">
      <button
        type="button"
        className={`mem-chip${active === "all" ? " active" : ""}`}
        onClick={() => onSelect("all")}
      >
        <span className="mc-ic">
          <Icon name="brain" size={13} />
        </span>
        All
        <span className="mc-count">{total}</span>
      </button>
      {TYPE_ORDER.map((type) => {
        const meta = TYPE_META[type];
        return (
          <button
            key={type}
            type="button"
            className={`mem-chip${active === type ? " active" : ""}`}
            data-tip={meta.blurb}
            onClick={() => onSelect(type)}
          >
            <span className={`mc-ic ${meta.tint}`}>
              <Icon name={meta.icon} size={12} />
            </span>
            {meta.label}
            <span className="mc-count">{counts[type]}</span>
          </button>
        );
      })}
    </div>
  );
}

// ---- memory card ------------------------------------------------------------

interface CardProps {
  item: MemoryItem;
  onEdit: (item: MemoryItem) => void;
}

function MemoryCard({ item, onEdit }: CardProps) {
  const meta = metaFor(item.type);
  return (
    <article
      className="mem-card"
      role="button"
      tabIndex={0}
      aria-label={`Edit memory: ${item.title}`}
      style={{ "--edge": meta.color } as React.CSSProperties}
      onClick={() => onEdit(item)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onEdit(item);
        }
      }}
    >
      <div className="row between">
        <Badge tone={MEMORY_TONE[item.type] ?? "neutral"}>
          <Icon name={meta.icon} size={12} />
          {meta.label}
        </Badge>
        <span className="mem-edit" data-tip="Edit" data-tip-below aria-hidden>
          <Icon name="pen" size={14} />
        </span>
      </div>
      <h3 className="mem-title">{item.title}</h3>
      <p className="mem-body">{item.body}</p>
      <div className="mem-foot">
        <span className="mem-updated">
          <Icon name="clock" size={12} />
          {item.updatedAt ? `updated ${formatUpdated(item.updatedAt)}` : "no history"}
        </span>
        {item.links.length > 0 && (
          <div className="mem-links">
            {item.links.map((link) => (
              <span key={link} className="chip">
                <Icon name="sparkles" size={11} />
                {link}
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

// ---- composer (add / edit) --------------------------------------------------

interface Draft {
  type: MemType;
  title: string;
  body: string;
  links: string;
}

interface ComposerProps {
  editing: MemoryItem | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (draft: Draft) => void;
  onDelete: () => void;
}

function Composer({ editing, busy, onClose, onSubmit, onDelete }: ComposerProps) {
  const [draft, setDraft] = useState<Draft>(() => ({
    type: editing?.type ?? "project",
    title: editing?.title ?? "",
    body: editing?.body ?? "",
    links: (editing?.links ?? []).join(", "),
  }));
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const patch = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const save = () => {
    if (!draft.title.trim()) {
      titleRef.current?.focus();
      return;
    }
    onSubmit(draft);
  };

  return (
    <div
      className="overlay"
      role="dialog"
      aria-modal
      aria-label={editing ? "Edit memory" : "Add memory"}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="modal-card">
        <div className="modal-head">
          <h3 className="row" style={{ gap: 9 }}>
            <Icon name="brain" size={16} />
            {editing ? "Edit memory" : "Add memory"}
          </h3>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="modal-body">
          <div className="field">
            <label htmlFor="mem-type">Type</label>
            <select
              id="mem-type"
              className="select"
              value={draft.type}
              onChange={(e) => patch("type", e.target.value as MemType)}
            >
              {TYPE_ORDER.map((type) => (
                <option key={type} value={type}>
                  {TYPE_META[type].label}
                </option>
              ))}
            </select>
            <span className="hint">{metaFor(draft.type).blurb}</span>
          </div>

          <div className="field">
            <label htmlFor="mem-title">Title</label>
            <input
              id="mem-title"
              ref={titleRef}
              className="input"
              placeholder="A short, memorable name for this fact"
              value={draft.title}
              onChange={(e) => patch("title", e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="mem-body">Body</label>
            <textarea
              id="mem-body"
              className="textarea"
              style={{ height: 120, padding: "11px 13px", resize: "vertical" }}
              placeholder="What the org should remember — one idea per sentence."
              value={draft.body}
              onChange={(e) => patch("body", e.target.value)}
            />
          </div>

          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="mem-links">Links</label>
            <input
              id="mem-links"
              className="input"
              placeholder="reconcile, verify"
              value={draft.links}
              onChange={(e) => patch("links", e.target.value)}
            />
            <span className="hint">Comma-separated skills or references this memory relates to.</span>
          </div>
        </div>

        <div className="modal-foot mem-modalfoot">
          <div className="mf-left">
            {editing && (
              <Button variant="danger" disabled={busy} onClick={onDelete}>
                Delete
              </Button>
            )}
            <span className="mf-hint">
              <Icon name="check" size={13} />
              Saved to your workspace &amp; embedded for recall
            </span>
          </div>
          <div className="mf-actions">
            <Button variant="ghost" disabled={busy} onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy} onClick={save}>
              <Icon name="check" size={15} />
              {busy ? "Saving…" : editing ? "Save changes" : "Add memory"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- page -------------------------------------------------------------------

export default function MemoryPage() {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [results, setResults] = useState<MemoryItem[] | null>(null); // semantic hits (query mode)
  const [busy, setBusy] = useState(false);

  const reload = () =>
    listMemory()
      .then(setItems)
      .catch((e) => setError(isApiError(e) ? e.message : "Failed to load memory"));

  useEffect(() => {
    reload();
  }, []);

  // Semantic search: debounce the query and ask the API for ranked hits (the same retrieval the
  // agents use). Empty query → clear results and fall back to the full list.
  useEffect(() => {
    const term = query.trim();
    if (!term) {
      setResults(null);
      return;
    }
    let active = true; // ignore a response if the query moved on (avoids out-of-order results)
    const id = window.setTimeout(() => {
      searchMemory(term, 20)
        .then((r) => { if (active) setResults(r); })
        .catch(() => { if (active) setResults([]); });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(id);
    };
  }, [query]);

  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(id);
  }, [notice]);

  const counts = useMemo(() => {
    const base: Record<MemType, number> = { project: 0, feedback: 0, reference: 0, user: 0 };
    for (const item of items ?? []) base[item.type] += 1;
    return base;
  }, [items]);

  // When searching, rank comes from the API (semantic); otherwise show the full list. Either way
  // the active type chip narrows it.
  const searching = query.trim().length > 0;
  const filtered = useMemo(() => {
    const source = searching ? (results ?? []) : (items ?? []);
    return source.filter((item) => filter === "all" || item.type === filter);
  }, [items, results, searching, filter]);

  const openCreate = () => {
    setEditing(null);
    setComposerOpen(true);
  };

  const openEdit = (item: MemoryItem) => {
    setEditing(item);
    setComposerOpen(true);
  };

  const closeComposer = () => {
    setComposerOpen(false);
    setEditing(null);
  };

  const submit = async (draft: Draft) => {
    const links = draft.links.split(",").map((s) => s.trim()).filter(Boolean);
    const input = { type: draft.type, title: draft.title.trim(), body: draft.body.trim(), links };
    setBusy(true);
    try {
      if (editing) {
        await updateMemory(editing.id, input);
        setNotice("Memory updated.");
      } else {
        await createMemory(input);
        setFilter("all");
        setQuery("");
        setNotice("Memory added.");
      }
      await reload();
      closeComposer();
    } catch (e) {
      setNotice(isApiError(e) ? e.message : "Could not save memory.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await deleteMemory(editing.id);
      await reload();
      setNotice("Memory pruned.");
      closeComposer();
    } catch (e) {
      setNotice(isApiError(e) ? e.message : "Could not delete memory.");
    } finally {
      setBusy(false);
    }
  };

  // Prune title-duplicate memories for real: delete the losing rows via the API, then reload.
  const consolidate = async () => {
    const list = items ?? [];
    const seen = new Set<string>();
    const duplicateIds: string[] = [];
    for (const item of list) {
      const key = normaliseTitle(item.title);
      if (seen.has(key)) duplicateIds.push(item.id);
      else seen.add(key);
    }
    if (duplicateIds.length === 0) {
      setNotice(`Reviewed ${list.length} memories — nothing to merge.`);
      return;
    }
    setBusy(true);
    try {
      await Promise.all(duplicateIds.map((id) => deleteMemory(id)));
      await reload();
      setNotice(`Merged ${duplicateIds.length} duplicate ${duplicateIds.length === 1 ? "memory" : "memories"}.`);
    } catch (e) {
      setNotice(isApiError(e) ? e.message : "Could not consolidate.");
    } finally {
      setBusy(false);
    }
  };

  const loading = !items && !error;
  const total = items?.length ?? 0;

  return (
    <div>
      <style>{STYLES}</style>

      <div className="page-header">
        <div>
          <h1 className="page-title">Memory</h1>
          <p className="page-desc">
            What the org has learned — facts, decisions, and preferences that persist across missions.
          </p>
        </div>
        <div className="page-actions">
          <Button variant="subtle" onClick={consolidate} disabled={loading || !!error || busy}>
            <Icon name="sparkles" size={16} />
            Consolidate
          </Button>
          <Button variant="primary" onClick={openCreate} disabled={loading || !!error || busy}>
            <Icon name="plus" size={16} />
            Add memory
          </Button>
        </div>
      </div>

      {error && <div className="lb-error">{error}</div>}
      {loading && <p style={{ color: "var(--faint)" }}>Loading…</p>}

      {notice && (
        <div className="mem-notice">
          <Icon name="check" size={14} />
          {notice}
        </div>
      )}

      {items && (
        <>
          <div className="mem-toolbar">
            <TypeChips active={filter} counts={counts} total={total} onSelect={setFilter} />
            <div className="mem-search">
              <span className="m-si">
                <Icon name="search" size={15} />
              </span>
              <input
                className="input"
                type="text"
                placeholder="Search memory…"
                value={query}
                autoComplete="off"
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="mem-grid">
            {filtered.length === 0 ? (
              <div className="empty">
                <div className="em-ic">◈</div>
                {query
                  ? `No memories match “${query}” yet.`
                  : "No memories in this filter yet."}
              </div>
            ) : (
              filtered.map((item) => <MemoryCard key={item.id} item={item} onEdit={openEdit} />)
            )}
          </div>
        </>
      )}

      {composerOpen && (
        <Composer editing={editing} busy={busy} onClose={closeComposer} onSubmit={submit} onDelete={remove} />
      )}
    </div>
  );
}

const STYLES = `
.mem-toolbar{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; justify-content:space-between; margin-bottom:20px; }
.mem-chips{ display:flex; flex-wrap:wrap; gap:8px; }
.mem-chip{ display:inline-flex; align-items:center; gap:8px; height:34px; padding:0 11px 0 8px; border-radius:11px;
  border:1px solid var(--line); background:var(--panel-2); color:var(--muted); font-weight:600; font-size:12.5px;
  cursor:pointer; transition:.16s; user-select:none; position:relative; }
.mem-chip:hover{ color:var(--text); border-color:var(--line-2); transform:translateY(-1px); }
.mem-chip.active{ color:var(--text); border-color:transparent; background:var(--grad-soft); box-shadow:inset 0 0 0 1px rgba(124,92,255,.42); }
.mem-chip .mc-ic{ width:22px; height:22px; border-radius:7px; display:grid; place-content:center; background:var(--panel); border:1px solid var(--line); }
.mem-chip .mc-count{ font-family:var(--mono); font-size:11px; color:var(--faint); background:var(--panel); border:1px solid var(--line);
  border-radius:20px; padding:0 7px; line-height:16px; font-variant-numeric:tabular-nums; }
.mem-chip.active .mc-count{ color:var(--text); }

.mem-search{ position:relative; flex:0 1 300px; min-width:200px; }
.mem-search .m-si{ position:absolute; left:12px; top:50%; transform:translateY(-50%); color:var(--faint); display:grid; pointer-events:none; }
.mem-search .input{ padding-left:36px; width:100%; }

.mem-notice{ display:inline-flex; align-items:center; gap:8px; margin-bottom:16px; padding:9px 13px; border-radius:11px;
  font-size:12.5px; font-weight:600; color:var(--green); background:rgba(58,210,159,.13); border:1px solid rgba(58,210,159,.3); }

.mem-grid{ display:grid; gap:16px; grid-template-columns:repeat(auto-fill, minmax(min(100%, 290px), 1fr)); }
.mem-grid .empty{ grid-column:1/-1; }

.mem-card{ position:relative; display:flex; flex-direction:column; gap:11px; padding:16px 17px; min-height:172px;
  background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow-sm);
  cursor:pointer; transition:.16s; overflow:hidden; }
.mem-card::before{ content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--edge); opacity:.75; }
.mem-card:hover{ transform:translateY(-2px); border-color:var(--line-2); box-shadow:var(--shadow); }
.mem-card:focus-visible{ outline:none; border-color:var(--brand); box-shadow:var(--ring); }
.mem-title{ font-size:14.5px; font-weight:700; letter-spacing:-.01em; line-height:1.35; }
.mem-body{ margin:0; color:var(--muted); font-size:12.8px; line-height:1.55; flex:1;
  display:-webkit-box; -webkit-line-clamp:4; line-clamp:4; -webkit-box-orient:vertical; overflow:hidden; }
.mem-edit{ color:var(--faint); opacity:0; transition:.16s; display:grid; }
/* Edit pencil sits in the top-right corner of an overflow-hidden card: anchor its tooltip to the
   right edge (extends leftward) so it never clips, and data-tip-below drops it into the card body. */
.mem-edit[data-tip]:hover::after{ left:auto; right:0; transform:none; }
.mem-card:hover .mem-edit, .mem-card:focus-within .mem-edit{ opacity:1; }
.mem-foot{ margin-top:auto; padding-top:11px; border-top:1px solid var(--line); display:flex; align-items:center;
  justify-content:space-between; gap:10px; flex-wrap:wrap; }
.mem-updated{ display:inline-flex; align-items:center; gap:5px; font-size:11px; color:var(--faint); }
.mem-links{ display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end; }
.mem-links .chip{ padding:2px 8px; font-size:11px; color:var(--muted); }
/* Modal footer: Delete + reassurance hint on the left, Cancel/Save locked together on the right so
   the buttons never get squeezed and the hint no longer wraps between them. */
.mem-modalfoot{ align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
.mem-modalfoot .mf-left{ display:flex; align-items:center; gap:12px; min-width:0; flex:1 1 auto; }
.mem-modalfoot .mf-hint{ display:inline-flex; align-items:center; gap:6px; font-size:12px;
  color:var(--muted); min-width:0; line-height:1.4; }
.mem-modalfoot .mf-hint svg{ color:var(--green); flex:0 0 auto; }
.mem-modalfoot .mf-actions{ display:flex; align-items:center; gap:10px; flex:0 0 auto; margin-left:auto; }
@media(max-width:520px){
  .mem-modalfoot .mf-hint{ display:none; }
  .mem-modalfoot .mf-actions{ flex:1 1 auto; justify-content:flex-end; }
}
`;
