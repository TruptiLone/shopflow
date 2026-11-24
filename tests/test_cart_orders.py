import asyncio
import uuid

from tests.conftest import CUSTOMER_1, CUSTOMER_2, PRODUCT_BEAUTY, PRODUCT_PC, PRODUCT_UNSOLD

API = "/api/v1"


async def _cart(client, customer=CUSTOMER_1, items=()):
    r = await client.post(f"{API}/carts", json={"customer_id": customer})
    assert r.status_code == 201, r.text
    cart_id = r.json()["id"]
    for product_id, qty in items:
        r = await client.post(
            f"{API}/carts/{cart_id}/items", json={"product_id": product_id, "quantity": qty}
        )
        assert r.status_code == 200, r.text
    return cart_id


async def _stock(client, product_id):
    return (await client.get(f"{API}/products/{product_id}")).json()["stock"]


async def _checkout(client, cart_id, key=None):
    return await client.post(
        f"{API}/orders",
        json={"cart_id": cart_id},
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )


async def test_cart_lifecycle(client):
    cart_id = await _cart(client, items=[(PRODUCT_BEAUTY, 1)])
    r = await client.post(
        f"{API}/carts/{cart_id}/items", json={"product_id": PRODUCT_BEAUTY, "quantity": 2}
    )
    cart = r.json()
    assert cart["items"][0]["quantity"] == 3  # adding again merges lines
    assert cart["items_total"] == "89.97"

    r = await client.patch(f"{API}/carts/{cart_id}/items/{PRODUCT_BEAUTY}", json={"quantity": 1})
    assert r.json()["items_total"] == "29.99"

    r = await client.delete(f"{API}/carts/{cart_id}/items/{PRODUCT_BEAUTY}")
    assert r.json()["items"] == []


async def test_cart_validation(client):
    assert (await client.post(f"{API}/carts", json={"customer_id": "ghost"})).status_code == 404
    cart_id = await _cart(client)
    r = await client.post(
        f"{API}/carts/{cart_id}/items", json={"product_id": PRODUCT_UNSOLD, "quantity": 1}
    )
    assert r.status_code == 422
    r = await client.post(
        f"{API}/carts/{cart_id}/items", json={"product_id": PRODUCT_BEAUTY, "quantity": 0}
    )
    assert r.status_code == 422
    assert (await _checkout(client, cart_id)).status_code == 422  # empty cart


async def test_checkout_reserves_stock_and_writes_outbox(client, db_conn):
    before = await _stock(client, PRODUCT_BEAUTY)
    cart_id = await _cart(client, items=[(PRODUCT_BEAUTY, 2)])
    r = await _checkout(client, cart_id)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "created"
    assert [i["item_seq"] for i in order["items"]] == [1, 2]  # one row per unit, like Olist
    assert order["items_total"] == "59.98"
    assert await _stock(client, PRODUCT_BEAUTY) == before - 2

    cart = (await client.get(f"{API}/carts/{cart_id}")).json()
    assert cart["status"] == "checked_out"
    r = await client.post(
        f"{API}/carts/{cart_id}/items", json={"product_id": PRODUCT_BEAUTY, "quantity": 1}
    )
    assert r.status_code == 409

    event = db_conn.execute(
        "SELECT event_type, payload->>'items_total', published_at FROM outbox_events"
        " WHERE aggregate_id = %s",
        (order["id"],),
    ).fetchone()
    assert event == ("order.created", "59.98", None)

    # cancel returns stock and emits another event
    r = await client.post(f"{API}/orders/{order['id']}/cancel")
    assert r.json()["status"] == "canceled"
    assert await _stock(client, PRODUCT_BEAUTY) == before
    assert (await client.post(f"{API}/orders/{order['id']}/cancel")).status_code == 409


async def test_idempotent_checkout(client):
    before = await _stock(client, PRODUCT_BEAUTY)
    cart_id = await _cart(client, items=[(PRODUCT_BEAUTY, 1)])
    key = uuid.uuid4().hex
    first = await _checkout(client, cart_id, key)
    again = await _checkout(client, cart_id, key)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.headers["idempotent-replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert await _stock(client, PRODUCT_BEAUTY) == before - 1

    other_cart = await _cart(client, items=[(PRODUCT_BEAUTY, 1)])
    assert (await _checkout(client, other_cart, key)).status_code == 409
    await client.post(f"{API}/orders/{first.json()['id']}/cancel")


async def test_concurrent_retries_with_same_key_create_one_order(client):
    cart_id = await _cart(client, items=[(PRODUCT_BEAUTY, 1)])
    key = uuid.uuid4().hex
    results = await asyncio.gather(*(_checkout(client, cart_id, key) for _ in range(5)))
    assert sorted(r.status_code for r in results) == [200, 200, 200, 200, 201]
    assert len({r.json()["id"] for r in results}) == 1
    await client.post(f"{API}/orders/{results[0].json()['id']}/cancel")


async def test_no_oversell_under_concurrency(client):
    stock = await _stock(client, PRODUCT_PC)
    carts = [await _cart(client, items=[(PRODUCT_PC, 1)]) for _ in range(stock + 3)]
    results = await asyncio.gather(*(_checkout(client, c) for c in carts))
    codes = [r.status_code for r in results]
    assert codes.count(201) == stock
    assert codes.count(409) == 3
    assert all(r.json()["error"]["code"] == "out_of_stock" for r in results if r.status_code == 409)
    assert await _stock(client, PRODUCT_PC) == 0
    for r in results:
        if r.status_code == 201:
            await client.post(f"{API}/orders/{r.json()['id']}/cancel")
    assert await _stock(client, PRODUCT_PC) == stock


async def test_order_state_machine(client):
    cart_id = await _cart(client, customer=CUSTOMER_2, items=[(PRODUCT_BEAUTY, 1)])
    order_id = (await _checkout(client, cart_id)).json()["id"]

    async def move(status):
        return await client.patch(f"{API}/orders/{order_id}/status", json={"status": status})

    assert (await move("delivered")).status_code == 409
    for status in ("approved", "invoiced", "shipped", "delivered"):
        r = await move(status)
        assert r.status_code == 200, r.text
    assert r.json()["delivered_customer_at"] is not None
    assert (await move("canceled")).status_code == 409


async def test_customer_order_history_includes_olist_orders(client):
    r = await client.get(f"{API}/customers/{CUSTOMER_1}/orders")
    sources = {o["source"] for o in r.json()["items"]}
    assert "olist" in sources
