from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from shopflow.api.deps import CatalogDep, PageDep
from shopflow.domain.schemas import CategoryOut, Page, ProductDetail, ProductSummary, ReviewOut
from shopflow.services.catalog import ProductSort

router = APIRouter(tags=["catalog"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(svc: CatalogDep):
    return await svc.list_categories()


@router.get("/products", response_model=Page[ProductSummary])
async def list_products(
    svc: CatalogDep,
    page: PageDep,
    category: str | None = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    sort: ProductSort = "popular",
):
    return await svc.list_products(
        category=category,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product(product_id: str, svc: CatalogDep):
    return await svc.get_product(product_id)


@router.get("/products/{product_id}/reviews", response_model=Page[ReviewOut])
async def product_reviews(product_id: str, svc: CatalogDep, page: PageDep):
    return await svc.product_reviews(product_id, page.limit, page.offset)


@router.get("/search", response_model=Page[ProductSummary])
async def search(
    svc: CatalogDep, page: PageDep, q: Annotated[str, Query(min_length=2, max_length=100)]
):
    return await svc.search(q, page.limit, page.offset)
