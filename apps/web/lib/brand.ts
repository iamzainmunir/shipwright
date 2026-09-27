/**
 * Branding + theme — reads the ONE canonical source, the repo-root `brand.json` (also read by the
 * backend's `foundry_core.brand`). Rebranding is a single edit to `brand.json`.
 *
 * The color tokens defined in `@foundry/ui/tokens.css` are overridden at runtime by `brandCssVars()`,
 * injected once in the root layout. (Importing across the app boundary needs `experimental.externalDir`
 * in next.config.)
 */
import brand from "../../../brand.json";

export const BRAND = {
  /** Full product name (page titles, sidebar, landing, footer). */
  name: brand.name,
  /** One-line descriptor shown under the name. */
  tagline: brand.tagline,
  /** Folder name used when suggesting where greenfield app builds are created. */
  projectsDirName: brand.projectsDirName,
} as const;

/** Brand accent colors. Change these in brand.json to recolor the whole UI (buttons, links, ring, …). */
export const THEME = {
  brand: brand.theme.brand,
  brand2: brand.theme.brand2,
  brandInk: brand.theme.brandInk,
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
