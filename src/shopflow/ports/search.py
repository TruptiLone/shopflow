"""Search port. Phase 1 uses Postgres ILIKE; the OpenSearch phase replaces it with a
typo-tolerant adapter behind the same interface."""

from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.domain.models import Category, Product


class SearchBackend(Protocol):
    async def search_product_ids(
        self, session: AsyncSession, q: str, limit: int, offset: int
    ) -> tuple[list[str], int]: ...


class PostgresSearch:
    async def search_product_ids(
        self, session: AsyncSession, q: str, limit: int, offset: int
    ) -> tuple[list[str], int]:
        pattern = f"%{q.strip()}%"
        cond = (
            Product.is_active.is_(True),
            or_(
                Product.name.ilike(pattern),
                Category.name_en.ilike(pattern),
                Category.name_pt.ilike(pattern),
            ),
        )
        base = select(Product.id).outerjoin(Category).where(*cond)
        total = await session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await session.scalars(
            base.order_by(Product.review_count.desc(), Product.id).limit(limit).offset(offset)
        )
        return list(rows), int(total or 0)
