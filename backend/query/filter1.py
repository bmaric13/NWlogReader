"""Filter #1: element-based query via entity join + ILIKE search (DuckDB)."""
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from backend.normalize.entities import parse_user_element, ABBREV_MAP

FAILURE_KEYWORDS = [
    "err-disable", "errdisable", "down", "flap", "CRC", "input error",
    "discard", "overrun", "SFP", "DOM", "link not connected", "LACP",
    "vPC", "STP", "BPDU", "UDLD", "BGP", "OSPF", "adjacency", "timeout",
    "reset", "shutdown", "notconnect", "suspended", "inactive",
]
_FAILURE_RE = re.compile("|".join(f"(?:{kw})" for kw in FAILURE_KEYWORDS), re.IGNORECASE)


@dataclass
class ChunkResult:
    chunk_id: int
    domain: str
    source_name: str
    title: str
    body_preview: str
    hit_count: int
    relevance_score: float
    health_items: list
    entity_matched: bool = False
    # Internal — not serialised to API
    _source_path: str | None = field(default=None, repr=False)
    _start_line: int = field(default=0, repr=False)
    _line_count: int = field(default=0, repr=False)
    _truncated: bool = field(default=False, repr=False)


def query_by_element(
    conn,
    session_id: str,
    user_input: str,
    domain: str | None = None,
    page: int = 0,
    limit: int = 50,
) -> list[ChunkResult]:
    parsed = parse_user_element(user_input)
    canonical = parsed["canonical"]

    # Build boundary regex (avoids Eth1/1 matching inside Eth1/10)
    boundary_re = _build_boundary_re(canonical)

    # Fast path: entity join (index scan — always fast)
    results = _entity_join_query(conn, session_id, canonical, domain, limit * 3)

    # ILIKE fallback — only run when entity join found nothing.
    # On large sessions (50K+ chunks) a full body ILIKE scan can take minutes;
    # entity join covers 99% of cases when entity extraction worked correctly.
    # For KEYWORD type (e.g. "show version", "show inventory") always use title search.
    if not results:
        if parsed["type"] == "KEYWORD":
            results = _keyword_title_query(conn, session_id, canonical, domain, limit * 3)
        if not results:
            ilike_results = _ilike_query(conn, session_id, parsed["fts_query"], domain, limit * 3, boundary_re)
            if ilike_results:
                results = ilike_results

    # For truncated chunks: load full body from source files (one pass per file)
    _batch_resolve_bodies(results)

    # Domain-aware line filtering
    for r in results:
        _enrich_preview(r, canonical, boundary_re)

    # Score and sort
    scored = [_score(r, canonical, boundary_re) for r in results]
    scored.sort(key=lambda r: r.relevance_score, reverse=True)

    start = page * limit
    return scored[start: start + limit]


def _build_boundary_re(canonical: str) -> re.Pattern:
    """
    Match canonical (and abbreviations) but NOT when followed by more digits,
    slashes, or dots — so Ethernet1/1 won't match Ethernet1/10 or Ethernet1/1.100.
    """
    variants: list[str] = [canonical]
    m = re.match(r"([A-Za-z\-]+)([\d/:.]+.*)", canonical)
    if m:
        prefix, num = m.group(1), m.group(2)
        for abbrev, full in ABBREV_MAP.items():
            if full.lower() == prefix.lower():
                variants.append(f"{abbrev}{num}")
                variants.append(f"{abbrev.capitalize()}{num}")
                variants.append(f"{abbrev.upper()}{num}")

    seen: set[str] = set()
    deduped: list[str] = []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            deduped.append(v)

    parts = [f"(?:{re.escape(v)})" for v in deduped]
    pattern = "(?:" + "|".join(parts) + r")(?=[^0-9/.]|$)"
    return re.compile(pattern, re.IGNORECASE)


def _entity_join_query(conn, session_id, canonical, domain, limit) -> list[ChunkResult]:
    domain_clause = "AND c.domain = ?" if domain else ""
    params = [session_id, canonical]
    if domain:
        params.append(domain)
    params.append(limit)

    sql = f"""
        SELECT c.chunk_id, c.domain, c.source_name, c.title,
               ct.body, ce.hit_count,
               c.source_path, c.start_line, c.line_count, ct.truncated
        FROM chunks c
        JOIN chunk_text ct ON ct.chunk_id = c.chunk_id
        JOIN chunk_entities ce ON ce.chunk_id = c.chunk_id
        JOIN entities e ON e.entity_id = ce.entity_id
        WHERE c.session_id = ?
          AND lower(e.canonical) = lower(?)
          {domain_clause}
        ORDER BY ce.hit_count DESC
        LIMIT ?
    """
    rows = conn.execute(sql, params).fetchall()
    results = _rows_to_results(rows)
    for r in results:
        r.entity_matched = True
    return results


