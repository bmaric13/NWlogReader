"""Filter #2: domain filter applied to element query results."""
from backend.query.filter1 import ChunkResult


def apply_domain_filter(results: list[ChunkResult], domain: str) -> list[ChunkResult]:
    """
    Keep results matching the given domain.
    UNKNOWN chunks are included as a soft supplement when the strict match
    returns fewer than 5 results (avoids empty domain tabs when domain
    detection is incomplete).
    """
    if not domain or domain.upper() == "ALL":
        return results
    domain_upper = domain.upper()
    matched = [r for r in results if r.domain.upper() == domain_upper]
    if len(matched) < 5:
        # Supplement with UNKNOWN chunks (they may belong to this domain
        # but weren't categorized) — append after domain-specific, lower priority
        unknown = [r for r in results if r.domain == "UNKNOWN"]
        seen = {r.chunk_id for r in matched}
        matched += [r for r in unknown if r.chunk_id not in seen]
    return matched
