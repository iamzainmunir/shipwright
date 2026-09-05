# @foundry/api-types

Shared TypeScript types for Shipwright's API wire shapes, imported by the web BFF and the
client SDK.

> **Hand-authored seed.** These types are maintained by hand today and are the deliberate
> seed of what will later be **generated from the orchestrator's OpenAPI spec**
> (plan doc `03-api-and-events.md`). Until that generator lands, keep `src/index.ts` in
> sync with **Canon §5** (entities) and **Canon §13.3** (locked enums) by hand.

## Conventions (Canon §8 / §13)

- JSON on the wire is **camelCase** (the DB is snake_case; the BFF maps).
- IDs are **ULIDs** (`Ulid`); user-facing mission keys are `FND-<n>` (`MissionKey`).
- Timestamps are ISO-8601 UTC strings (`IsoDateTime`).
- `priority` is `P0..P3` in JSON (stored `p0..p3`).
- Every error body is the `ErrorEnvelope` `{ error: { code, message, details, requestId, retryable } }`.

## Consuming

Source-only — no build step; consumed via Next `transpilePackages`. Add
`"@foundry/api-types": "workspace:*"` and import:

```ts
import type { Mission, Blocker, Paginated, ErrorEnvelope } from "@foundry/api-types";
```

## What's here

Entities: `Mission`, `Blocker`, `Agent`, `ModelConnection`, `Skill`, `Memory`.
Enums: `RunStatus`, `StepStatus`, `MissionStage`, `BlockerKind`, `Priority`, `Autonomy`,
`MissionSource`, `AgentRoleKey`, `AgentStatus`, `ModelProvider`, `ConnectionStatus`,
`ModelTier`, `SkillCategory`, `MemoryType`, and more.
Wrappers: `ErrorEnvelope`, `Paginated<T>`.

## Develop

```sh
pnpm --filter @foundry/api-types typecheck   # tsc --noEmit
```
