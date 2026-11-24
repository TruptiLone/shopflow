from tests.conftest import PRODUCT_BEAUTY, PRODUCT_UNSOLD


async def test_health(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200


async def test_categories_have_english_names_and_counts(client):
    cats = {c["slug"]: c for c in (await client.get("/api/v1/categories")).json()}
    assert cats["beleza_saude"]["name_en"] == "Health Beauty"
    assert cats["beleza_saude"]["product_count"] == 1  # the unsold one is inactive


async def test_list_products_filters_and_sorts(client):
    r = await client.get("/api/v1/products", params={"sort": "price_desc"})
    body = r.json()
    assert r.status_code == 200
    prices = [float(p["price"]) for p in body["items"]]
    assert prices == sorted(prices, reverse=True)
    assert PRODUCT_UNSOLD not in {p["id"] for p in body["items"]}

    r = await client.get("/api/v1/products", params={"category": "pc_gamer"})
    assert [p["category_name"] for p in r.json()["items"]] == ["PC Gaming"]

    r = await client.get("/api/v1/products", params={"max_price": "20"})
    assert all(float(p["price"]) <= 20 for p in r.json()["items"])


async def test_pagination_is_capped(client):
    r = await client.get("/api/v1/products", params={"limit": 1000})
    assert r.json()["limit"] == 100


async def test_product_detail_and_404(client):
    r = await client.get(f"/api/v1/products/{PRODUCT_BEAUTY}")
    assert r.status_code == 200
    assert r.json()["review_count"] == 2
    r = await client.get("/api/v1/products/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_reviews_are_english(client):
    r = await client.get(f"/api/v1/products/{PRODUCT_BEAUTY}/reviews")
    items = r.json()["items"]
    assert r.json()["total"] == 2
    translated = [i for i in items if i["translated"]]
    assert translated and translated[0]["message"].startswith("[en] ")
    assert translated[0]["message_pt"] is not None


async def test_search(client):
    r = await client.get("/api/v1/search", params={"q": "gaming"})
    assert [p["category_slug"] for p in r.json()["items"]] == ["pc_gamer"]
    assert (await client.get("/api/v1/search", params={"q": "x"})).status_code == 422


async def test_request_id_roundtrip(client):
    r = await client.get("/healthz", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
