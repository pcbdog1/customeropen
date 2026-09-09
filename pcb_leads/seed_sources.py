from __future__ import annotations

from .search import SearchResult


def germany_seed_results(query: str, limit: int) -> list[SearchResult]:
    """Return local Germany seeds.

    The open-source distribution intentionally ships without real company
    records. Import seeds through ``inputs/domain_seed.csv`` instead.
    """
    return []


def seed_results_for(country: str, industry: str, limit: int) -> list[SearchResult]:
    """Return built-in company seeds; empty in the public distribution."""
    return []
