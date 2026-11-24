"""Two-stage load: raw CSV -> `staging` schema (all TEXT, schema-on-read) -> curated tables.

Every step is idempotent: staging is truncated and reloaded, curated inserts use
ON CONFLICT DO NOTHING, and derived columns are recomputed. Re-running never duplicates
data and never resets stock levels that the API has changed.
"""

import csv
import time
from pathlib import Path

import psycopg
from psycopg import sql

STAGING_TABLES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

# Olist's translation file is missing two categories that products use.
CATEGORY_OVERRIDES = {
    "pc_gamer": "PC Gaming",
    "portateis_cozinha_e_preparadores_de_alimentos": "Portable Kitchen and Food Preparers",
}

# Olist timestamps are local Brazil time without an offset.
TZ = "America/Sao_Paulo"


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:  # translation csv has a BOM
        return next(csv.reader(f))


def load_staging(conn: psycopg.Connection, raw_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS staging")
        for table, filename in STAGING_TABLES.items():
            path = raw_dir / filename
            cols = _header(path)
            ident = sql.Identifier("staging", table)
            col_defs = sql.SQL(", ").join(
                sql.SQL("{} text").format(sql.Identifier(c)) for c in cols
            )
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
            cur.execute(sql.SQL("CREATE TABLE {} ({})").format(ident, col_defs))
            copy = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                ident, sql.SQL(", ").join(sql.Identifier(c) for c in cols)
            )
            with path.open("rb") as f, cur.copy(copy) as cp:
                while chunk := f.read(1 << 20):
                    cp.write(chunk)
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
            counts[table] = cur.fetchone()[0]
    conn.commit()
    return counts


def _ts(col: str) -> str:
    return f"(NULLIF({col}, '')::timestamp AT TIME ZONE '{TZ}')"


def _int(col: str) -> str:
    return f"NULLIF({col}, '')::numeric::int"


