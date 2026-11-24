import uuid

from fastapi import APIRouter, status

from shopflow.api.deps import CartDep
from shopflow.domain.schemas import CartCreate, CartItemIn, CartItemUpdate, CartOut

router = APIRouter(prefix="/carts", tags=["cart"])


@router.post("", response_model=CartOut, status_code=status.HTTP_201_CREATED)
async def create_cart(body: CartCreate, svc: CartDep):
    return await svc.create(body.customer_id)


@router.get("/{cart_id}", response_model=CartOut)
async def get_cart(cart_id: uuid.UUID, svc: CartDep):
    return await svc.get(cart_id)


@router.post("/{cart_id}/items", response_model=CartOut)
async def add_item(cart_id: uuid.UUID, body: CartItemIn, svc: CartDep):
    return await svc.add_item(cart_id, body.product_id, body.quantity)


@router.patch("/{cart_id}/items/{product_id}", response_model=CartOut)
async def update_item(cart_id: uuid.UUID, product_id: str, body: CartItemUpdate, svc: CartDep):
    return await svc.update_item(cart_id, product_id, body.quantity)


@router.delete("/{cart_id}/items/{product_id}", response_model=CartOut)
async def remove_item(cart_id: uuid.UUID, product_id: str, svc: CartDep):
    return await svc.remove_item(cart_id, product_id)
