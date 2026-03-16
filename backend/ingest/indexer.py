"""Index chunks into DuckDB: domain detect, entity extract, bulk insert."""
import hashlib
from backend.ingest.chunker import Chunk
from backend.normalize.domain import detect_domain
from backend.normalize.entities import extract_entities

# Commit after this many chunks — large batches mean fewer fsync round-trips.
COMMIT_EVERY = 2000


def _body_hash(chunk: Chunk) -> str:
    sig = chunk.title + "".join(chunk.lines[:5])
    return hashlib.md5(sig.encode("utf-8", errors="replace")).hexdigest()


def index_chunks(
    conn,
    session_id: str,
    chunks: list[Chunk],
    source_domain: str = "UNKNOWN",
) -> int:
    """Index a list of chunks (with entity extraction). Used by callers that
    don't pre-extract — wraps the prepared path."""
    prepared = []
    for chunk in chunks:
        first_lines = chunk.lines[:80]
        if source_domain != "UNKNOWN":
            domain = source_domain
        else:
            domain = detect_domain("", [chunk.title])
            if domain == "UNKNOWN":
                domain = detect_domain(chunk.source_name, [])
            if domain == "UNKNOWN":
                domain = detect_domain("", first_lines[:60])
        body = "\n".join(chunk.lines)
        entities = extract_entities(chunk.title + "\n" + body)
        prepared.append((chunk, domain, entities))
    return index_prepared_chunks(conn, session_id, prepared)


# Max lines stored in chunk_text during ingest.
# Full content is read from source_path on demand for "View Raw".
# 200 lines ≈ 16 KB per chunk vs up to 400 KB for 5000-line chunks.
# For a 600 MB file this cuts chunk_text writes from 600 MB → ~30 MB.
_PREVIEW_LINES = 500


def index_prepared_chunks(
    conn,
    session_id: str,
    prepared: list,          # [(Chunk, domain, entities), ...]
    seen_hashes: set | None = None,
    source_path: str | None = None,  # absolute path to source file (flat ingest)
) -> int:
    """
    Bulk-insert pre-prepared (chunk, domain, entities) triples.

    Strategy:
      1. Dedup in Python via seen_hashes.
      2. Pre-allocate chunk IDs with one sequence query.
      3. executemany for chunks table  — one round-trip.
      4. executemany for chunk_text    — one round-trip.
      5. Batch entity upsert + link    — one upsert pass + one link pass.

    This replaces the old per-row INSERT loop and eliminates most of the
    Python→DuckDB boundary crossings that made large ingests slow.
    """
    if seen_hashes is None:
        seen_hashes = set()

    # ── 1. Dedup ──────────────────────────────────────────────────────────────
    to_insert: list[tuple] = []   # (chunk, domain, entities, bh)
    for chunk, domain, entities in prepared:
        bh = _body_hash(chunk)
        if bh in seen_hashes:
            continue
        seen_hashes.add(bh)
        to_insert.append((chunk, domain, entities, bh))

    if not to_insert:
        return 0

    conn.begin()
    try:
        n = len(to_insert)

        # ── 2. Pre-allocate chunk IDs (single query) ──────────────────────────
        chunk_ids = [
            r[0] for r in
            conn.execute(f"SELECT nextval('seq_chunk_id') FROM range({n})").fetchall()
        ]

        # ── 3. Bulk insert chunks ─────────────────────────────────────────────
        chunk_rows = [
            (cid, session_id, ch.source_name, source_path, dom, ch.title,
             ch.start_line, len(ch.lines), bh)
            for cid, (ch, dom, _ents, bh) in zip(chunk_ids, to_insert)
        ]
        conn.executemany(
            """INSERT INTO chunks
               (chunk_id, session_id, source_name, source_path, domain, title,
                start_line, line_count, body_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            chunk_rows,
        )

        # ── 4. Bulk insert chunk_text (preview only — full text read on demand) ──
        text_rows = []
        for cid, (ch, _dom, _ents, _bh) in zip(chunk_ids, to_insert):
            truncated = len(ch.lines) > _PREVIEW_LINES
            preview_lines = ch.lines[:_PREVIEW_LINES]
            text_rows.append((cid, "\n".join(preview_lines), truncated))
        conn.executemany(
            "INSERT INTO chunk_text (chunk_id, body, truncated) VALUES (?, ?, ?)",
            text_rows,
        )

        # ── 5. Batch entity upsert + chunk_entity links ───────────────────────
        _batch_upsert_entities(conn, session_id, chunk_ids, to_insert)

        conn.commit()
        return n

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def _batch_upsert_entities(conn, session_id: str, chunk_ids: list, to_insert: list) -> None:
    """
    Upsert all entities for a batch of chunks in three passes:
      a) Collect unique (raw, normalized, canonical, entity_type) across all chunks.
      b) executemany upsert into entities table.
      c) Query back all entity_ids for our canonicals.
      d) executemany insert chunk_entities.
    """
    # a) Collect unique entities and per-chunk entity keys
    unique_ents: dict[tuple, str] = {}   # (canonical, type) -> raw  (last raw wins)
    chunk_ent_keys: list[list[tuple]] = []  # per chunk: [(canonical, type, count), ...]

    for _cid, (_ch, _dom, entities, _bh) in zip(chunk_ids, to_insert):
        key_counts: dict[tuple, int] = {}
        for ent in (entities or []):
            k = (ent.canonical, ent.entity_type)
            unique_ents[k] = ent.raw
            key_counts[k] = key_counts.get(k, 0) + 1
        chunk_ent_keys.append([(c, t, cnt) for (c, t), cnt in key_counts.items()])

    if not unique_ents:
        return

    # b) Batch upsert entities
    ent_rows = []
    for (canonical, etype), raw in unique_ents.items():
        ent_rows.append((session_id, raw, canonical, canonical, etype))

    conn.executemany(
        """INSERT INTO entities
               (entity_id, session_id, raw, normalized, canonical, entity_type)
           VALUES (nextval('seq_entity_id'), ?, ?, ?, ?, ?)
           ON CONFLICT (session_id, canonical, entity_type)
           DO UPDATE SET raw = excluded.raw""",
        ent_rows,
    )

    # c) Query back entity_ids for all our canonicals in one shot
    canonicals = list({c for (c, _t) in unique_ents})
    placeholders = ",".join(["?"] * len(canonicals))
    rows = conn.execute(
        f"""SELECT entity_id, canonical, entity_type
            FROM entities
            WHERE session_id = ? AND canonical IN ({placeholders})""",
        [session_id] + canonicals,
    ).fetchall()
    eid_map: dict[tuple, int] = {(r[1], r[2]): r[0] for r in rows}

    # d) Batch insert chunk_entities
    ce_rows = []
    for cid, chunk_keys in zip(chunk_ids, chunk_ent_keys):
        for canonical, etype, count in chunk_keys:
            eid = eid_map.get((canonical, etype))
            if eid is not None:
                ce_rows.append((cid, eid, count))

    if ce_rows:
        conn.executemany(
            """INSERT INTO chunk_entities (chunk_id, entity_id, hit_count)
               VALUES (?, ?, ?)
               ON CONFLICT (chunk_id, entity_id)
               DO UPDATE SET hit_count = hit_count + excluded.hit_count""",
            ce_rows,
        )
