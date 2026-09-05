import { forwardRef } from "react";
import type { HTMLAttributes, CSSProperties } from "react";
import { cx } from "./cx";

/** Semantic color tints (mirror the mockup's `.tint-*` helpers). */
export type BadgeTone =
  | "neutral"
  | "brand"
  | "green"
  | "amber"
  | "red"
  | "cyan"
  | "blue"
  | "pink";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** Color tone. `neutral` is the default panel chip. */
  tone?: BadgeTone;
  /** Solid fill (used for high-emphasis status pills). */
  solid?: boolean;
  /** Optional leading status dot in the tone's color. */
  dot?: boolean;
}

// Each tone sets the full `border` shorthand (not `borderColor`) so it never mixes shorthand +
// longhand with the base `border` — React warns and can drop the color when the tone re-renders.
const TINT: Record<Exclude<BadgeTone, "neutral">, CSSProperties> = {
  brand: { background: "rgba(124,92,255,.14)", color: "var(--brand-2)", border: "1px solid rgba(124,92,255,.3)" },
  green: { background: "rgba(58,210,159,.13)", color: "var(--green)", border: "1px solid rgba(58,210,159,.3)" },
  amber: { background: "rgba(246,196,84,.14)", color: "var(--amber)", border: "1px solid rgba(246,196,84,.32)" },
  red: { background: "rgba(255,107,125,.13)", color: "var(--red)", border: "1px solid rgba(255,107,125,.32)" },
  cyan: { background: "rgba(52,211,238,.13)", color: "var(--cyan)", border: "1px solid rgba(52,211,238,.3)" },
  blue: { background: "rgba(91,140,255,.13)", color: "var(--blue)", border: "1px solid rgba(91,140,255,.3)" },
  pink: { background: "rgba(255,122,198,.13)", color: "var(--pink)", border: "1px solid rgba(255,122,198,.32)" },
};

const DOT_COLOR: Record<BadgeTone, string> = {
  neutral: "var(--muted)",
  brand: "var(--brand)",
  green: "var(--green)",
  amber: "var(--amber)",
  red: "var(--red)",
  cyan: "var(--cyan)",
  blue: "var(--blue)",
  pink: "var(--pink)",
};

/**
 * Badge — small status chip.
 * Not color-only: callers pair the tone with text/icon content for a11y
 * (09-frontend.md A9). Renders `.badge`/`.tint-*` class names plus
 * token-referencing inline styles so it is correct with only `tokens.css`.
 */
export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { tone = "neutral", solid = false, dot = false, className, style, children, ...rest },
  ref,
) {
  const base: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 22,
    padding: "0 9px",
    borderRadius: 20,
    fontSize: 11.5,
    fontWeight: 600,
    lineHeight: 1,
    background: "var(--panel-2)",
    border: "1px solid var(--line)",
    color: "var(--muted)",
    whiteSpace: "nowrap",
  };

  const tint = tone === "neutral" ? undefined : TINT[tone];
  const solidStyle: CSSProperties | undefined = solid
    ? { color: "#fff", border: "none", background: DOT_COLOR[tone] }
    : undefined;

  return (
    <span
      ref={ref}
      className={cx("badge", tone !== "neutral" && `tint-${tone}`, solid && "solid", className)}
      style={{ ...base, ...tint, ...solidStyle, ...style }}
      {...rest}
    >
      {dot && (
        <span
          aria-hidden="true"
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flex: "0 0 8px",
            background: solid ? "#fff" : DOT_COLOR[tone],
          }}
        />
      )}
      {children}
    </span>
  );
});
