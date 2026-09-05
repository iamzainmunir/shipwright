/**
 * Branding + theme — the ONE place to rebrand the app.
 *
 * Change the product name, tagline, and brand accent colors here; everything else reads from
 * this file. (Deliberately generic-named — not tied to the current brand — so a rename is a
 * one-file edit.) The color tokens defined in `@foundry/ui/tokens.css` are overridden at runtime
 * by `brandCssVars()`, injected once in the root layout.
 */

export const BRAND = {
  /** Full product name (page titles, sidebar, landing, footer). */
  name: "Shipwright",
  /** One-line descriptor shown under the name. */
  tagline: "Autonomous Engineering Org",
  /** Folder name used when suggesting where greenfield app builds are created. */
  projectsDirName: "ShipwrightProjects",
} as const;

/** Brand accent colors. Change these to recolor the whole UI (buttons, links, focus ring, …). */
export const THEME = {
  brand: "#7c5cff",
  brand2: "#9d7bff",
  brandInk: "#ffffff",
} as const;

/** CSS that overrides the brand-accent tokens from THEME. Injected after the token stylesheet
 *  so it wins the cascade; applies across light & dark (brand accents are theme-independent). */
export function brandCssVars(): string {
  return (
    ":root{" +
    `--brand:${THEME.brand};` +
    `--brand-2:${THEME.brand2};` +
    `--brand-ink:${THEME.brandInk};` +
    `--ring:0 0 0 3px color-mix(in srgb, ${THEME.brand} 28%, transparent);` +
    "}"
  );
}
