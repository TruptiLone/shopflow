"""Integration tests run against a real Postgres (no mocks for the DB layer).

A throwaway database is created, migrated with Alembic, and loaded with the small Olist
fixture through the real ingestion pipeline, so tests cover ingest + API together.
"""

import os
from pathlib import Path

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://shopflow:shopflow@localhost:5432/postgres")
TEST_DB = os.getenv("TEST_DB_NAME", "shopflow_test")
FIXTURE = Path(__file__).parent / "fixtures" / "olist_sample"

base = ADMIN_URL.rsplit("/", 1)[0]
os.environ["DATABASE_URL"] = f"{base.replace('postgresql://', 'postgresql+asyncpg://')}/{TEST_DB}"
os.environ["DEFAULT_STOCK"] = "5"

# Keep sample-data customer ids in one place for the tests.
CUSTOMER_1 = "u1" + "0" * 30
CUSTOMER_2 = "u2" + "0" * 30
PRODUCT_BEAUTY = "b1" + "0" * 30  # sold, reviewed, has a price
PRODUCT_PC = "b2" + "0" * 30
PRODUCT_UNSOLD = "b4" + "0" * 30  # never sold -> no price -> inactive


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB}"')

    from alembic import command
    from alembic.config import Config

    from shopflow.core.config import get_settings

    get_settings.cache_clear()
    root = Path(__file__).parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")

    from shopflow.ingest import load, translate

    with psycopg.connect(f"{base}/{TEST_DB}") as conn:
        load.load_staging(conn, FIXTURE)
        load.transform(conn, default_stock=5)
        translate.translate_reviews(conn, translate.Prefix())
    yield


@pytest.fixture(scope="session")
async def client(database):
    from shopflow.api.main import app
    from shopflow.core.db import dispose_engine
    from shopflow.ports.cache import NoopCache
    from shopflow.ports.search import PostgresSearch

    app.state.cache = NoopCache()
    app.state.search = PostgresSearch()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await dispose_engine()


@pytest.fixture
def db_conn():
    with psycopg.connect(f"{base}/{TEST_DB}", autocommit=True) as conn:
        yield conn
