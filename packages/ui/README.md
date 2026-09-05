# @foundry/ui

Shipwright's shared design system: the **token layer** (ported 1:1 from the
original design mockup) plus typed, accessible React primitives.

Dark-first "mission control" aesthetic, premium violet → cyan palette. Every color in
the library references a CSS custom property from `tokens.css` — no pixel color literals
outside that file (09-frontend.md §A8).

## Consuming

This package is **source-only** — there is no build step. `apps/web` compiles the TSX
directly via Next's `transpilePackages: ["@foundry/ui", "@foundry/api-types"]`.

```ts
// apps/web/app/layout.tsx (or global CSS)
import "@foundry/ui/tokens.css";

// anywhere in the app
import { Button, Badge } from "@foundry/ui";
```

```tsx
<Button variant="primary" size="md">New mission</Button>
<Badge tone="green" dot>Shipped</Badge>
<Badge tone="amber">Usage limit</Badge>
```

Add the dependency from another workspace package with `"@foundry/ui": "workspace:*"`.

## Exports

| Entry | What |
|---|---|
| `.` (`./src/index.ts`) | `Button`, `Badge`, `cx`, and their prop/variant types |
| `./tokens.css` | CSS custom properties + Tailwind v4 `@theme inline` bridge |

## Theming

`tokens.css` resolves three states via `data-theme` on `<html>`:

- **default / `data-theme="dark"`** — dark (Shipwright's identity)
- **`data-theme="light"`** — explicit light overrides
- **`data-theme="system"`** — follows `prefers-color-scheme`

The `@theme inline` block maps tokens onto Tailwind v4 utilities (`bg-panel`,
`text-muted`, `rounded-card`, `font-display`, …); it activates when the consumer runs
Tailwind v4.

## Develop

```sh
pnpm --filter @foundry/ui typecheck   # tsc --noEmit against tsconfig.base.json
```

Components use `forwardRef`, typed `variant`/`size` props, and render both the mockup's
class names (for app-level CSS enhancement) and token-referencing inline styles (so they
render correctly with only `tokens.css` loaded).
