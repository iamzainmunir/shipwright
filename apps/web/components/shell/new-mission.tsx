"use client";

import { Button, Icon } from "@foundry/ui";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { isApiError } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { AUTONOMY_LEVELS, type AutonomyLevel, type Team, createMission, extractRequirements, listTeams } from "@/lib/foundry";
import { useToast } from "@/components/toast";
import { useShellData } from "./shell-data";

interface NewMissionContextValue {
  open: () => void;
}

const NewMissionContext = createContext<NewMissionContextValue | null>(null);

const SOURCES = [
  { value: "jira", label: "Jira" },
  { value: "linear", label: "Linear" },
  { value: "github", label: "GitHub" },
  { value: "manual", label: "Manual" },
];
const PRIORITIES = ["P1", "P0", "P2", "P3"];

export function NewMissionProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);

  return (
    <NewMissionContext.Provider value={{ open }}>
      {children}
      {isOpen && <NewMissionModal onClose={close} />}
    </NewMissionContext.Provider>
  );
}

export function useNewMission(): NewMissionContextValue {
  const ctx = useContext(NewMissionContext);
  if (!ctx) throw new Error("useNewMission must be used within <NewMissionProvider>");
  return ctx;
}

type Mode = "app" | "change";

function NewMissionModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const { toast } = useToast();
  const { refresh } = useShellData();
  const [mode, setMode] = useState<Mode>("app");
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [uploadName, setUploadName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [source, setSource] = useState("jira");
  const [priority, setPriority] = useState("P1");
  const [autonomy, setAutonomy] = useState<AutonomyLevel>("supervised");
  // Team that will staff this mission — "" means the whole org roster (no specific team).
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(true);
  const [teamId, setTeamId] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Load the real teams once so the mission can be staffed by an existing team. On failure we
  // silently fall back to just the "No team" option rather than block the whole modal.
  useEffect(() => {
    let active = true;
    listTeams()
      .then((next) => { if (active) setTeams(next); })
      .catch(() => { /* keep the org-roster-only picker */ })
      .finally(() => { if (active) setTeamsLoading(false); });
    return () => { active = false; };
  }, []);

  const hint = AUTONOMY_LEVELS.find((l) => l.key === autonomy)?.hint ?? "";
  const isApp = mode === "app";

  async function onUpload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    try {
      const res = await extractRequirements(file);
      setRequirements((prev) => (prev.trim() ? `${prev.trim()}\n\n${res.text}` : res.text));
      setUploadName(res.filename);
      toast("Requirements added", `${res.filename} · ${res.chars.toLocaleString()} chars`, "ok");
    } catch (e) {
      toast("Couldn't read the file", e instanceof Error ? e.message : "Try a .md, .txt, or .docx", "err");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function dispatch() {
    setBusy(true);
    try {
      const mission = await createMission({
        title: title.trim() || (isApp ? "New app" : "Untitled mission"),
        source: isApp ? "manual" : source,
        priority,
        autonomy,
        teamId: teamId || undefined,
        projectKind: isApp ? "app" : "change",
        requirements: requirements.trim() || undefined,
        projectPath: projectPath.trim() || undefined,
      });
      onClose();
      toast("Mission dispatched",
        isApp ? `${mission.key} — the team will scope, ask questions, then build`
              : `${mission.key} — the PM agent is on it`, "info");
      refresh();
      router.push(`/dashboard/missions/${mission.key}`);
    } catch (e) {
      toast("Could not create mission", isApiError(e) ? e.message : "Try again", "err");
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="New mission">
        <div className="modal-head">
          <h3>New mission</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label>What kind of work is this?</label>
            <div className="segmented" style={{ width: "100%" }} role="group" aria-label="Mission type">
              <button type="button" style={{ flex: 1 }} className={isApp ? "active" : ""} onClick={() => setMode("app")}>
                Build a new app
              </button>
              <button type="button" style={{ flex: 1 }} className={!isApp ? "active" : ""} onClick={() => setMode("change")}>
                Fix or change
              </button>
            </div>
          </div>

          <div className="field">
            <label htmlFor="nm-title">{isApp ? "What should the team build?" : "What should the team build or fix?"}</label>
            <textarea
              id="nm-title" className="textarea" autoFocus value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={isApp
                ? "e.g. Create a todo app using Next.js with localStorage persistence"
                : "Paste a Jira/Linear link, or describe the task…"}
            />
            <span className="hint">
              {isApp
                ? "The team scopes it, asks you clarifying questions, then builds it in a new project folder."
                : "The PM agent reads this, runs an intake interview, and drafts a spec."}
            </span>
          </div>

          <div className="field">
            <div className="row between" style={{ marginBottom: 6 }}>
              <label htmlFor="nm-req" style={{ margin: 0 }}>Requirements / brief <span className="faint">(optional)</span></label>
              <input
                ref={fileRef} type="file" accept=".md,.markdown,.txt,.docx" hidden
                onChange={(e) => onUpload(e.target.files?.[0] ?? undefined)}
              />
              <button type="button" className="link-btn text-xs fw-6" style={{ cursor: "pointer" }}
                      onClick={() => fileRef.current?.click()} disabled={uploading}>
                <Icon name="doc" size={13} /> {uploading ? "Reading…" : "Upload .md / .docx"}
              </button>
            </div>
            <textarea
              id="nm-req" className="textarea" value={requirements}
              onChange={(e) => setRequirements(e.target.value)}
              placeholder={isApp
                ? "Describe features, screens, data, and any tech-stack preference — or upload a requirements doc."
                : "Describe the bug or change, and where it lives — or upload a doc."}
            />
            {uploadName && <span className="hint">Loaded from {uploadName}</span>}
          </div>
          <div className="field">
            <label htmlFor="nm-path">
              {isApp ? <>Project location <span className="faint">(optional)</span></> : <>Existing repo path <span className="faint">(required for a real edit)</span></>}
            </label>
            <input id="nm-path" className="input mono" value={projectPath}
                   onChange={(e) => setProjectPath(e.target.value)}
                   placeholder={isApp ? `~/${BRAND.projectsDirName}/<mission>-<slug>  (default)` : "/path/to/your/repo"} />
            <span className="hint">
              {isApp
                ? "Where the app is created on disk. Leave blank for the default."
                : "A local git repo the team edits on a fix/… branch. Leave blank for the built-in demo."}
            </span>
          </div>

          <div className="grid g-2">
            {!isApp && (
              <div className="field">
                <label htmlFor="nm-source">Source</label>
                <select id="nm-source" className="select" value={source} onChange={(e) => setSource(e.target.value)}>
                  {SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </div>
            )}
            <div className="field">
              <label htmlFor="nm-priority">Priority</label>
              <select id="nm-priority" className="select" value={priority} onChange={(e) => setPriority(e.target.value)}>
                {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="nm-team">Team</label>
              <select id="nm-team" className="select" value={teamId} disabled={teamsLoading}
                      onChange={(e) => setTeamId(e.target.value)}>
                <option value="">{teamsLoading ? "Loading teams…" : "No team (org roster)"}</option>
                {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
          </div>

          <div className="field">
            <label>Autonomy for this mission</label>
            <div className="segmented" style={{ width: "100%" }} role="group" aria-label="Autonomy">
              {AUTONOMY_LEVELS.map((l) => (
                <button
                  key={l.key} type="button" style={{ flex: 1 }}
                  className={autonomy === l.key ? "active" : ""}
                  onClick={() => setAutonomy(l.key)}
                >
                  {l.label}
                </button>
              ))}
            </div>
            <span className="hint" style={{ marginTop: 8 }}>
              {isApp ? "Supervised is recommended — the team will pause to ask questions and before merge." : hint}
            </span>
          </div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={dispatch}>
            <Icon name="rocket" size={15} /> {busy ? "Dispatching…" : "Dispatch team"}
          </Button>
        </div>
      </div>
    </div>
  );
}
