# Olist data mapping

Source: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(CC BY-NC-SA 4.0). Nine CSVs, about 100k orders placed from 2016 to 2018.

| Olist file | Staging table | Curated table | Notes |
|---|---|---|---|
| `product_category_name_translation.csv` | `category_translation` | `categories` | The file starts with a UTF-8 BOM. `pc_gamer` and `portateis_cozinha_e_preparadores_de_alimentos` are missing from it and get overrides |
| `olist_products_dataset.csv` | `products` | `products` | No product names in Olist, so `name` = English category + short id. No price in Olist either: `price` = median sale price, `seller_id` = most frequent seller, `freight_estimate` = mean freight. Never-sold products have no price and are `is_active = false` |
| `olist_sellers_dataset.csv` | `sellers` | `sellers` | |
| `olist_customers_dataset.csv` | `customers` | `customers` | Olist's `customer_id` is per order; `customer_unique_id` is the person. We key customers by the unique id and remap orders onto it |
| `olist_orders_dataset.csv` | `orders` | `orders` | Local Brazil times converted from `America/Sao_Paulo` to `timestamptz`. `source = 'olist'`. Totals computed from items |
| `olist_order_items_dataset.csv` | `order_items` | `order_items` | One row per unit (`order_item_id` 1..n). New API orders follow the same convention |
| `olist_order_payments_dataset.csv` | `payments` | `payments` | `installments = 0` in a few rows is clamped to 1 |
| `olist_order_reviews_dataset.csv` | `reviews` | `reviews` | `review_id` is **not** unique (the same id appears on several orders), so the key is `(review_id, order_id)`. Messages contain embedded newlines. `*_pt` holds the original text, `*_en` the translation |
| `olist_geolocation_dataset.csv` | `geolocation` | none | About 1M rows, many duplicates per zip prefix. Kept in staging for the ML phase |

Derived: `products.review_count` / `review_avg` come from the reviews of orders that
contain the product.

## Translation

- Categories are translated with Olist's own translation file plus the overrides above
  (deterministic, no machine translation).
- Review titles and messages go through the `translate` step (`src/shopflow/ingest/translate.py`).
  Results are cached in `translations` by `sha256(source_text)`, so the step is
  incremental: new or re-run loads only translate strings that haven't been seen before.
- The API returns English in `title` and `message` when available, falls back to the
  Portuguese, and always includes `title_pt` / `message_pt` plus a `translated` flag.

## Sample fixture

`tests/fixtures/olist_sample/` holds a few rows per file with the real headers, and it
deliberately includes the awkward cases: the BOM, a category missing from the translation
file, a product with no category, a never-sold product, a duplicate `review_id`, a
multi-line review, and empty strings. `make ingest-sample` loads it.
