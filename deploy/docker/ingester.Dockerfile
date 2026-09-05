# syntax=docker/dockerfile:1
# Shipwright ingester (services/ingester) — Go 1.24 webhook front door.
# BUILD CONTEXT = repo root:
#   docker build -f deploy/docker/ingester.Dockerfile -t foundry/ingester .
# Listens on :8090 (docs/VERSIONS.md), XADDs normalized events to Redis stream `ingest:webhooks` (Canon §13.8).

FROM golang:1.24 AS build
WORKDIR /src
# Cache modules first (go.sum may not exist yet on a fresh scaffold, hence the glob).
COPY services/ingester/go.mod services/ingester/go.sum* ./
RUN go mod download
COPY services/ingester/ ./
# Static binary; main package lives at the module root (adjust to ./cmd/ingester if the layout uses cmd/).
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/ingester .

# ---- runtime ----
FROM alpine:3.20
RUN apk add --no-cache ca-certificates && adduser -D -u 10001 foundry
COPY --from=build /out/ingester /usr/local/bin/ingester
USER foundry
ENV INGESTER_PORT=8090
EXPOSE 8090
ENTRYPOINT ["/usr/local/bin/ingester"]
