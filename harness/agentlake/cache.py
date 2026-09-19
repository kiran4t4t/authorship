"""Result cache models.

We simulate caches rather than measure a real one so that two keying strategies
can be compared on the identical query stream. A real engine gives you one cache
and no counterfactual.

`ExactTextCache` models what analytical engines conventionally ship: a result
cache keyed on the query string. `PlanCache` models the semantics-aware
alternative, keyed on the optimised plan.
"""

from __future__ import annotations

import dataclasses
from collections import OrderedDict

from .engine import QueryStats


@dataclasses.dataclass
class CacheStats:
    """Hit/miss counters and the work a cache avoided.

    Attributes:
        hits: Lookups served from cache.
        misses: Lookups that had to execute.
        rows_scanned_saved: Rows not scanned because of a hit. The cache's value
            in the same unit as the scan-amplification analysis.
    """

    hits: int = 0
    misses: int = 0
    rows_scanned_saved: int = 0

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups served from cache; 0.0 if never consulted."""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class _LRUCache:
    """Shared LRU machinery. Subclasses supply the key."""

    def __init__(self, capacity: int = 512) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._entries: OrderedDict[str, int] = OrderedDict()
        self.stats = CacheStats()

    def _key(self, stat: QueryStats) -> str:
        raise NotImplementedError

    def lookup(self, stat: QueryStats) -> bool:
        """Record a lookup for `stat`, returning whether it hit.

        Failed queries are never cached: an error is not a result. They are not
        counted as lookups either, since a cache would not be consulted for a
        query that does not plan.
        """
        if stat.error is not None:
            return False
        key = self._key(stat)
        if key in self._entries:
            self._entries.move_to_end(key)
            self.stats.hits += 1
            self.stats.rows_scanned_saved += self._entries[key]
            return True
        self.stats.misses += 1
        self._entries[key] = stat.rows_scanned
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return False


class ExactTextCache(_LRUCache):
    """Keyed on normalised query text. What engines conventionally ship."""

    def _key(self, stat: QueryStats) -> str:
        return stat.text_key


class PlanCache(_LRUCache):
    """Keyed on the optimised operator tree.

    Catches semantically equivalent rewrites that `ExactTextCache` misses, which
    is precisely the correction-phase pattern.
    """

    def _key(self, stat: QueryStats) -> str:
        return stat.plan_key
