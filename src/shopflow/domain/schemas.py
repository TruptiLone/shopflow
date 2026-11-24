"""Pydantic API contracts. Kept separate from ORM models so the wire format can evolve
independently of the storage schema."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from shopflow.domain.models import CartStatus, OrderStatus

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- catalog ---------------------------------------------------------------


class CategoryOut(ORM):
    slug: str
    name_en: str
    name_pt: str
    product_count: int = 0


class ProductSummary(ORM):
    id: str
    name: str
    category_slug: str | None
    category_name: str | None = None
    price: Decimal | None
    review_avg: Decimal | None
    review_count: int
    in_stock: bool


class ProductDetail(ProductSummary):
    seller_id: str | None
    freight_estimate: Decimal | None
    stock: int
    photos_qty: int | None
    weight_g: int | None
    length_cm: int | None
    height_cm: int | None
    width_cm: int | None
    version: int


class ReviewOut(ORM):
    review_id: str
    order_id: str
    score: int
    title: str | None = Field(description="English (machine-translated) title when available")
    message: str | None = Field(description="English (machine-translated) message when available")
    title_pt: str | None
    message_pt: str | None
    translated: bool
    created_at: datetime | None


# ---- cart ------------------------------------------------------------------


class CartCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=32)


class CartItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0, le=50)


class CartItemUpdate(BaseModel):
    quantity: int = Field(gt=0, le=50)


class CartLine(BaseModel):
    product_id: str
    name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    freight_estimate: Decimal


class CartOut(BaseModel):
    id: uuid.UUID
    customer_id: str
    status: CartStatus
    items: list[CartLine]
    items_total: Decimal
    freight_total: Decimal
    grand_total: Decimal


# ---- orders ----------------------------------------------------------------


class OrderCreate(BaseModel):
    cart_id: uuid.UUID


class OrderItemOut(ORM):
    item_seq: int
    product_id: str
    seller_id: str | None
    price: Decimal
    freight_value: Decimal


class OrderOut(ORM):
    id: str
    customer_id: str
    status: OrderStatus
    source: str
    purchased_at: datetime
    approved_at: datetime | None
    delivered_customer_at: datetime | None
    estimated_delivery_at: datetime | None
    items_total: Decimal
    freight_total: Decimal
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
