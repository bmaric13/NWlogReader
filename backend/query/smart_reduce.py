"""Filter #3: Smart Reduce — scoring, dedup, table slicing, structured view."""
import hashlib
import math
import re
from dataclasses import dataclass, field
from backend.query.filter1 import ChunkResult

FAILURE_KEYWORDS = re.compile(
    r"err-?disable|down|flap|CRC|input error|discard|overrun|SFP|DOM|"
    r"link not connected|LACP|vPC|STP|BPDU|UDLD|adjacency|timeout|reset|"
    r"suspended|notconnect|inactive|shutdown",
    re.IGNORECASE,
)


@dataclass
class ReducedView:
    element: str
    top_cards: list[dict]       # key/value summary cards
    alerts: list[str]           # high-priority warning lines
    timeline: list[dict]        # {timestamp, message}
    evidence: list[ChunkResult] # top-N deduplicated, scored
    daemon_hints: list          # list[DaemonHint] from daemons.py


def dedup_results(results: list[ChunkResult]) -> list[ChunkResult]:
    """Remove near-duplicate chunks (same title + similar body start)."""
    seen: set[str] = set()
    out: list[ChunkResult] = []
    for r in results:
        key = hashlib.md5(
            (r.title.lower().strip() + r.body_preview[:200].lower().strip()).encode()
        ).hexdigest()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _is_table_line(line: str) -> bool:
    return bool(re.search(r"\s{2,}|\t", line)) and len(line.strip()) > 10


def slice_table(body: str, canonical: str) -> str:
    """
    From a wide table, keep: header rows + rows matching element + 1 context row each side.
    """
    lines = body.splitlines()
    if not lines:
        return body

    # Find header rows (typically first 1-3 lines of a table)
    header_rows: list[str] = []
    data_start = 0
    for i, line in enumerate(lines[:5]):
        if _is_table_line(line) or re.match(r"\s*[-=]{3,}", line):
            header_rows.append(line)
            data_start = i + 1
        else:
            break

    canonical_lower = canonical.lower()
    match_indices: list[int] = []
    for i, line in enumerate(lines[data_start:], start=data_start):
        if canonical_lower in line.lower():
            match_indices.append(i)

    if not match_indices:
        # No match found — return original (up to 30 lines)
        return "\n".join(lines[:30])

    # Collect header + matching rows + 1 context line above/below
    keep: set[int] = set()
    for idx in match_indices:
        keep.add(idx)
        if idx > data_start:
            keep.add(idx - 1)
        if idx + 1 < len(lines):
            keep.add(idx + 1)

    result_lines = header_rows + [lines[i] for i in sorted(keep)]
    return "\n".join(result_lines)


_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?|"
    r"\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)


def _extract_timeline(results: list[ChunkResult]) -> list[dict]:
    events: list[dict] = []
    for r in results:
        if r.domain == "LOGS":
            for line in r.body_preview.splitlines():
                m = _TS_RE.search(line)
                if m and FAILURE_KEYWORDS.search(line):
                    events.append({"timestamp": m.group(1), "message": line.strip()[:200]})
    # Deduplicate and sort
    seen: set[str] = set()
    unique: list[dict] = []
    for e in events:
        key = e["message"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:20]


def _extract_cards(results: list[ChunkResult], canonical: str) -> list[dict]:
    """Build top summary cards from interface-domain results."""
    cards: list[dict] = []
    seen_keys: set[str] = set()

    def _add(key: str, value: str, status: str = "normal") -> None:
        if key not in seen_keys and value:
            seen_keys.add(key)
            cards.append({"key": key, "value": value, "status": status})

    for r in results:
        if r.domain != "INTERFACES":
            continue
        body = r.body_preview

        # Admin/Oper state
        m = re.search(r"is\s+(up|down|admin(?:istratively)?\s+down)", body, re.IGNORECASE)
        if m:
            state = m.group(1).lower()
            _add("State", state, "red" if "down" in state else "green")

        # Speed/duplex
        m = re.search(r"(\d+[GMK]?b(?:ps|it)?(?:/s)?(?:,?\s*(?:full|half))?)", body, re.IGNORECASE)
        if m:
            _add("Speed", m.group(1))

        # MTU
        m = re.search(r"MTU\s+(\d+)\s+bytes", body, re.IGNORECASE)
        if m:
            _add("MTU", f"{m.group(1)} bytes")

        # Error counters
        m = re.search(r"(\d+)\s+CRC", body, re.IGNORECASE)
        if m and m.group(1) != "0":
            _add("CRC Errors", m.group(1), "red")

        # Description
        m = re.search(r"Description:\s*(.+)", body, re.IGNORECASE)
        if m:
            _add("Description", m.group(1).strip()[:60])

    return cards[:8]


def _extract_alerts(results: list[ChunkResult]) -> list[str]:
    alerts: list[str] = []
    seen: set[str] = set()
    for r in results:
        for line in r.body_preview.splitlines():
            if FAILURE_KEYWORDS.search(line) and line.strip():
                key = line.strip()[:100]
                if key not in seen:
                    seen.add(key)
                    alerts.append(line.strip()[:200])
    return alerts[:10]


def smart_reduce(
    results: list[ChunkResult],
    element: str,
    top_n: int = 50,
) -> ReducedView:
    """
    Produce a structured ReducedView from raw query results.
    """
    deduped = dedup_results(results)

    # Score each result
    from backend.query.filter1 import _score
    scored = [_score(r, element) for r in deduped]
    scored.sort(key=lambda r: r.relevance_score, reverse=True)
    top = scored[:top_n]

    # Slice tables
    for r in top:
        if re.search(r"\s{2,}", r.body_preview):
            r.body_preview = slice_table(r.body_preview, element)

    from backend.query.daemons import infer_daemons
    daemon_hints = infer_daemons(element, top)

    return ReducedView(
        element=element,
        top_cards=_extract_cards(top, element),
        alerts=_extract_alerts(top),
        timeline=_extract_timeline(top),
        evidence=top,
        daemon_hints=daemon_hints,
    )
