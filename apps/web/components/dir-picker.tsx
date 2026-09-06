"use client";

import { Badge, Button, Icon } from "@foundry/ui";
import { type CSSProperties, useCallback, useEffect, useState } from "react";
import { isApiError } from "@/lib/api";
import { type DirEntry, listDir } from "@/lib/foundry";

const overlay: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,.45)",
  display: "grid",
  placeItems: "center",
  zIndex: 60,
  padding: 16,
};

/** A local-filesystem folder picker (backed by GET /fs/list). Click a folder to open it; use
 *  "Up" to go to the parent; "Select this folder" picks the current directory. Git repos are badged. */
export function DirectoryPicker({
  onClose,
  onSelect,
  title = "Choose a folder",
  initialPath,
}: {
  onClose: () => void;
  onSelect: (path: string) => void;
  title?: string;
  initialPath?: string;
}) {
  const [path, setPath] = useState<string>("");
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (p?: string) => {
    setLoading(true);
    try {
      const r = await listDir(p);
      setPath(r.path);
      setParent(r.parent);
      setEntries(r.entries);
      setError(null);
    } catch (e) {
      setError(isApiError(e) ? e.message : "Couldn't read that folder");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(initialPath);
  }, [load, initialPath]);

  return (
    <div style={overlay} onClick={onClose}>
      <div
        className="card"
        onClick={(e) => e.stopPropagation()}
        style={{
          padding: 18,
          width: 640,
          maxWidth: "94vw",
          maxHeight: "82vh",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div className="card-head">
          <h3>
            <Icon name="branch" size={16} /> {title}
          </h3>
        </div>

        <div className="row gap-8" style={{ alignItems: "center", margin: "8px 0" }}>
          <Button variant="subtle" size="sm" onClick={() => parent && load(parent)} disabled={!parent}>
            <Icon name="arrow" size={13} /> Up
          </Button>
          <span
            className="hint"
            style={{ fontFamily: "var(--font-mono, monospace)", wordBreak: "break-all", flex: 1 }}
          >
            {path || "…"}
          </span>
        </div>

        {error && (
          <div className="lb-error" role="alert">
            {error}
          </div>
        )}

        <div
          style={{
            overflowY: "auto",
            flex: 1,
            border: "1px solid var(--line)",
            borderRadius: 10,
            minHeight: 200,
          }}
        >
          {loading ? (
            <p className="hint" style={{ padding: 14 }}>
              Loading…
            </p>
          ) : entries.length === 0 ? (
            <p className="hint" style={{ padding: 14 }}>
              No sub-folders here.
            </p>
          ) : (
            entries.map((e) => (
              <div
                key={e.path}
                className="row"
                style={{
                  alignItems: "center",
                  gap: 8,
                  padding: "8px 12px",
                  borderBottom: "1px solid var(--line)",
                }}
              >
                <button
                  type="button"
                  onClick={() => load(e.path)}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text)",
                    flex: 1,
                    textAlign: "left",
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    minWidth: 0,
                  }}
                >
                  <Icon name="chevron" size={13} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{e.name}</span>
                  {e.isRepo && <Badge tone="green">git</Badge>}
                </button>
                <Button variant="subtle" size="sm" onClick={() => onSelect(e.path)}>
                  Select
                </Button>
              </div>
            ))
          )}
        </div>

        <div className="row" style={{ justifyContent: "space-between", gap: 8, marginTop: 12 }}>
          <span className="hint">
            Folders marked <b>git</b> are repositories. Click a folder to open it.
          </span>
          <div className="row gap-8">
            <Button variant="subtle" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={() => onSelect(path)} disabled={!path}>
              Select this folder
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
