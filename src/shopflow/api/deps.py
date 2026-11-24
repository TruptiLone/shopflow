from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.core.config import get_settings
from shopflow.core.db import get_session
from shopflow.services.carts import CartService
from shopflow.services.catalog import CatalogService
from shopflow.services.orders import OrderService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class Pagination:
    def __init__(
        self,
        limit: Annotated[int | None, Query(ge=1)] = None,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ):
        s = get_settings()
        self.limit = min(limit or s.default_page_size, s.max_page_size)
        self.offset = offset


PageDep = Annotated[Pagination, Depends()]


def catalog_service(request: Request, session: SessionDep) -> CatalogService:
    return CatalogService(session, request.app.state.cache, request.app.state.search)


def cart_service(session: SessionDep) -> CartService:
    return CartService(session)


def order_service(request: Request, session: SessionDep) -> OrderService:
    return OrderService(session, request.app.state.cache)


CatalogDep = Annotated[CatalogService, Depends(catalog_service)]
CartDep = Annotated[CartService, Depends(cart_service)]
OrderDep = Annotated[OrderService, Depends(order_service)]
