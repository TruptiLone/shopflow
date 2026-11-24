from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.core.errors import NotFound
from shopflow.domain.models import Category, OrderItem, Product, Review
from shopflow.domain.schemas import (
    CategoryOut,
    Page,
    ProductDetail,
    ProductSummary,
    ReviewOut,
)
from shopflow.ports.cache import CATEGORIES_KEY, Cache, product_key
from shopflow.ports.search import SearchBackend

ProductSort = Literal["popular", "price_asc", "price_desc", "rating"]

_SORTS = {
    "popular": (Product.review_count.desc(), Product.id),
    "price_asc": (Product.price.asc().nulls_last(), Product.id),
    "price_desc": (Product.price.desc().nulls_last(), Product.id),
    "rating": (Product.review_avg.desc().nulls_last(), Product.review_count.desc(), Product.id),
}

PRODUCT_TTL = 300
CATEGORIES_TTL = 3600


def _summary(p: Product) -> ProductSummary:
    return ProductSummary(
        id=p.id,
        name=p.name,
        category_slug=p.category_slug,
        category_name=p.category.name_en if p.category else None,
        price=p.price,
        review_avg=p.review_avg,
        review_count=p.review_count,
        in_stock=p.stock > 0 and p.is_active and p.price is not None,
    )


def _detail(p: Product) -> ProductDetail:
    return ProductDetail(
        **_summary(p).model_dump(),
        seller_id=p.seller_id,
        freight_estimate=p.freight_estimate,
        stock=p.stock,
        photos_qty=p.photos_qty,
        weight_g=p.weight_g,
        length_cm=p.length_cm,
        height_cm=p.height_cm,
        width_cm=p.width_cm,
        version=p.version,
    )


class CatalogService:
    def __init__(self, session: AsyncSession, cache: Cache, search: SearchBackend):
        self.session = session
        self.cache = cache
        self.search_backend = search

    async def list_categories(self) -> list[CategoryOut]:
        if (cached := await self.cache.get(CATEGORIES_KEY)) is not None:
            return [CategoryOut.model_validate(c) for c in cached]
        count = func.count(Product.id).label("product_count")
        rows = await self.session.execute(
            select(Category, count)
            .outerjoin(Product, (Product.category_slug == Category.slug) & Product.is_active)
            .group_by(Category.slug)
            .order_by(count.desc(), Category.slug)
        )
        out = [
            CategoryOut(slug=c.slug, name_en=c.name_en, name_pt=c.name_pt, product_count=n)
            for c, n in rows
        ]
        await self.cache.set(CATEGORIES_KEY, [c.model_dump() for c in out], CATEGORIES_TTL)
        return out

    async def list_products(
        self,
        *,
        category: str | None,
        min_price: Decimal | None,
        max_price: Decimal | None,
        sort: ProductSort,
        limit: int,
        offset: int,
    ) -> Page[ProductSummary]:
        conds = [Product.is_active.is_(True)]
        if category:
            conds.append(Product.category_slug == category)
        if min_price is not None:
            conds.append(Product.price >= min_price)
        if max_price is not None:
            conds.append(Product.price <= max_price)

        total = await self.session.scalar(select(func.count(Product.id)).where(*conds))
        rows = await self.session.scalars(
            select(Product).where(*conds).order_by(*_SORTS[sort]).limit(limit).offset(offset)
        )
        return Page(
            items=[_summary(p) for p in rows], total=int(total or 0), limit=limit, offset=offset
        )

    async def get_product(self, product_id: str) -> ProductDetail:
        key = product_key(product_id)
        if (cached := await self.cache.get(key)) is not None:
            return ProductDetail.model_validate(cached)
        p = await self.session.get(Product, product_id)
        if p is None:
            raise NotFound(f"product {product_id} not found")
        detail = _detail(p)
        await self.cache.set(key, detail.model_dump(mode="json"), PRODUCT_TTL)
        return detail

    async def product_reviews(self, product_id: str, limit: int, offset: int) -> Page[ReviewOut]:
        if await self.session.get(Product, product_id) is None:
            raise NotFound(f"product {product_id} not found")
        has_product = (
            select(OrderItem.order_id)
            .where(OrderItem.order_id == Review.order_id, OrderItem.product_id == product_id)
            .exists()
        )
        total = await self.session.scalar(
            select(func.count()).select_from(Review).where(has_product)
        )
        rows = await self.session.scalars(
            select(Review)
            .where(has_product)
            .order_by(Review.created_at.desc().nulls_last(), Review.review_id)
            .limit(limit)
            .offset(offset)
        )
        items = [
            ReviewOut(
                review_id=r.review_id,
                order_id=r.order_id,
                score=r.score,
                title=r.title_en or r.title_pt,
                message=r.message_en or r.message_pt,
                title_pt=r.title_pt,
                message_pt=r.message_pt,
                translated=bool(r.title_en or r.message_en),
                created_at=r.created_at,
            )
            for r in rows
        ]
        return Page(items=items, total=int(total or 0), limit=limit, offset=offset)

    async def search(self, q: str, limit: int, offset: int) -> Page[ProductSummary]:
        ids, total = await self.search_backend.search_product_ids(self.session, q, limit, offset)
        if not ids:
            return Page(items=[], total=total, limit=limit, offset=offset)
        by_id = {
            p.id: p for p in await self.session.scalars(select(Product).where(Product.id.in_(ids)))
        }
        return Page(
            items=[_summary(by_id[i]) for i in ids if i in by_id],
            total=total,
            limit=limit,
            offset=offset,
        )
