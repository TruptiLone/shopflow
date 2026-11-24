"""Cache port. Phase 1 ships a no-op; the Redis phase swaps in a real adapter without
touching services. Services use cache-aside and call `invalidate` on writes."""

from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    async def invalidate(self, *keys: str) -> None: ...


class NoopCache:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        return None

    async def invalidate(self, *keys: str) -> None:
        return None


def product_key(product_id: str) -> str:
    return f"product:{product_id}"


CATEGORIES_KEY = "categories:all"