def _keyword_title_query(conn, session_id, keyword, domain, limit) -> list[ChunkResult]:
    """Title ILIKE search for free-text keywords like 'show version', 'show inventory'."""
    domain_clause = "AND c.domain = ?" if domain else ""
    params = [session_id, f"%{keyword}%"]
    if domain:
        params.append(domain)
    params.append(limit)
    sql = f"""
        SELECT c.chunk_id, c.domain, c.source_name, c.title,
               ct.body, 1 AS hit_count,
               c.source_path, c.start_line, c.line_count, ct.truncated
        FROM chunks c
        JOIN chunk_text ct ON ct.chunk_id = c.chunk_id
        WHERE c.session_id = ?
          AND c.title ILIKE ?
          {domain_clause}
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_results(rows)
    except Exception:
        return []


def _ilike_query(conn, session_id, fts_query, domain, limit, boundary_re: re.Pattern) -> list[ChunkResult]:
    """
    ILIKE search with Python-side boundary post-filter to eliminate false positives.
    """
    terms = re.findall(r'"([^"]+)"', fts_query)
    if not terms:
        terms = [fts_query.strip().strip('"')]
    terms = [t for t in terms if t.strip()][:3]
    if not terms:
        return []

    domain_clause = "AND c.domain = ?" if domain else ""
    ilike_parts = " OR ".join(["ct.body ILIKE ? OR c.title ILIKE ?"] * len(terms))
    params: list = [session_id]
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    if domain:
        params.append(domain)
    params.append(limit)

    sql = f"""
        SELECT c.chunk_id, c.domain, c.source_name, c.title,
               ct.body, 1 AS hit_count,
               c.source_path, c.start_line, c.line_count, ct.truncated
        FROM chunks c
        JOIN chunk_text ct ON ct.chunk_id = c.chunk_id
        WHERE c.session_id = ?
          AND ({ilike_parts})
          {domain_clause}
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
        # Post-filter: boundary_re must match in title or stored body preview
        filtered = [r for r in rows if boundary_re.search(r[4] or "") or boundary_re.search(r[3] or "")]
        return _rows_to_results(filtered)
    except Exception:
        return []


def _batch_resolve_bodies(results: list[ChunkResult]) -> None:
    """
    For truncated chunks, read the correct line range from the source file on disk.
    Groups by source file and reads each file only ONCE per query (streaming up to
    the deepest line needed), so a 1 GB file is never fully loaded into memory.
    """
    # Group truncated results by source file
    by_file: dict[str, list[ChunkResult]] = {}
    for r in results:
        if r._truncated and r._source_path:
            by_file.setdefault(r._source_path, []).append(r)

    for source_path, file_results in by_file.items():
        try:
            src = Path(source_path)
            if not src.exists():
                continue
            # Only read up to the deepest line any chunk needs
            max_line = max(r._start_line + r._line_count for r in file_results)
            lines_cache: list[str] = []
            with src.open("r", encoding="utf-8", errors="replace", buffering=1 << 16) as fh:
                for lineno, line in enumerate(fh):
                    if lineno >= max_line:
                        break
                    lines_cache.append(line.rstrip("\n\r"))
            # Assign each result its slice
            for r in file_results:
                end = min(r._start_line + r._line_count, len(lines_cache))
                if r._start_line < end:
                    r.body_preview = "\n".join(lines_cache[r._start_line:end])
        except Exception:
            pass  # keep the 200-line stored preview


# ── Domain-aware preview filtering ──────────────────────────────────────────

