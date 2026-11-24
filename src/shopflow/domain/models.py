"""ORM models: the curated, application-facing schema.

Raw Olist CSVs land in the `staging` schema first (see shopflow.ingest); these tables
are what the API reads and writes.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(12, 2)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Category(Base):
    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(80), primary_key=True)  # Olist Portuguese key
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    name_pt: Mapped[str] = mapped_column(String(120), nullable=False)


class Seller(Base):
    __tablename__ = "sellers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    zip_code_prefix: Mapped[str | None] = mapped_column(String(5))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(2))


class Customer(TimestampMixin, Base):
    """Keyed by Olist's customer_unique_id (one per real person).

    Olist's per-order `customer_id` is folded into this during ingestion.
    """

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    zip_code_prefix: Mapped[str | None] = mapped_column(String(5))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(2))


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
        Index("ix_products_category_price", "category_slug", "price"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_slug: Mapped[str | None] = mapped_column(ForeignKey("categories.slug"))
    seller_id: Mapped[str | None] = mapped_column(ForeignKey("sellers.id"))

    price: Mapped[Decimal | None] = mapped_column(Money)
    freight_estimate: Mapped[Decimal | None] = mapped_column(Money)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    photos_qty: Mapped[int | None] = mapped_column(SmallInteger)
    weight_g: Mapped[int | None] = mapped_column(Integer)
    length_cm: Mapped[int | None] = mapped_column(Integer)
    height_cm: Mapped[int | None] = mapped_column(Integer)
    width_cm: Mapped[int | None] = mapped_column(Integer)

    review_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    review_avg: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))

    # Bumped on every write; cache keys and ETags will hang off this in the Redis phase.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    category: Mapped[Category | None] = relationship(lazy="joined")


class CartStatus(enum.StrEnum):
    open = "open"
    checked_out = "checked_out"
    abandoned = "abandoned"


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[CartStatus] = mapped_column(
        Enum(CartStatus, name="cart_status"), nullable=False, default=CartStatus.open
    )

    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CartItem.added_at",
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),)

    cart_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")


class OrderStatus(enum.StrEnum):
    """Olist's own status vocabulary, so historical and new orders share one model."""

    created = "created"
    approved = "approved"
    invoiced = "invoiced"
    processing = "processing"
    shipped = "shipped"
    delivered = "delivered"
    unavailable = "unavailable"
    canceled = "canceled"


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_customer_purchased", "customer_id", "purchased_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False, server_default="api")

    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_carrier_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_customer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items_total: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default="0")
    freight_total: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default="0")

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderItem.item_seq",
    )


class OrderItem(Base):
    """One row per unit, mirroring Olist (order_item_id is a 1..n sequence)."""

    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_product", "product_id"),)

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    item_seq: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    seller_id: Mapped[str | None] = mapped_column(ForeignKey("sellers.id"))
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    freight_value: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default="0")
    shipping_limit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    sequential: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    installments: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    value: Mapped[Decimal] = mapped_column(Money, nullable=False)


class Review(Base):
    """Olist review_id is not unique on its own, so the key is (review_id, order_id)."""

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_order", "order_id"),)

    review_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title_pt: Mapped[str | None] = mapped_column(Text)
    message_pt: Mapped[str | None] = mapped_column(Text)
    title_en: Mapped[str | None] = mapped_column(Text)
    message_en: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Translation(Base):
    """Content-addressed translation cache: each distinct string is translated once."""

    __tablename__ = "translations"

    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_lang: Mapped[str] = mapped_column(String(5), primary_key=True)
    source_lang: Mapped[str] = mapped_column(String(5), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    backend: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutboxEvent(Base):
    """Transactional outbox. Written in the same transaction as the state change;
    a relay (queue/Kafka phase) publishes unpublished rows and stamps published_at."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_unpublished", "id", postgresql_where=text("published_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
