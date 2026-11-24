# shopflow

A production-style e-commerce backend built to practise system design end to end, using
the real [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(~100k orders, 33k products, 3k sellers, 2016–2018), translated to English.

The idea is to follow **one order through the whole stack** and add one production
concern at a time: caching, search, object storage, queues and events, observability,
CI/CD, infrastructure as code and Kubernetes. After the backend come ML (recommendations,
delivery-time prediction) and AI (multi-agent orchestration with guardrails and evals).

**Phase 1 (this release):** FastAPI + PostgreSQL in Docker Compose, the Olist ingestion
pipeline with English translation, and the catalog, cart and order APIs.

- How it fits together and why: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- What comes next, in order: [docs/ROADMAP.md](docs/ROADMAP.md)
- How Olist maps to our schema: [docs/DATA.md](docs/DATA.md)

## Quick start (2 minutes, no Kaggle account)

```bash
cp .env.example .env
make up              # postgres + migrations + api on http://localhost:8000
make ingest-sample   # loads a tiny bundled sample of Olist
open http://localhost:8000/docs
```

## Load the full Olist dataset

The dataset is on Kaggle, which requires a free account. Pick **one** way to give the
pipeline access:

1. **API token (recommended).** On kaggle.com go to *Settings → API → Create New Token*.
   Copy the username and key from the downloaded `kaggle.json` into `.env`:
   ```
   KAGGLE_USERNAME=your_username
   KAGGLE_KEY=your_key
   ```
2. **Manual download.** Download the zip from the dataset page in a browser, save it as
   `data/archive.zip`, then run
   `docker compose --profile ingest run --rm ingest download --zip /data/archive.zip`.

`.env` and `data/` are git-ignored, so credentials and data never get committed.

Then:

```bash
make ingest          # download -> load -> translate reviews (pt -> en)
```

Loading takes about a minute. Review translation runs through a self-hosted
[LibreTranslate](https://libretranslate.com) container (open source, no API key, no cost);
the first start downloads its models. Translating ~40k distinct review strings on a
laptop CPU takes a while, so the step is **resumable**: stop it any time and run
`make translate` to pick up where it left off. Try `--limit 500` for a quick sample:
`docker compose --profile ingest run --rm ingest translate --limit 500`.

## API

All business endpoints live under `/api/v1`. Interactive docs at `/docs`.

| Area | Endpoint | Notes |
|---|---|---|
| Health | `GET /healthz`, `GET /readyz` | liveness and readiness (checks the DB) |
| Catalog | `GET /categories` | English names, product counts |
| | `GET /products?category=&min_price=&max_price=&sort=&limit=&offset=` | `sort`: popular, price_asc, price_desc, rating |
| | `GET /products/{id}` | |
| | `GET /products/{id}/reviews` | English text, original Portuguese kept alongside |
| | `GET /search?q=` | Postgres for now, OpenSearch later |
| Cart | `POST /carts` | `{"customer_id": "..."}` |
| | `GET /carts/{id}` | live prices and totals |
| | `POST /carts/{id}/items` | `{"product_id": "...", "quantity": 2}` (merges lines) |
| | `PATCH /carts/{id}/items/{product_id}`, `DELETE ...` | |
| Orders | `POST /orders` | `{"cart_id": "..."}` + required `Idempotency-Key` header |
| | `GET /orders/{id}` | |
| | `POST /orders/{id}/cancel` | returns reserved stock |
| | `PATCH /orders/{id}/status` | approve, invoice, ship, deliver (state machine enforced) |
| | `GET /customers/{id}/orders` | includes historical Olist orders |

Try a full checkout against the sample data:

```bash
CART=$(curl -s -X POST localhost:8000/api/v1/carts -H 'content-type: application/json' \
  -d '{"customer_id":"u1000000000000000000000000000000"}' | jq -r .id)
curl -s -X POST localhost:8000/api/v1/carts/$CART/items -H 'content-type: application/json' \
  -d '{"product_id":"b1000000000000000000000000000000","quantity":2}' | jq
curl -s -X POST localhost:8000/api/v1/orders -H 'content-type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" -d "{\"cart_id\":\"$CART\"}" | jq
```

Errors share one shape: `{"error": {"code": "out_of_stock", "message": "..."}}`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,ingest]'
docker compose up -d postgres
make test            # integration tests against a real Postgres (creates shopflow_test)
make lint
```

Layout:

```
src/shopflow/
  api/        FastAPI app, routers, dependency wiring, middleware
  services/   business logic: catalog, carts, orders (no HTTP in here)
  domain/     ORM models (storage) and Pydantic schemas (wire format)
  ports/      swappable adapters: cache, search, events (outbox)
  ingest/     Kaggle download, CSV -> staging -> curated load, translation
  core/       config, DB engine, JSON logging, domain errors
migrations/   Alembic
tests/        integration tests + a tiny Olist-shaped fixture
```
