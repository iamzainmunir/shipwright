# CI workflows

`ci.yml` runs on every pull request and on pushes to `main`. It fans out into one job per
toolchain in the monorepo, all against the versions pinned in [`docs/VERSIONS.md`](../../docs/VERSIONS.md):
**web** installs the workspace with pnpm 11 on Node 24 (pnpm store cached) and runs
`lint → typecheck → build` for `@foundry/web`; **orchestrator** and **py-core** each use
`astral-sh/setup-uv` with Python 3.13 to `uv sync` and run their tests (orchestrator also runs
`ruff check`); **ingester** uses Go 1.24 to `go build`, `go vet`, and `go test`; and **db** stands
up a Postgres 16 + `pgvector` service container and `psql`-applies every `db/migrations/*.sql`
file in order to prove the migrations apply cleanly. A `concurrency` group keyed on the ref
cancels superseded in-flight runs, and each language job caches its dependencies for fast reruns.
This is the minimal correctness gate; supply-chain scanning, image build/sign, migration
reversibility, and preview/staging deploys layer
on top of it in later phases.