def _enrich_preview(result: ChunkResult, canonical: str, boundary_re: re.Pattern) -> None:
    """
    Trim body_preview to only the lines relevant to the searched element.

    Strategy:
    1. CONFIG       → extract the interface stanza (interface X … !)
    2. Title match  → element IS the subject of this chunk; show full body (cap 300 lines)
    3. LOGS         → filter to matching lines ± 1 context line
    4. INTERFACES / HARDWARE → wider window (±60) so full interface block is captured
    5. Others       → matching lines ± 5 context lines, with table headers prepended
    """
    body = result.body_preview
    lines = body.splitlines()
    if not lines:
        return

    # ── 1. CONFIG: extract stanza ─────────────────────────────────────────────
    if result.domain == "CONFIG":
        stanza = _extract_config_stanza(lines, boundary_re)
        if stanza:
            result.body_preview = "\n".join(stanza)
            return

    # ── 2. Title match → dedicated chunk, show full body ─────────────────────
    # e.g. "show interface Ethernet1/1" or a file named for that interface.
    # Every line is relevant, no filtering needed.
    if boundary_re.search(result.title):
        result.body_preview = "\n".join(lines[:300])
        return

    # ── 3-5. Line-level filtering for multi-interface tables / logs ───────────
    # Context window: how many lines before/after each match to include.
    # INTERFACES/HARDWARE need large ctx_after because interface detail blocks
    # start with "Ethernet1/1 is up" and have 50+ lines of counters below it.
    if result.domain == "LOGS":
        ctx_before, ctx_after = 1, 1
    elif result.domain in ("INTERFACES", "HARDWARE"):
        ctx_before, ctx_after = 0, 60
    elif result.domain == "ROUTING":
        ctx_before, ctx_after = 3, 10
    else:
        ctx_before, ctx_after = 2, 5

    # Detect table header lines near the top of the chunk (first 10 lines).
    # Exclude interface-state lines like "Vlan1 is administratively down..."
    _IFACE_STATE_RE = re.compile(r"^\S+\s+is\s+(up|down|admin|notconnect|connected)", re.IGNORECASE)
    header_lines: list[str] = []
    for line in lines[:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if _IFACE_STATE_RE.match(stripped):
            break  # interface detail block, not a table — stop header detection
        if re.search(r"(?:Port|Interface|Admin|Oper|Status|Speed|Duplex|Vlan|Type|Name|Counter|Tx|Rx)", line, re.IGNORECASE):
            header_lines.append(line)
        elif re.match(r"^[-=]+$", stripped):
            header_lines.append(line)
        elif header_lines:
            break

    # Collect line ranges around element matches
    matching_ranges: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if boundary_re.search(line):
            matching_ranges.append((
                max(0, i - ctx_before),
                min(len(lines), i + ctx_after + 1),
            ))

    if not matching_ranges:
        result.body_preview = "\n".join(lines[:30])
        return

    # Merge overlapping/adjacent ranges
    merged: list[list[int]] = []
    for start, end in sorted(matching_ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    output_lines: list[str] = []
    total = 0

    # Prepend table headers for table-style domains
    if header_lines and result.domain in ("INTERFACES", "HARDWARE", "ROUTING", "UNKNOWN"):
        output_lines.extend(header_lines)
        output_lines.append("")

    prev_end = 0
    for start, end in merged:
        if total >= 200:
            output_lines.append("  ⋯ (more matches omitted)")
            break
        if prev_end > 0 and start > prev_end:
            output_lines.append("  ⋯")
        chunk_lines = lines[start:end]
        output_lines.extend(chunk_lines)
        total += len(chunk_lines)
        prev_end = end

    result.body_preview = "\n".join(output_lines)


def _extract_config_stanza(lines: list[str], boundary_re: re.Pattern) -> list[str]:
    """
    Extract the interface configuration stanza.
    Collects from 'interface <name>' up to the next top-level stanza or '!'.
    """
    in_stanza = False
    stanza: list[str] = []

    for line in lines:
        stripped = line.strip()

        if re.match(r"interface\s+\S", stripped, re.IGNORECASE):
            if boundary_re.search(stripped):
                in_stanza = True
                stanza = [line]
            elif in_stanza:
                break  # different interface stanza — done
            continue

        if in_stanza:
            stanza.append(line)
            if stripped == "!" and len(stanza) > 1:
                break

    return stanza if len(stanza) > 1 else []


def _rows_to_results(rows) -> list[ChunkResult]:
    results = []
    for row in rows:
        chunk_id   = row[0]
        domain     = row[1]
        source_name = row[2]
        title      = row[3]
        body       = row[4]
        hit_count  = row[5]
        source_path = row[6] if len(row) > 6 else None
        start_line = row[7] if len(row) > 7 else 0
        line_count = row[8] if len(row) > 8 else 0
        truncated  = bool(row[9]) if len(row) > 9 else False

        results.append(ChunkResult(
            chunk_id=chunk_id,
            domain=domain,
            source_name=source_name,
            title=title,
            body_preview=(body or ""),
            hit_count=int(hit_count or 1),
            relevance_score=0.0,
            health_items=[],
            _source_path=source_path,
            _start_line=int(start_line or 0),
            _line_count=int(line_count or 0),
            _truncated=truncated,
        ))
    return results


def _score(result: ChunkResult, canonical: str, boundary_re: re.Pattern | None = None) -> ChunkResult:
    score = 0.0
    title_lower = result.title.lower()
    canonical_lower = canonical.lower()
    body = result.body_preview

    if canonical_lower in title_lower:
        score += 0.60
    elif boundary_re and boundary_re.search(result.title):
        score += 0.55

    if boundary_re and boundary_re.search(body):
        score += 0.35
    elif canonical_lower in body.lower():
        score += 0.30
    elif result.entity_matched:
        score += 0.25

    if _FAILURE_RE.search(body):
        score += 0.15

    score += min(0.20, math.log1p(result.hit_count) * 0.05)

    if not result.entity_matched and canonical_lower not in title_lower and result.hit_count <= 1:
        score -= 0.30

    result.relevance_score = max(0.0, min(1.0, score))
    return result
