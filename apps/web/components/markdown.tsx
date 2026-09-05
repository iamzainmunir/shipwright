"use client";

import type { ReactNode } from "react";

/**
 * Minimal, dependency-free, XSS-safe Markdown → React.
 *
 * Builds real React elements (never dangerouslySetInnerHTML), so agent/LLM output renders with
 * headings, bold/italic/inline-code, fenced code blocks, and bullet/number lists — instead of a
 * flattened blob of raw `##`/`**` markers. Kept intentionally small for run consoles.
 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return <div className={className ? `md ${className}` : "md"}>{renderBlocks(text ?? "")}</div>;
}

/** Inline formatting: **bold**, *italic*, `code`. */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${keyBase}-${i}`}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={`${keyBase}-${i}`} className="md-code">{tok.slice(1, -1)}</code>);
    else out.push(<em key={`${keyBase}-${i}`}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(<p key={`p-${key++}`}>{inline(para.join(" "), `p-${key}`)}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, j) => <li key={`li-${key}-${j}`}>{inline(it, `li-${key}-${j}`)}</li>);
      blocks.push(list.ordered ? <ol key={`ol-${key++}`}>{items}</ol> : <ul key={`ul-${key++}`}>{items}</ul>);
      list = null;
    }
  };

  for (let idx = 0; idx < lines.length; idx += 1) {
    const raw = lines[idx] ?? "";
    const line = raw.trimEnd();

    if (line.trimStart().startsWith("```")) {
      flushPara();
      flushList();
      const code: string[] = [];
      idx += 1;
      while (idx < lines.length && !(lines[idx] ?? "").trimStart().startsWith("```")) {
        code.push(lines[idx] ?? "");
        idx += 1;
      }
      blocks.push(
        <pre key={`code-${key++}`} className="md-pre">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const level = heading[1]?.length ?? 1;
      blocks.push(
        <div key={`h-${key++}`} className="md-h" data-level={level}>
          {inline(heading[2] ?? "", `h-${key}`)}
        </div>,
      );
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.*)$/);
    if (bullet || ordered) {
      flushPara();
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push((bullet?.[1] ?? ordered?.[1]) ?? "");
      continue;
    }

    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }

    flushList();
    para.push(line.trim());
  }
  flushPara();
  flushList();
  return blocks;
}
