"use client";

import { Badge, Button, Icon } from "@foundry/ui";
import { useRouter } from "next/navigation";
import { type CSSProperties, useEffect, useState } from "react";
import { isApiError } from "@/lib/api";
import { useToast } from "@/components/toast";
import {
  type Project,
  deleteProject,
  listProjects,
  registerProject,
  startProjectChange,
} from "@/lib/foundry";

const inp: CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  borderRadius: 10,
  border: "1px solid var(--line)",
  background: "var(--surface)",
  color: "var(--text)",
};

const overlay: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,.45)",
  display: "grid",
  placeItems: "center",
  zIndex: 50,
  padding: 16,
};

export default function ProjectsPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [request, setRequest] = useState("");
  const [starting, setStarting] = useState(false);
  const [showRegister, setShowRegister] = useState(false);

  async function load() {
    try {
      setProjects(await listProjects());
      setError(null);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Failed to load projects");
    }
  }
  useEffect(() => {
    void load();
  }, []);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  async function onRemove(p: Project) {
    try {
      await deleteProject(p.id);
      setSelected((s) => {
        const n = new Set(s);
        n.delete(p.id);
        return n;
      });
      await load();
    } catch (e) {
      toast("Couldn't remove project", isApiError(e) ? e.message : undefined, "err");
    }
  }

  async function onStart() {
    if (selected.size === 0 || !title.trim() || !request.trim()) return;
    setStarting(true);
    try {
      const mission = await startProjectChange({
        projectIds: [...selected],
        title: title.trim(),
        request: request.trim(),
      });
      router.push(`/dashboard/missions/${mission.key}`);
    } catch (e) {
      toast("Couldn't start the change", isApiError(e) ? e.message : undefined, "err");
      setStarting(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Projects</h1>
          <p className="page-desc">
            Your codebases — apps Shipwright built, plus local repos you register. Select one or more,
            describe a change, and the team coordinates it across them (e.g. add an API in a service
            and wire it into the gateway).
          </p>
        </div>
        <div className="page-actions">
          <Button variant="primary" size="sm" onClick={() => setShowRegister(true)}>
            <Icon name="plus" size={14} /> Register project
          </Button>
        </div>
      </div>

      {error && (
        <div className="lb-error" role="alert">
          {error}
        </div>
      )}

      {projects === null ? (
        <p className="hint">Loading…</p>
      ) : projects.length === 0 ? (
        <div className="card" style={{ padding: 24 }}>
          <p className="hint">
            No projects yet. Register a local git repo, or ship a mission and its project appears here
            automatically.
          </p>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))",
            gap: 12,
          }}
        >
          {projects.map((p) => {
            const on = selected.has(p.id);
            return (
              <div
                key={p.id}
                className="card"
                style={{ padding: 16, borderColor: on ? "var(--brand)" : undefined }}
              >
                <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(p.id)}
                    aria-label={`Select ${p.name}`}
                    style={{ marginTop: 4, accentColor: "var(--brand)" }}
                  />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="row gap-8" style={{ alignItems: "center" }}>
                      <span style={{ fontWeight: 700 }}>{p.name}</span>
                      <Badge tone={p.source === "built" ? "green" : "blue"}>{p.source}</Badge>
                    </div>
                    <div
                      className="hint"
                      style={{
                        fontFamily: "var(--font-mono, monospace)",
                        wordBreak: "break-all",
                        marginTop: 4,
                      }}
                    >
                      {p.path}
                    </div>
                  </div>
                </label>
                <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
                  <Button variant="subtle" size="sm" onClick={() => onRemove(p)}>
                    <Icon name="close" size={13} /> Remove
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selected.size > 0 && (
        <div className="card" style={{ padding: 18, marginTop: 16 }}>
          <div className="card-head">
            <h3>
              <Icon name="branch" size={16} /> Coordinated change · {selected.size} selected
            </h3>
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="pc-title">Title</label>
            <input
              id="pc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Add an /orders API"
              style={inp}
            />
          </div>
          <div className="field">
            <label htmlFor="pc-req">What should change?</label>
            <textarea
              id="pc-req"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              rows={4}
              placeholder="Add an orders endpoint in the service and route it through the gateway."
              style={{ ...inp, resize: "vertical" }}
            />
          </div>
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
            <Button
              variant="primary"
              size="sm"
              onClick={onStart}
              disabled={starting || !title.trim() || !request.trim()}
            >
              <Icon name="rocket" size={14} /> {starting ? "Starting…" : "Start change"}
            </Button>
          </div>
        </div>
      )}

      {showRegister && (
        <RegisterModal
          onClose={() => setShowRegister(false)}
          onDone={async () => {
            setShowRegister(false);
            await load();
          }}
        />
      )}
    </div>
  );
}

function RegisterModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!name.trim() || !path.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      await registerProject({ name: name.trim(), path: path.trim() });
      toast("Project registered", undefined, "ok");
      onDone();
    } catch (e) {
      setErr(isApiError(e) ? e.message : "Could not register project");
      setSaving(false);
    }
  }

  return (
    <div onClick={onClose} style={overlay}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="card"
        style={{ padding: 20, width: 520, maxWidth: "92vw" }}
      >
        <div className="card-head">
          <h3>
            <Icon name="branch" size={16} /> Register project
          </h3>
        </div>
        <p className="hint">Point Shipwright at an existing local git repository.</p>
        <div className="field" style={{ marginTop: 10 }}>
          <label htmlFor="rp-name">Name</label>
          <input
            id="rp-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="API Gateway"
            style={inp}
          />
        </div>
        <div className="field">
          <label htmlFor="rp-path">Local repo path</label>
          <input
            id="rp-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/Users/you/code/gateway"
            style={{ ...inp, fontFamily: "var(--font-mono, monospace)" }}
          />
        </div>
        {err && (
          <div className="lb-error" role="alert">
            {err}
          </div>
        )}
        <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <Button variant="subtle" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={save}
            disabled={saving || !name.trim() || !path.trim()}
          >
            {saving ? "Registering…" : "Register"}
          </Button>
        </div>
      </div>
    </div>
  );
}
