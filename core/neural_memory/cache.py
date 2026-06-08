"""Hot node cache for Hikari Neural Memory."""

import time
import logging
from typing import Optional, Any, Dict
from collections import OrderedDict

from .config import config

logger = logging.getLogger(__name__)


class MemoryCache:
    def __init__(self, max_size: int = None, ttl_seconds: int = None):
        self.max_size = max_size or config.CACHE_MAX_SIZE
        self.ttl_seconds = ttl_seconds or config.CACHE_TTL_SECONDS
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]

        if time.time() - timestamp > self.ttl_seconds:
            del self._cache[key]
            return None

        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any):
        if key in self._cache:
            self._cache.move_to_end(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = (value, time.time())

    def delete(self, key: str):
        if key in self._cache:
            del self._cache[key]

    def clear(self):
        self._cache.clear()

    def invalidate_pattern(self, pattern: str):
        keys_to_delete = [k for k in self._cache.keys() if pattern in k]
        for key in keys_to_delete:
            del self._cache[key]

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._cache)
        expired = sum(
            1
            for _, (_, ts) in self._cache.items()
            if time.time() - ts > self.ttl_seconds
        )
        return {
            "size": total,
            "max_size": self.max_size,
            "expired": expired,
            "ttl_seconds": self.ttl_seconds,
        }


node_cache = MemoryCache(max_size=500, ttl_seconds=3600)
context_cache = MemoryCache(max_size=100, ttl_seconds=300)
