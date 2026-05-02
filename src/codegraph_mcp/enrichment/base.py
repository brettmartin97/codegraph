from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EnrichmentResult:
    function_id: str
    purpose: str
    category: str
    importance: float
    tags: list[str]
    source: str


class BaseEnricher(ABC):
    """Abstract enricher — implement enrich_batch() for a specific backend."""

    @abstractmethod
    def enrich_batch(self, batch: list[dict]) -> list[EnrichmentResult]:
        """
        batch: list of {function_id, name, qualified_name, signature, summary, docstring, complexity, decorators}
        Returns EnrichmentResult for each item (best-effort; may return fewer than input).
        """

    def close(self) -> None:
        pass
