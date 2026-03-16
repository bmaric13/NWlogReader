"""Session lifecycle: create, list, resolve paths."""

import json
import random
import string
import duckdb
from datetime import datetime
from pathlib import Path

from backend.db import open_for_ingest as init_db

WORK_DIR = Path("work")
_DB_NAME = "index.duckdb"
_META_NAME = "session.json"


def _make_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}_{rand}"


def _write_sidecar(session_dir: Path, meta: dict) -> None:
    """Write/update the JSON sidecar — always readable even while DB is locked."""
    try:
        (session_dir / _META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] sidecar write failed: {e}", file=sys.stderr)


def create_session(original_filename: str = "") -> dict:
    """Create a new session directory + DuckDB. Returns session metadata."""
    session_id = _make_id()
    session_dir = WORK_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    db_path = session_dir / _DB_NAME
    conn = init_db(db_path)
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT INTO session_meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [
            ("session_id", session_id),
            ("original_filename", original_filename),
            ("created_at", now),
            ("status", "created"),
        ],
    )
    conn.close()

    meta = {
        "session_id": session_id,
        "original_filename": original_filename,
        "created_at": now,
        "status": "created",
        "db_path": str(db_path),
    }
    _write_sidecar(session_dir, meta)
    return meta


def list_sessions() -> list[dict]:
    """List all sessions found in work/ directory, newest first.
    Reads JSON sidecar first so sessions are visible while DB is locked (indexing)."""
    if not WORK_DIR.exists():
        return []
    sessions = []
    import re as _re

    _SESSION_RE = _re.compile(r"^\d{8}_\d{6}_[a-z0-9]+$")
    for d in sorted(WORK_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        # Skip old test/bench directories that aren't real sessions
        if not _SESSION_RE.match(d.name):
            continue
        db_path = d / _DB_NAME
        json_path = d / _META_NAME
        if not db_path.exists() and not json_path.exists():
            continue

        # Try JSON sidecar first (always readable, even while DB is locked)
        meta = None
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Try DB for fresher data (not locked)
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            rows = conn.execute("SELECT key, value FROM session_meta").fetchall()
            conn.close()
            db_meta = {r[0]: r[1] for r in rows}
            db_meta["db_path"] = str(db_path)
            meta = db_meta  # DB is authoritative when available
        except Exception:
            pass  # DB locked — fall back to JSON sidecar

        if meta:
            meta.setdefault("db_path", str(db_path))
            # Always guarantee session_id — fall back to directory name
            meta.setdefault("session_id", d.name)
            sessions.append(meta)
    return sessions


def get_session_db_path(session_id: str) -> Path:
    return WORK_DIR / session_id / _DB_NAME


def delete_session(session_id: str) -> bool:
    """Delete session directory and all its data. Returns True if deleted."""
    import shutil

    session_dir = WORK_DIR / session_id
    if not session_dir.exists():
        return False
    shutil.rmtree(session_dir, ignore_errors=True)
    return True


def update_session_status(session_id: str, status: str, conn=None) -> None:
    """Update status in session_meta. Accepts optional open conn to avoid second connection."""
    sql = "INSERT INTO session_meta VALUES ('status', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
    if conn is not None:
        conn.execute(sql, [status])
        try:
            conn.commit()
        except Exception:
            pass  # may already be in autocommit mode
    else:
        db_path = get_session_db_path(session_id)
        c = duckdb.connect(str(db_path))
        c.execute(sql, [status])
        c.close()
    # Keep sidecar in sync so list_sessions() sees the new status immediately
    session_dir = WORK_DIR / session_id
    json_path = session_dir / _META_NAME
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            meta["status"] = status
            _write_sidecar(session_dir, meta)
        except Exception as e:
            print(
                f"[WARN] sidecar update failed for {session_id}: {e}", file=sys.stderr
            )
