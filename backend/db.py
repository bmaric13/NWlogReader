"""Database schema creation - DuckDB backend."""
import duckdb
from pathlib import Path


def _exec(conn, sql: str) -> None:
    """Execute multiple DDL statements separated by semicolons."""
    for stmt in sql.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


def get_conn(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open DB for query use: create tables + indexes (idempotent)."""
    conn = duckdb.connect(str(db_path))
    _create_tables(conn)
    create_indexes(conn)
    return conn


def open_for_ingest(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """
    Open DB for bulk ingest: create tables only, NO indexes.
    Call create_indexes(conn) after all inserts are done.
    Building indexes on empty/small tables at schema-create time and then
    maintaining them through millions of inserts is slow.  Deferring until
    after ingest lets DuckDB build them in one sorted pass — much faster.
    """
    conn = duckdb.connect(str(db_path))
    _create_tables(conn)
    return conn


def create_indexes(conn) -> None:
    """Create all query-time indexes.  Safe to call multiple times (IF NOT EXISTS)."""
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_chunks_session  ON chunks(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_domain   ON chunks(session_id, domain)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_hash     ON chunks(session_id, body_hash)",
        "CREATE INDEX IF NOT EXISTS idx_entities_session   ON entities(session_id, entity_type)",
        "CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(session_id, canonical)",
        "CREATE INDEX IF NOT EXISTS idx_ce_entity       ON chunk_entities(entity_id, chunk_id)",
        "CREATE INDEX IF NOT EXISTS idx_rel_session     ON relationships(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_rel_a           ON relationships(session_id, a_value)",
        "CREATE INDEX IF NOT EXISTS idx_rel_b           ON relationships(session_id, b_value)",
        "CREATE INDEX IF NOT EXISTS idx_gn_session      ON graph_nodes(session_id, node_type)",
    ]:
        try:
            conn.execute(ddl)
        except Exception:
            pass


def _create_tables(conn) -> None:
    """Create sequences and tables only — no indexes."""
    _exec(conn, "CREATE SEQUENCE IF NOT EXISTS seq_chunk_id START 1")
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id    INTEGER DEFAULT nextval('seq_chunk_id') PRIMARY KEY,
            session_id  TEXT    NOT NULL,
            source_name TEXT    NOT NULL,
            source_path TEXT,
            domain      TEXT    NOT NULL DEFAULT 'UNKNOWN',
            title       TEXT    NOT NULL DEFAULT '',
            start_line  INTEGER NOT NULL DEFAULT 0,
            line_count  INTEGER NOT NULL DEFAULT 0,
            body_hash   TEXT,
            created_at  TIMESTAMP DEFAULT now()
        )
    """)
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS chunk_text (
            chunk_id    INTEGER PRIMARY KEY,
            body        TEXT    NOT NULL,
            truncated   BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    _exec(conn, "CREATE SEQUENCE IF NOT EXISTS seq_entity_id START 1")
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS entities (
            entity_id   INTEGER DEFAULT nextval('seq_entity_id') PRIMARY KEY,
            session_id  TEXT    NOT NULL,
            raw         TEXT    NOT NULL,
            normalized  TEXT    NOT NULL,
            canonical   TEXT    NOT NULL,
            entity_type TEXT    NOT NULL,
            UNIQUE(session_id, canonical, entity_type)
        )
    """)
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS chunk_entities (
            chunk_id    INTEGER NOT NULL,
            entity_id   INTEGER NOT NULL,
            hit_count   INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (chunk_id, entity_id)
        )
    """)
    _exec(conn, "CREATE SEQUENCE IF NOT EXISTS seq_rel_id START 1")
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS relationships (
            rel_id      INTEGER DEFAULT nextval('seq_rel_id') PRIMARY KEY,
            session_id  TEXT    NOT NULL,
            rel_type    TEXT    NOT NULL,
            a_type      TEXT    NOT NULL,
            a_value     TEXT    NOT NULL,
            b_type      TEXT    NOT NULL,
            b_value     TEXT    NOT NULL,
            evidence_chunk_id INTEGER,
            confidence  TEXT    NOT NULL DEFAULT 'MED',
            UNIQUE(session_id, rel_type, a_value, b_value)
        )
    """)
    _exec(conn, "CREATE SEQUENCE IF NOT EXISTS seq_node_id START 1")
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS graph_nodes (
            node_id     INTEGER DEFAULT nextval('seq_node_id') PRIMARY KEY,
            session_id  TEXT    NOT NULL,
            node_type   TEXT    NOT NULL,
            name        TEXT    NOT NULL,
            metadata    TEXT,
            UNIQUE(session_id, node_type, name)
        )
    """)
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS device_info (
            session_id          TEXT PRIMARY KEY,
            hostname            TEXT,
            platform            TEXT,
            serial              TEXT,
            mgmt_ip             TEXT,
            vpc_domain_id       TEXT,
            vpc_peer_keepalive  TEXT,
            vpc_peer_link       TEXT,
            stack_members       TEXT
        )
    """)
    _exec(conn, """
        CREATE TABLE IF NOT EXISTS session_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
