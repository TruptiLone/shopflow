import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.core.errors import InvalidState, NotFound, ValidationFailed
from shopflow.domain.models import Cart, CartItem, CartStatus, Customer, Product
from shopflow.domain.schemas import CartLine, CartOut

ZERO = Decimal("0.00")


def cart_view(cart: Cart) -> CartOut:
    """Carts show live prices; the price is only frozen onto the order at checkout."""
    lines: list[CartLine] = []
    for item in cart.items:
        p = item.product
        unit = p.price or ZERO
        freight = (p.freight_estimate or ZERO) * item.quantity
        lines.append(
            CartLine(
                product_id=p.id,
                name=p.name,
                quantity=item.quantity,
                unit_price=unit,
                line_total=unit * item.quantity,
                freight_estimate=freight,
            )
        )
    items_total = sum((line.line_total for line in lines), ZERO)
    freight_total = sum((line.freight_estimate for line in lines), ZERO)
    return CartOut(
        id=cart.id,
        customer_id=cart.customer_id,
        status=cart.status,
        items=lines,
        items_total=items_total,
        freight_total=freight_total,
        grand_total=items_total + freight_total,
    )


class CartService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _load(self, cart_id: uuid.UUID, *, for_update: bool = False) -> Cart:
        stmt = select(Cart).where(Cart.id == cart_id)
        if for_update:
            stmt = stmt.with_for_update()
        cart = await self.session.scalar(stmt)
        if cart is None:
            raise NotFound(f"cart {cart_id} not found")
        return cart

    async def _load_open(self, cart_id: uuid.UUID) -> Cart:
        cart = await self._load(cart_id, for_update=True)
        if cart.status != CartStatus.open:
            raise InvalidState(f"cart {cart_id} is {cart.status.value}")
        return cart

    async def _refresh(self, cart: Cart) -> Cart:
        await self.session.refresh(cart, ["items"])
        return cart

    async def create(self, customer_id: str) -> CartOut:
        if await self.session.get(Customer, customer_id) is None:
            raise NotFound(f"customer {customer_id} not found")
        cart = Cart(customer_id=customer_id, status=CartStatus.open)
        self.session.add(cart)
        await self.session.commit()
        return cart_view(await self._refresh(cart))

    async def get(self, cart_id: uuid.UUID) -> CartOut:
        return cart_view(await self._load(cart_id))

    async def add_item(self, cart_id: uuid.UUID, product_id: str, quantity: int) -> CartOut:
        cart = await self._load_open(cart_id)
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFound(f"product {product_id} not found")
        if not product.is_active or product.price is None:
            raise ValidationFailed(f"product {product_id} is not for sale")

        existing = next((i for i in cart.items if i.product_id == product_id), None)
        if existing:
            existing.quantity += quantity
        else:
            cart.items.append(CartItem(product_id=product_id, quantity=quantity))
        await self.session.commit()
        return cart_view(await self._refresh(cart))

    async def update_item(self, cart_id: uuid.UUID, product_id: str, quantity: int) -> CartOut:
        cart = await self._load_open(cart_id)
        item = next((i for i in cart.items if i.product_id == product_id), None)
        if item is None:
            raise NotFound(f"product {product_id} is not in cart")
        item.quantity = quantity
        await self.session.commit()
        return cart_view(await self._refresh(cart))

    async def remove_item(self, cart_id: uuid.UUID, product_id: str) -> CartOut:
        cart = await self._load_open(cart_id)
        item = next((i for i in cart.items if i.product_id == product_id), None)
        if item is None:
            raise NotFound(f"product {product_id} is not in cart")
        cart.items.remove(item)
        await self.session.commit()
        return cart_view(await self._refresh(cart))
