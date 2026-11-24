from shopflow.ingest import load, translate
from tests.conftest import FIXTURE, PRODUCT_BEAUTY, PRODUCT_UNSOLD


def test_categories_translated_including_missing_ones(db_conn):
    rows = dict(db_conn.execute("SELECT slug, name_en FROM categories").fetchall())
    assert rows["beleza_saude"] == "Health Beauty"
    assert rows["pc_gamer"] == "PC Gaming"  # absent from Olist's translation csv


def test_customers_are_deduplicated_by_unique_id(db_conn):
    assert db_conn.execute("SELECT count(*) FROM customers").fetchone()[0] == 2


def test_product_price_is_median_sale_price_and_unsold_is_inactive(db_conn):
    price, active = db_conn.execute(
        "SELECT price, is_active FROM products WHERE id = %s", (PRODUCT_BEAUTY,)
    ).fetchone()
    assert str(price) == "29.99" and active
    price, active = db_conn.execute(
        "SELECT price, is_active FROM products WHERE id = %s", (PRODUCT_UNSOLD,)
    ).fetchone()
    assert price is None and not active


def test_duplicate_review_ids_across_orders_are_kept(db_conn):
    n = db_conn.execute(
        "SELECT count(*) FROM reviews WHERE review_id = %s", ("e2" + "0" * 30,)
    ).fetchone()[0]
    assert n == 2


def test_reviews_translated_and_cache_dedupes(db_conn):
    # "Recomendo" appears as a title and as a message but is translated once.
    assert db_conn.execute("SELECT count(*) FROM translations").fetchone()[0] == 2
    msgs = [
        r[0] for r in db_conn.execute("SELECT message_en FROM reviews WHERE message_pt IS NOT NULL")
    ]
    assert all(m.startswith("[en] ") for m in msgs)


def test_ingestion_is_idempotent(db_conn):
    before = db_conn.execute(
        "SELECT (SELECT count(*) FROM orders), (SELECT count(*) FROM order_items),"
        " (SELECT count(*) FROM reviews)"
    ).fetchone()
    load.load_staging(db_conn, FIXTURE)
    load.transform(db_conn, default_stock=5)
    stats = translate.translate_reviews(db_conn, translate.Prefix())
    after = db_conn.execute(
        "SELECT (SELECT count(*) FROM orders), (SELECT count(*) FROM order_items),"
        " (SELECT count(*) FROM reviews)"
    ).fetchone()
    assert before == after
    assert stats["translated"] == 0
