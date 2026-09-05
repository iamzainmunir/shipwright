# Helm charts — Phase 4 (placeholder)

Kubernetes packaging is **deferred to Phase 4** per the roadmap. Local development uses
`../docker/docker-compose.yml`; there are no charts to install yet.

When Phase 4 lands, this directory will hold the chart layout:

```
deploy/helm/
  foundry/                     # umbrella chart
    charts/
      web/                     # apps/web (Next.js)            :3000
      orchestrator/            # services/orchestrator (API + WS + workers) :8000
      runner/                  # services/runner (sandbox dev)
      qa-runner/               # services/qa-runner (Playwright)
      ingester/                # services/ingester (Go)        :8090
    values.yaml                # base
    values-dev.yaml
    values-staging.yaml
    values-production.yaml
  deps/                        # dev/preview only (statefuls are Terraform-managed in staging/prod)
    temporal/  redis/  postgres/  clickhouse/  minio/  vault/
  observability/               # kube-prometheus-stack, loki, tempo, grafana, alloy, otel-collector
    dashboards/
    datasources.yaml
    rules/
    slos.yaml
```

Per-service subchart contents (doc 11 §12.2): `Deployment`, `Service`, `ServiceMonitor`,
`HPA`/KEDA `ScaledObject`, `PodDisruptionBudget`, `NetworkPolicy` (default-deny + explicit
egress), Vault-bound `ServiceAccount` (external-secrets), resource requests/limits,
liveness/readiness/startup probes, `topologySpreadConstraints`, and injected OTel env.

Managed statefuls (Postgres/Redis/ClickHouse) are provisioned by Terraform (`deploy/terraform/`)
in staging/prod; the `deps/*` charts are used only in dev/preview. Temporal, MinIO, and Vault
run in-cluster via their official charts in all environments.