def transform_sql(default_stock: int) -> list[tuple[str, str]]:
    overrides = ", ".join(f"('{k}', '{v}')" for k, v in CATEGORY_OVERRIDES.items())
    return [
        (
            "categories",
            f"""
            INSERT INTO categories (slug, name_en, name_pt)
            SELECT s.slug,
                   COALESCE(o.name, initcap(replace(t.product_category_name_english, '_', ' ')),
                            initcap(replace(s.slug, '_', ' '))),
                   initcap(replace(s.slug, '_', ' '))
            FROM (
                SELECT product_category_name AS slug FROM staging.category_translation
                UNION
                SELECT product_category_name FROM staging.products
                WHERE NULLIF(product_category_name, '') IS NOT NULL
            ) s
            LEFT JOIN staging.category_translation t ON t.product_category_name = s.slug
            LEFT JOIN (VALUES {overrides}) AS o(slug, name) ON o.slug = s.slug
            ON CONFLICT (slug) DO UPDATE SET name_en = EXCLUDED.name_en
        """,
        ),
        (
            "sellers",
            """
            INSERT INTO sellers (id, zip_code_prefix, city, state)
            SELECT DISTINCT ON (seller_id) seller_id, seller_zip_code_prefix, seller_city,
                   seller_state
            FROM staging.sellers ORDER BY seller_id
            ON CONFLICT (id) DO NOTHING
        """,
        ),
        (
            "customers",
            """
            INSERT INTO customers (id, zip_code_prefix, city, state)
            SELECT DISTINCT ON (customer_unique_id) customer_unique_id, customer_zip_code_prefix,
                   customer_city, customer_state
            FROM staging.customers ORDER BY customer_unique_id, customer_id
            ON CONFLICT (id) DO NOTHING
        """,
        ),
        (
            "products",
            f"""
            WITH sales AS (
                SELECT product_id,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY price::numeric) AS price,
                       avg(freight_value::numeric) AS freight,
                       mode() WITHIN GROUP (ORDER BY seller_id) AS seller_id
                FROM staging.order_items GROUP BY product_id
            )
            INSERT INTO products (id, name, category_slug, seller_id, price, freight_estimate,
                                  stock, is_active, photos_qty, weight_g, length_cm, height_cm,
                                  width_cm)
            SELECT p.product_id,
                   COALESCE(c.name_en, 'Product') || ' #' || left(p.product_id, 8),
                   c.slug, s.seller_id, round(s.price::numeric, 2), round(s.freight, 2),
                   {int(default_stock)}, s.price IS NOT NULL,
                   {_int("p.product_photos_qty")}, {_int("p.product_weight_g")},
                   {_int("p.product_length_cm")}, {_int("p.product_height_cm")},
                   {_int("p.product_width_cm")}
            FROM staging.products p
            LEFT JOIN categories c ON c.slug = NULLIF(p.product_category_name, '')
            LEFT JOIN sales s ON s.product_id = p.product_id
            ON CONFLICT (id) DO NOTHING
        """,
        ),
        (
            "orders",
            f"""
            WITH totals AS (
                SELECT order_id, sum(price::numeric) AS items, sum(freight_value::numeric) AS fr
                FROM staging.order_items GROUP BY order_id
            )
            INSERT INTO orders (id, customer_id, status, source, purchased_at, approved_at,
                                delivered_carrier_at, delivered_customer_at,
                                estimated_delivery_at, items_total, freight_total)
            SELECT o.order_id, c.customer_unique_id, o.order_status::order_status, 'olist',
                   {_ts("o.order_purchase_timestamp")}, {_ts("o.order_approved_at")},
                   {_ts("o.order_delivered_carrier_date")},
                   {_ts("o.order_delivered_customer_date")},
                   {_ts("o.order_estimated_delivery_date")},
                   COALESCE(t.items, 0), COALESCE(t.fr, 0)
            FROM staging.orders o
            JOIN staging.customers c ON c.customer_id = o.customer_id
            LEFT JOIN totals t ON t.order_id = o.order_id
            ON CONFLICT (id) DO NOTHING
        """,
        ),
        (
            "order_items",
            f"""
            INSERT INTO order_items (order_id, item_seq, product_id, seller_id, price,
                                     freight_value, shipping_limit_at)
            SELECT i.order_id, i.order_item_id::int, i.product_id, i.seller_id,
                   i.price::numeric, i.freight_value::numeric, {_ts("i.shipping_limit_date")}
            FROM staging.order_items i
            JOIN orders o ON o.id = i.order_id
            ON CONFLICT DO NOTHING
        """,
        ),
        (
            "payments",
            """
            INSERT INTO payments (order_id, sequential, payment_type, installments, value)
            SELECT p.order_id, p.payment_sequential::int, p.payment_type,
                   GREATEST(p.payment_installments::int, 1), p.payment_value::numeric
            FROM staging.payments p
            JOIN orders o ON o.id = p.order_id
            ON CONFLICT DO NOTHING
        """,
        ),
        (
            "reviews",
            f"""
            INSERT INTO reviews (review_id, order_id, score, title_pt, message_pt, created_at,
                                 answered_at)
            SELECT DISTINCT ON (r.review_id, r.order_id)
                   r.review_id, r.order_id, r.review_score::int,
                   NULLIF(btrim(r.review_comment_title), ''),
                   NULLIF(btrim(r.review_comment_message), ''),
                   {_ts("r.review_creation_date")}, {_ts("r.review_answer_timestamp")}
            FROM staging.reviews r
            JOIN orders o ON o.id = r.order_id
            ORDER BY r.review_id, r.order_id, r.review_answer_timestamp DESC
            ON CONFLICT DO NOTHING
        """,
        ),
        (
            "product_review_stats",
            """
            WITH stats AS (
                SELECT oi.product_id, count(*) AS n, round(avg(r.score), 2) AS avg
                FROM (SELECT DISTINCT order_id, product_id FROM order_items) oi
                JOIN reviews r ON r.order_id = oi.order_id
                GROUP BY oi.product_id
            )
            UPDATE products p SET review_count = s.n, review_avg = s.avg
            FROM stats s
            WHERE s.product_id = p.id
              AND (p.review_count, p.review_avg) IS DISTINCT FROM (s.n, s.avg)
        """,
        ),
    ]


def transform(conn: psycopg.Connection, default_stock: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for name, stmt in transform_sql(default_stock):
            t0 = time.perf_counter()
            cur.execute(stmt)
            counts[name] = cur.rowcount
            print(f"  {name:<22} {cur.rowcount:>8} rows  {time.perf_counter() - t0:6.2f}s")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("ANALYZE")
    return counts
