import { cloneElement, forwardRef, isValidElement } from "react";
import type { ButtonHTMLAttributes, CSSProperties, ReactElement } from "react";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "subtle" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual variant (mirrors the mockup's `.btn` modifiers). */
  variant?: ButtonVariant;
  /** Control height/padding/type-scale. */
  size?: ButtonSize;
  /** Stretch to full width and center content. */
  block?: boolean;
  /**
   * Render the single child element instead of a `<button>`, merging the
   * button's className/style/handlers onto it (Radix "Slot" pattern). Use to
   * make a `<Link>` look like a Button: `<Button asChild><Link .../></Button>`.
   */
  asChild?: boolean;
}

const SIZE: Record<ButtonSize, CSSProperties> = {
  sm: { height: 32, padding: "0 11px", fontSize: 12, borderRadius: 9 },
  md: { height: 38, padding: "0 15px", fontSize: 13, borderRadius: 11 },
  lg: { height: 44, padding: "0 20px", fontSize: 14, borderRadius: 11 },
};

const VARIANT: Record<ButtonVariant, CSSProperties> = {
  primary: {
    background: "var(--grad)",
    border: "none",
    color: "#fff",
    boxShadow: "0 10px 26px -12px rgba(124,92,255,.8)",
  },
  // Use the `border` shorthand (not `borderColor`) so a variant never mixes shorthand + longhand
  // with the base `border` — React warns and can drop the color when switching variants.
  subtle: { background: "var(--panel-2)", border: "1px solid var(--line)" },
  ghost: { background: "transparent" },
  danger: {
    color: "var(--red)",
    border: "1px solid color-mix(in srgb, var(--red) 40%, var(--line))",
  },
};

/**
 * Button — the design-system primitive.
 * Renders the mockup's `.btn` class names (so app-level CSS can enhance
 * hover/focus) plus token-referencing inline styles so it looks correct
 * even when only `tokens.css` is loaded. Focus ring uses the `--ring` token.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "subtle",
    size = "md",
    block = false,
    asChild = false,
    className,
    style,
    type,
    disabled,
    children,
    ...rest
  },
  ref,
) {
  const base: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: block ? "center" : undefined,
    gap: 8,
    width: block ? "100%" : undefined,
    border: "1px solid var(--line-2)",
    background: "var(--panel)",
    color: "var(--text)",
    fontFamily: "inherit",
    fontWeight: 600,
    whiteSpace: "nowrap",
    userSelect: "none",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : undefined,
    transition: ".16s",
  };
  const mergedClassName = cx("btn", variant, block && "block", size, className);
  const mergedStyle: CSSProperties = { ...base, ...SIZE[size], ...VARIANT[variant], ...style };

  // Slot pattern: render the child (e.g. a Next <Link>) with our styling merged on.
  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<{ className?: string; style?: CSSProperties }>;
    return cloneElement(child, {
      ref,
      className: cx(mergedClassName, child.props.className),
      style: { ...mergedStyle, ...child.props.style },
      ...rest,
    } as Record<string, unknown>);
  }

  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled}
      className={mergedClassName}
      style={mergedStyle}
      {...rest}
    >
      {children}
    </button>
  );
});
