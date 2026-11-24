import hashlib
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.core.errors import Conflict, InvalidState, NotFound, OutOfStock, ValidationFailed
from shopflow.domain.models import (
    Cart,
    CartStatus,
    IdempotencyKey,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)
from shopflow.domain.schemas import OrderOut, Page
from shopflow.ports.cache import Cache, product_key
from shopflow.ports.events import record_event

S = OrderStatus

# Order state machine. Anything not listed is rejected.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    S.created: {S.approved, S.canceled},
    S.approved: {S.invoiced, S.processing, S.canceled},
    S.invoiced: {S.processing, S.shipped, S.canceled},
    S.processing: {S.shipped, S.unavailable, S.canceled},
    S.shipped: {S.delivered},
    S.delivered: set(),
    S.unavailable: set(),
    S.canceled: set(),
}

# Statuses where stock is still "held" by the order and must be returned on cancel.
_RESTOCK_ON_CANCEL = {S.created, S.approved, S.invoiced, S.processing}

CREATE_SCOPE = "orders.create"
ESTIMATED_DELIVERY = timedelta(days=12)  # close to Olist's median promise


def _order_payload(order: Order) -> dict:
    return {
        "order_id": order.id,
        "customer_id": order.customer_id,
        "status": order.status.value,
        "items_total": str(order.items_total),
        "freight_total": str(order.freight_total),
        "items": [
            {"product_id": i.product_id, "seller_id": i.seller_id, "price": str(i.price)}
            for i in order.items
        ],
    }


class OrderService:
    def __init__(self, session: AsyncSession, cache: Cache):
        self.session = session
        self.cache = cache

    async def _get(self, order_id: str, *, for_update: bool = False) -> Order:
        stmt = select(Order).where(Order.id == order_id)
        if for_update:
            stmt = stmt.with_for_update()
        order = await self.session.scalar(stmt)
        if order is None:
            raise NotFound(f"order {order_id} not found")
        return order

    async def get(self, order_id: str) -> OrderOut:
        return OrderOut.model_validate(await self._get(order_id))

    async def list_for_customer(self, customer_id: str, limit: int, offset: int) -> Page[OrderOut]:
        cond = Order.customer_id == customer_id
        total = await self.session.scalar(select(func.count(Order.id)).where(cond))
        rows = await self.session.scalars(
            select(Order)
            .where(cond)
            .order_by(Order.purchased_at.desc(), Order.id)
            .limit(limit)
            .offset(offset)
        )
        return Page(
            items=[OrderOut.model_validate(o) for o in rows],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def create_from_cart(
        self, cart_id: uuid.UUID, idempotency_key: str
    ) -> tuple[OrderOut, bool]:
        """Checkout. Returns (order, replayed).

        One transaction does all of: claim the idempotency key, lock the cart, reserve
        stock with conditional UPDATEs, write the order, close the cart, and record the
        outbox event. Any failure rolls everything back, including the key claim, so the
        client can safely retry with the same key.
        """
        request_hash = hashlib.sha256(str(cart_id).encode()).hexdigest()

        claimed = await self.session.scalar(
            insert(IdempotencyKey)
            .values(key=idempotency_key, scope=CREATE_SCOPE, request_hash=request_hash)
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        )
        if claimed is None:
            # A concurrent request with the same key blocks on the insert above until the
            # first one commits, so by now the stored row is final.
            existing = await self.session.get(IdempotencyKey, (idempotency_key, CREATE_SCOPE))
            assert existing is not None
            if existing.request_hash != request_hash:
                raise Conflict("Idempotency-Key was already used for a different request")
            order_id = existing.resource_id
            if order_id is None:
                raise Conflict("a request with this Idempotency-Key is still in progress")
            await self.session.rollback()
            return await self.get(order_id), True

        cart = await self.session.scalar(select(Cart).where(Cart.id == cart_id).with_for_update())
        if cart is None:
            raise NotFound(f"cart {cart_id} not found")
        if cart.status != CartStatus.open:
            raise InvalidState(f"cart {cart_id} is {cart.status.value}")
        if not cart.items:
            raise ValidationFailed("cart is empty")

        order = Order(
            id=uuid.uuid4().hex,
            customer_id=cart.customer_id,
            status=OrderStatus.created,
            source="api",
            purchased_at=datetime.now(UTC),
            estimated_delivery_at=datetime.now(UTC) + ESTIMATED_DELIVERY,
        )
        seq = 0
        items_total = freight_total = Decimal("0.00")
        # Lock rows in a stable order so two checkouts can't deadlock each other.
        for item in sorted(cart.items, key=lambda i: i.product_id):
            row = (
                await self.session.execute(
                    update(Product)
                    .where(
                        Product.id == item.product_id,
                        Product.is_active.is_(True),
                        Product.price.is_not(None),
                        Product.stock >= item.quantity,
                    )
                    .values(stock=Product.stock - item.quantity, version=Product.version + 1)
                    .returning(Product.price, Product.seller_id, Product.freight_estimate)
                )
            ).one_or_none()
            if row is None:
                raise OutOfStock(f"not enough stock for product {item.product_id}")
            price, seller_id, freight = row
            freight = freight or Decimal("0.00")
            for _ in range(item.quantity):  # Olist stores one row per unit
                seq += 1
                order.items.append(
                    OrderItem(
                        item_seq=seq,
                        product_id=item.product_id,
                        seller_id=seller_id,
                        price=price,
                        freight_value=freight,
                    )
                )
                items_total += price
                freight_total += freight

        order.items_total = items_total
        order.freight_total = freight_total
        cart.status = CartStatus.checked_out
        self.session.add(order)
        await self.session.flush()

        await self.session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == idempotency_key, IdempotencyKey.scope == CREATE_SCOPE)
            .values(resource_id=order.id)
        )
        await record_event(
            self.session,
            aggregate_type="order",
            aggregate_id=order.id,
            event_type="order.created",
            payload=_order_payload(order),
        )
        product_ids = {i.product_id for i in cart.items}
        await self.session.commit()
        await self.cache.invalidate(*(product_key(p) for p in product_ids))
        return OrderOut.model_validate(order), False

    async def transition(self, order_id: str, new_status: OrderStatus) -> OrderOut:
        order = await self._get(order_id, for_update=True)
        old = order.status
        if new_status not in TRANSITIONS[old]:
            raise InvalidState(f"cannot move order from {old.value} to {new_status.value}")

        now = datetime.now(UTC)
        if new_status == S.approved:
            order.approved_at = now
        elif new_status == S.shipped:
            order.delivered_carrier_at = now
        elif new_status == S.delivered:
            order.delivered_customer_at = now

        touched: set[str] = set()
        if new_status == S.canceled and old in _RESTOCK_ON_CANCEL:
            for product_id, qty in Counter(i.product_id for i in order.items).items():
                await self.session.execute(
                    update(Product)
                    .where(Product.id == product_id)
                    .values(stock=Product.stock + qty, version=Product.version + 1)
                )
                touched.add(product_id)

        order.status = new_status
        await record_event(
            self.session,
            aggregate_type="order",
            aggregate_id=order.id,
            event_type=f"order.{new_status.value}",
            payload={"order_id": order.id, "from": old.value, "to": new_status.value},
        )
        await self.session.commit()
        await self.cache.invalidate(*(product_key(p) for p in touched))
        return OrderOut.model_validate(order)
