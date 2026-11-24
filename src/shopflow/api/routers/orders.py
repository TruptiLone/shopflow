from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from shopflow.api.deps import OrderDep, PageDep
from shopflow.domain.models import OrderStatus
from shopflow.domain.schemas import OrderCreate, OrderOut, OrderStatusUpdate, Page

router = APIRouter(tags=["orders"])


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Replay of an earlier request with the same key"}},
)
async def create_order(
    body: OrderCreate,
    svc: OrderDep,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=100)],
):
    """Check out a cart. `Idempotency-Key` is required: retries with the same key return
    the original order instead of creating a second one."""
    order, replayed = await svc.create_from_cart(body.cart_id, idempotency_key)
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return order


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: str, svc: OrderDep):
    return await svc.get(order_id)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: str, svc: OrderDep):
    return await svc.transition(order_id, OrderStatus.canceled)


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
async def update_status(order_id: str, body: OrderStatusUpdate, svc: OrderDep):
    """Fulfilment hook (approve/ship/deliver). Becomes event-driven in the queue phase."""
    return await svc.transition(order_id, body.status)


@router.get("/customers/{customer_id}/orders", response_model=Page[OrderOut])
async def customer_orders(customer_id: str, svc: OrderDep, page: PageDep):
    return await svc.list_for_customer(customer_id, page.limit, page.offset)
