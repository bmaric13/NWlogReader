"""Orchestrate ingestion: detect file type, stream, chunk, index, emit progress."""
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Callable

from backend.db import open_for_ingest, create_indexes
from backend.ingest.tgz_reader import stream_members
from backend.ingest.flat_reader import stream_flat
from backend.ingest.chunker import chunk_nxos, chunk_ios, chunk_auto
from backend.ingest.indexer import index_prepared_chunks
from backend.ingest.relationships import extract_relationships
from backend.ingest.device_info import extract_device_info
from backend.ingest.vrf_extractor import extract_vrf_relationships
from backend.ingest.policy_extractor import extract_policy_relationships
from backend.normalize.domain import detect_domain, UNKNOWN
from backend.normalize.entities import extract_entities
from backend.session import get_session_db_path, update_session_status

# Parallel CPU threads for entity extraction / chunking workers.
# Use (cpu_count - 1), floor 4, cap 32.  Regex + entity extraction both
# release the GIL so threads run truly parallel across all physical cores.
_NTHREADS = max(4, min((os.cpu_count() or 4) - 1, 32))
# Max futures in-flight before draining.
# Deeper queue keeps all threads fed while the main thread reads/decompresses.
_MAX_INFLIGHT = _NTHREADS * 8
# Minimum seconds between progress callbacks
_PROGRESS_INTERVAL = 0.5


def _is_tgz(path: Path) -> bool:
    if path.suffix.lower() in (".tgz", ".gz") or str(path).endswith(".tar.gz"):
        return True
    # Detect by gzip magic bytes (1f 8b) — catches extension-less archives
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"\x1f\x8b"
    except Exception:
        return False


def _is_nxos_file(name: str, lines: list[str]) -> bool:
    return len(lines) < 3000 and not any(
        line.strip().startswith("----") for line in lines[:20]
    )


# ── Pure CPU workers (safe to run in thread pool) ─────────────────────────────

def _chunk_domain(ch, file_domain: str) -> str:
    """Return the best domain for one chunk.
    If the file-level domain is known, use it.
    Otherwise detect from chunk title then chunk body (first 60 lines).
    This ensures that 'interface Gi1/1/2' stanzas inside a flat show-tech
    file get CONFIG, 'show interfaces ...' sections get INTERFACES, etc.
    """
    if file_domain != UNKNOWN:
        return file_domain
    d = detect_domain("", [ch.title])
    if d == UNKNOWN:
        d = detect_domain(ch.source_name, [])
    if d == UNKNOWN:
        d = detect_domain("", ch.lines[:60])
    return d


def _prepare_member(name: str, lines: list[str]) -> tuple[list, str]:
    """Chunk + domain detect + entity extract for one file. Returns (prepared, domain)."""
    file_domain = detect_domain(name, lines[:80])
    chunks = chunk_nxos(name, lines) if _is_nxos_file(name, lines) else chunk_ios(name, lines)
    prepared = []
    for ch in chunks:
        domain = _chunk_domain(ch, file_domain)
        body = "\n".join(ch.lines)
        entities = extract_entities(ch.title + "\n" + body)
        prepared.append((ch, domain, entities))
    return prepared, file_domain


def _prepare_chunk_batch(chunks: list, file_domain: str) -> list:
    """Entity extraction for a batch of pre-chunked content.
    Per-chunk domain detection is applied when the file-level domain is UNKNOWN."""
    prepared = []
    for ch in chunks:
        domain = _chunk_domain(ch, file_domain)
        body = "\n".join(ch.lines)
        entities = extract_entities(ch.title + "\n" + body)
        prepared.append((ch, domain, entities))
    return prepared


# ── Progress throttle ─────────────────────────────────────────────────────────

class _Throttle:
    def __init__(self, interval: float):
        self._interval = interval
        self._last = 0.0

    def emit(self, stats: dict, progress_cb, current_file: str, force: bool = False) -> None:
        now = time.monotonic()
        if progress_cb and (force or now - self._last >= self._interval):
            self._last = now
            progress_cb({**stats, "status": "indexing", "current_file": current_file})


# ── Main ingest entry point ───────────────────────────────────────────────────

