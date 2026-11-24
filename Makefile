.PHONY: up down logs migrate ingest ingest-sample translate test lint fmt

up:            ## build and start postgres + migrations + api
	docker compose up -d --build api

down:
	docker compose down

logs:
	docker compose logs -f api

migrate:
	docker compose run --rm migrate

ingest:        ## full Olist download + load + translate (needs Kaggle creds, see README)
	docker compose --profile ingest up -d --wait libretranslate
	docker compose --profile ingest run --rm ingest all

ingest-sample: ## load the tiny bundled sample instead (no Kaggle needed)
	docker compose --profile ingest run --rm -v ./tests/fixtures/olist_sample:/sample:ro -e RAW_DIR=/sample ingest load
	docker compose --profile ingest run --rm --no-deps ingest translate --backend prefix

translate:     ## (re)run only the review translation step; resumable
	docker compose --profile ingest up -d --wait libretranslate
	docker compose --profile ingest run --rm ingest translate

test:
	pytest -q

lint:
	ruff check . && ruff format --check .

fmt:
	ruff format . && ruff check --fix .
