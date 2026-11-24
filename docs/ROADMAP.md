# Roadmap

Each step is one PR that adds a production concern to the same order flow. The seams
for most of them already exist in `src/shopflow/ports/`.

## Backend

| # | Step | What it teaches | Seam today |
|---|---|---|---|
| 1 | **FastAPI + Postgres + Olist ingestion** (done) | layering, transactions, idempotency, oversell safety, outbox | n/a |
| 2 | **Redis caching with invalidation** | cache-aside, TTLs, invalidate-on-write, stampede protection, versioned keys | `ports/cache.py` (`NoopCache`), `products.version` |
| 3 | **OpenSearch typo-tolerant search** | indexing pipeline, fuzzy matching, analyzers (en/pt), keeping the index in sync | `ports/search.py` (`PostgresSearch`) |
| 4 | **S3 / MinIO product images** | presigned uploads, object keys, CDN-friendly URLs | `products.photos_qty` |
| 5 | **Queue workers + Kafka order events** | outbox relay, at-least-once delivery, idempotent consumers, retries and DLQ | `outbox_events`, `idempotency_keys` |
| 6 | **OpenTelemetry, Prometheus, Grafana, Jaeger** | traces across API, DB and workers, RED metrics, dashboards, SLOs | request-id middleware, JSON logs |
| 7 | **GitHub Actions CI** | lint, test against service containers, image build and push | `make lint`, `make test` |
| 8 | **Terraform** | VPC, RDS, ElastiCache, MSK/OpenSearch, S3, IAM as code | env-driven config |
| 9 | **Kubernetes + Helm** | Deployments, probes, HPA, migration Job, secrets | `/healthz`, `/readyz`, `migrate` service |
| 10 | **ArgoCD** | GitOps promotion across environments | Helm chart from step 9 |

## ML

Delivery-time prediction (Olist has promised and actual delivery dates), review-score
prediction, and recommendations from co-purchase data. Features come from the curated
tables and `staging.geolocation`.

## AI

Multi-agent orchestration (for example a shopping assistant plus a support agent working
over the catalog, orders and reviews APIs), with guardrails and an evaluation harness.
An LLM translation backend can also replace LibreTranslate behind the same interface.