def ingest(
    session_id: str,
    file_path: str | Path,
    progress_cb: Callable[[dict], None] | None = None,
) -> dict:
    file_path = Path(file_path)
    db_path = get_session_db_path(session_id)
    conn = open_for_ingest(db_path)   # tables only — indexes built after ingest
    update_session_status(session_id, "indexing", conn)

    stats = {
        "members_total": 0,
        "members_done": 0,
        "chunks_total": 0,
        "entities_found": 0,
        "domain_counts": {},
        "errors": [],
    }

    throttle = _Throttle(_PROGRESS_INTERVAL)

    try:
        if _is_tgz(file_path):
            _ingest_tgz(conn, session_id, file_path, stats, progress_cb, throttle)
        else:
            _ingest_flat(conn, session_id, file_path, stats, progress_cb, throttle)

        if progress_cb:
            progress_cb({**stats, "status": "indexing", "current_file": "building indexes…"})
        create_indexes(conn)   # build indexes in one sorted pass now that data is loaded

        if progress_cb:
            progress_cb({**stats, "status": "indexing", "current_file": "building relationships…"})
        rel_count = extract_relationships(conn, session_id)
        stats["relationships_found"] = rel_count
        extract_device_info(conn, session_id)

        if progress_cb:
            progress_cb({**stats, "status": "indexing", "current_file": "extracting VRF topology…"})
        vrf_count = extract_vrf_relationships(conn, session_id)
        stats["relationships_found"] = stats.get("relationships_found", 0) + vrf_count

        if progress_cb:
            progress_cb({**stats, "status": "indexing", "current_file": "extracting policy graph…"})
        pol_count = extract_policy_relationships(conn, session_id)
        stats["relationships_found"] = stats.get("relationships_found", 0) + pol_count

        # Final entity count (once, at the end)
        row = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE session_id=?", [session_id]
        ).fetchone()
        stats["entities_found"] = row[0] if row else 0

        update_session_status(session_id, "ready", conn)
        if progress_cb:
            progress_cb({**stats, "status": "done"})
    except Exception as e:
        update_session_status(session_id, "error", conn)
        stats["errors"].append(str(e))
        if progress_cb:
            progress_cb({**stats, "status": "error", "error": str(e)})
    finally:
        conn.close()

    return stats


# ── TGZ ingestion (multi-member, parallel prep) ───────────────────────────────

def _flush_future(
    future: Future,
    name: str,
    conn,
    session_id: str,
    stats: dict,
    seen_hashes: set,
    progress_cb,
    throttle: _Throttle,
) -> None:
    try:
        prepared, domain = future.result()
        n = index_prepared_chunks(conn, session_id, prepared, seen_hashes)
        stats["chunks_total"] += n
        stats["members_done"] += 1
        stats["domain_counts"][domain] = stats["domain_counts"].get(domain, 0) + n
        throttle.emit(stats, progress_cb, name)
    except Exception as e:
        stats["errors"].append(f"{name}: {e}")
        stats["members_done"] += 1


def _ingest_tgz(conn, session_id, path, stats, progress_cb, throttle):
    import tarfile

    seen_hashes: set = set()
    pending: deque[tuple[Future, str]] = deque()

    # Single-pass: iterate lazily through the gzip stream — no double-read.
    # members_total is incremented as members are discovered so the progress
    # bar shows a running count rather than an upfront total.
    with ThreadPoolExecutor(max_workers=_NTHREADS) as pool:
        with tarfile.open(str(path), "r:gz") as tf:
            for member in tf:               # lazy sequential iteration
                if not member.isfile():
                    continue
                fobj = tf.extractfile(member)
                if not fobj:
                    continue
                try:
                    content = fobj.read().decode("utf-8", errors="replace")
                except Exception:
                    stats["members_done"] += 1
                    continue
                lines = content.splitlines()
                name = member.name
                stats["members_total"] += 1  # discovered; update before submit
                future = pool.submit(_prepare_member, name, lines)
                pending.append((future, name))

                while len(pending) >= _MAX_INFLIGHT:
                    done_future, done_name = pending.popleft()
                    _flush_future(done_future, done_name, conn, session_id,
                                  stats, seen_hashes, progress_cb, throttle)

        while pending:
            done_future, done_name = pending.popleft()
            _flush_future(done_future, done_name, conn, session_id,
                          stats, seen_hashes, progress_cb, throttle)


# Flat file sub-batch size: index after every N chunks to bound peak memory.
# For a 500MB IOS file, chunks can number in the thousands; loading all prepared
# triples at once before indexing caused multi-GB RSS spikes.
_FLAT_SUB_BATCH = 500


# ── Flat file ingestion (single file, parallel entity extraction) ─────────────

def _ingest_flat(conn, session_id, path, stats, progress_cb, throttle):
    members = list(stream_flat(path))
    stats["members_total"] = len(members)
    seen_hashes: set = set()
    # Absolute source path stored in each chunk so full text can be read on demand
    abs_source_path = str(path.resolve())

    # One pool for all members — avoids repeated thread-pool create/destroy overhead.
    with ThreadPoolExecutor(max_workers=_NTHREADS) as pool:
        for name, lines in members:
            try:
                domain = detect_domain(name, lines[:80])
                chunks = chunk_auto(name, lines)
                member_n = 0

                # Process in bounded sub-batches so we never hold all prepared
                # triples in memory at once (critical for large IOS flat files).
                for i in range(0, len(chunks), _FLAT_SUB_BATCH):
                    sub = chunks[i:i + _FLAT_SUB_BATCH]
                    tb = max(1, len(sub) // _NTHREADS)
                    batches = [sub[j:j + tb] for j in range(0, len(sub), tb)]
                    futures = [pool.submit(_prepare_chunk_batch, b, domain) for b in batches]
                    prepared = []
                    for f in futures:
                        prepared.extend(f.result())
                    n = index_prepared_chunks(conn, session_id, prepared, seen_hashes,
                                              source_path=abs_source_path)
                    member_n += n
                    stats["chunks_total"] += n
                    throttle.emit(stats, progress_cb, name)

                stats["members_done"] += 1
                stats["domain_counts"][domain] = stats["domain_counts"].get(domain, 0) + member_n
            except Exception as e:
                stats["errors"].append(f"{name}: {e}")
                stats["members_done"] += 1
