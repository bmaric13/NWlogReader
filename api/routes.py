"""All FastAPI route definitions."""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
    UploadFile,
    File,
    Query,
)
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.session import (
    create_session,
    list_sessions,
    get_session_db_path,
    delete_session,
)
from backend.db import get_conn
from backend.ingest.pipeline import ingest
from backend.query.filter1 import query_by_element
from backend.query.filter2 import apply_domain_filter
from backend.query.smart_reduce import smart_reduce
from backend.query.health import detect_health
from backend.query.daemons import infer_daemons
from backend.query.process_glossary import glossary_as_dict
from backend.query.relationships import (
    get_relationships,
    find_peer_session,
    serialize as rel_serialize,
)
from backend.query.graph import (
    get_traffic_context,
    get_policies_for_element,
    traffic_context_to_dict,
    policies_context_to_dict,
)
from backend.export.formatter import export_md, export_html, export_json
from backend.normalize.domain import ALL_DOMAINS

router = APIRouter()

INPUT_DIR = Path("input")


def _open_file_dialog() -> str:
    """
    Open a native Windows file picker using pythonw.exe (GUI interpreter).
    pythonw has its own message loop and can show tkinter dialogs from any thread.
    Result written to a temp file to avoid stdout capture issues.
    """
    import subprocess
    import sys

    tmp = tempfile.mktemp(suffix=".txt")
    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "root = tk.Tk(); root.withdraw(); root.lift(); "
        "root.attributes('-topmost', True); root.after(50, root.focus_force); "
        "p = filedialog.askopenfilename("
        "    title='Select show-tech file',"
        "    filetypes=[('All files','*.*'),('Text / Log','*.txt *.log *.out'),('Archives','*.tgz *.gz *.tar.gz')]"
        "); root.destroy(); "
        f"open(r'{tmp}','w').write(p or '')"
    )
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    try:
        subprocess.run([pythonw, "-c", script], timeout=300)
        if os.path.exists(tmp):
            with open(tmp, encoding="utf-8") as f:
                return f.read().strip()
        return ""
    except Exception:
        return ""
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


@router.get("/api/browse-file")
async def browse_file():
    """Open native file picker; returns {path} or empty string."""
    import asyncio

    path = await asyncio.get_event_loop().run_in_executor(None, _open_file_dialog)
    return {"path": path}


# In-memory progress store: session_id → list of event dicts
_progress: dict[str, list[dict]] = {}


# ── Ingest ────────────────────────────────────────────────────────────────────


@router.post("/api/sessions/ingest-stream")
async def ingest_stream(
    request: Request,
    background_tasks: BackgroundTasks,
    filename: str = Query(...),
    session_id: str | None = Query(default=None),
):
    """
    Receive raw binary stream (no multipart), save to disk, index from disk.
    If session_id is given, add this file to that existing session (multi-part).
    Otherwise create a new session (pruning oldest if >= 3 exist).
    """
    INPUT_DIR.mkdir(exist_ok=True)
    dest = INPUT_DIR / filename
    with dest.open("wb") as fh:
        async for chunk in request.stream():
            fh.write(chunk)

    new_session = session_id is None
    if new_session:
        existing = list_sessions()
        while len(existing) >= 3:
            oldest = existing[-1]
            delete_session(oldest.get("session_id", ""))
            _progress.pop(oldest.get("session_id", ""), None)
            existing = existing[:-1]
        session = create_session(original_filename=filename)
        session_id = session["session_id"]

    since = len(_progress.get(session_id, []))
    _progress.setdefault(session_id, [])

    def _cb(evt):
        _progress[session_id].append(evt)

    background_tasks.add_task(ingest, session_id, str(dest), _cb)
    return {
        "session_id": session_id,
        "filename": filename,
        "since": since,
        "new_session": new_session,
    }


@router.post("/api/sessions/ingest")
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """
    Upload a file and ingest it.
    If session_id is provided, add to that existing session (for multi-part archives).
    Otherwise create a new session.
    Returns {session_id, filename, new_session: bool}.
    """
    INPUT_DIR.mkdir(exist_ok=True)
    dest = INPUT_DIR / file.filename
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    new_session = session_id is None
    if new_session:
        # Auto-prune: keep at most 3 sessions (delete oldest beyond limit)
        existing = list_sessions()
        while len(existing) >= 3:
            oldest = existing[-1]  # list_sessions is newest-first
            delete_session(oldest["session_id"])
            _progress.pop(oldest["session_id"], None)
            existing = existing[:-1]

        session = create_session(original_filename=file.filename)
        session_id = session["session_id"]
        _progress[session_id] = []
    else:
        # Validate the session exists
        db_path = get_session_db_path(session_id)
        if not db_path.exists():
            raise HTTPException(404, f"Session {session_id} not found")
        if session_id not in _progress:
            _progress[session_id] = []

    def _cb(event: dict) -> None:
        event["filename"] = file.filename
        _progress[session_id].append(event)

    since = len(_progress[session_id])  # client should poll from this index
    background_tasks.add_task(_run_ingest, session_id, dest, _cb)
    return {
        "session_id": session_id,
        "filename": file.filename,
        "new_session": new_session,
        "since": since,
    }


def _run_ingest(session_id: str, path: Path, progress_cb) -> None:
    ingest(session_id, path, progress_cb)


# ── Path-based ingest (file already on disk — no upload copy) ─────────────────


class IngestPathRequest(BaseModel):
    path: str
    session_id: str | None = None


@router.post("/api/sessions/ingest-path")
async def ingest_path(background_tasks: BackgroundTasks, req: IngestPathRequest):
    """
    Ingest a file already on disk — no multipart upload, no copy.
    Send {path: "C:/data/nexus.tgz"} or a path relative to the app CWD.
    Critical for 4 GB+ files on the same machine.
    """
    file_path = Path(req.path)
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    file_path = file_path.resolve()

    if not file_path.exists():
        raise HTTPException(400, f"File not found: {file_path}")
    if not file_path.is_file():
        raise HTTPException(400, f"Not a file: {file_path}")

    new_session = req.session_id is None
    if new_session:
        existing = list_sessions()
        while len(existing) >= 3:
            oldest = existing[-1]
            delete_session(oldest["session_id"])
            _progress.pop(oldest["session_id"], None)
            existing = existing[:-1]
        session = create_session(original_filename=file_path.name)
        session_id = session["session_id"]
        _progress[session_id] = []
    else:
        session_id = req.session_id
        db_path = get_session_db_path(session_id)
        if not db_path.exists():
            raise HTTPException(404, f"Session {session_id} not found")
        if session_id not in _progress:
            _progress[session_id] = []

    def _cb(event: dict) -> None:
        event["filename"] = file_path.name
        _progress[session_id].append(event)

    since = len(_progress[session_id])
    background_tasks.add_task(_run_ingest, session_id, file_path, _cb)
    return {
        "session_id": session_id,
        "filename": file_path.name,
        "new_session": new_session,
        "since": since,
    }


# ── Progress SSE ──────────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/progress")
async def progress_stream(session_id: str, since: int = 0):
    """
    SSE stream of ingestion progress events.
    `since` lets clients start from a specific event index (for multi-part uploads).
    The response includes an `event_index` field so the client knows where to resume.
    """

    async def _generator():
        last_idx = since
        idle_count = 0
        while True:
            events = _progress.get(session_id, [])
            for event in events[last_idx:]:
                payload = {**event, "event_index": last_idx}
                last_idx += 1
                yield {"data": json.dumps(payload)}
                if event.get("status") in ("done", "error"):
                    return
            await asyncio.sleep(0.5)
            idle_count += 1
            if idle_count > 1200:  # 10 min timeout for large files
                break

    return EventSourceResponse(_generator())


# ── Sessions ──────────────────────────────────────────────────────────────────


@router.get("/api/sessions")
async def get_sessions():
    return list_sessions()


@router.delete("/api/sessions/{session_id}")
async def delete_session_route(session_id: str):
    """Permanently delete a session and all its data."""
    ok = delete_session(session_id)
    if not ok:
        raise HTTPException(404, "Session not found")
    _progress.pop(session_id, None)
    return {"deleted": session_id}


@router.delete("/api/sessions")
async def delete_latest_session():
    """Pop (delete) the most recently created session — lazy housekeeping."""
    sessions = list_sessions()
    if not sessions:
        raise HTTPException(404, "No sessions to delete")
    latest = sessions[0]  # list_sessions returns newest-first
    sid = latest["session_id"]
    delete_session(sid)
    _progress.pop(sid, None)
    return {"deleted": sid, "original_filename": latest.get("original_filename", "")}


# ── Process glossary ──────────────────────────────────────────────────────────


@router.get("/api/glossary")
async def get_glossary():
    """Return full process/daemon glossary for the frontend."""
    return glossary_as_dict()


# ── Relationships ─────────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/relationships")
async def get_element_relationships(session_id: str, element: str = Query(...)):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    conn = get_conn(db_path)
    try:
        from backend.normalize.entities import parse_user_element

        canonical = parse_user_element(element)["canonical"]
        ctx = get_relationships(conn, session_id, canonical)

        # Try to find peer session across all sessions
        all_sessions = list_sessions()
        # Enrich each session with its device_info for correlation
        for s in all_sessions:
            try:
                peer_conn = get_conn(get_session_db_path(s["session_id"]))
                row = peer_conn.execute(
                    "SELECT hostname, mgmt_ip FROM device_info WHERE session_id=?",
                    [s["session_id"]],
                ).fetchone()
                peer_conn.close()
                if row:
                    s["hostname"] = row[0]
                    s["mgmt_ip"] = row[1]
            except Exception:
                pass

        ctx.peer_session = find_peer_session(
            all_sessions, session_id, ctx.vpc_peer_keepalive, ctx.hostname
        )
        return rel_serialize(ctx)
    finally:
        conn.close()


@router.get("/api/sessions/{session_id}/graph")
async def get_graph(session_id: str, element: str = Query(...)):
    """Return full dependency graph for an element: traffic context + policies."""
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    conn = get_conn(db_path)
    try:
        from backend.normalize.entities import parse_user_element

        canonical = parse_user_element(element)["canonical"]
        traffic = get_traffic_context(conn, session_id, canonical)
        policies = get_policies_for_element(conn, session_id, canonical)
        return {
            "element": canonical,
            "traffic_context": traffic_context_to_dict(traffic),
            "policies": policies_context_to_dict(policies),
        }
    finally:
        conn.close()


@router.get("/api/sessions/{session_id}/device-info")
async def get_device_info(session_id: str):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")
    conn = get_conn(db_path)
    try:
        cur = conn.execute("SELECT * FROM device_info WHERE session_id=?", [session_id])
        row = cur.fetchone()
        if not row:
            return {}
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


# ── Entities (autocomplete) ───────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/entities")
async def get_entities(
    session_id: str,
    q: str = Query(default="", description="Prefix search"),
    type: str = Query(default="", description="Entity type filter"),
    limit: int = Query(default=20, ge=1, le=100),
):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    conn = get_conn(db_path)
    try:
        params = [session_id]
        clauses = ["e.session_id = ?"]

        if q:
            clauses.append(
                "(lower(e.canonical) ILIKE lower(?) OR lower(e.raw) ILIKE lower(?))"
            )
            params += [f"%{q}%", f"%{q}%"]
        if type:
            clauses.append("e.entity_type = ?")
            params.append(type.upper())

        where = " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT e.canonical, e.entity_type, any_value(e.raw) as raw, COUNT(*) as chunk_count
            FROM entities e
            JOIN chunk_entities ce ON ce.entity_id = e.entity_id
            WHERE {where}
            GROUP BY e.canonical, e.entity_type
            ORDER BY chunk_count DESC, e.canonical
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        return [
            {"canonical": r[0], "type": r[1], "raw": r[2], "chunk_count": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


# ── Query ─────────────────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    element: str
    domain: str | None = None
    smart_reduce: bool = False
    show_duplicates: bool = False
    page: int = 0
    limit: int = 50


@router.post("/api/sessions/{session_id}/query")
async def query_session(session_id: str, req: QueryRequest):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    if req.domain and req.domain not in ALL_DOMAINS:
        raise HTTPException(400, f"Unknown domain: {req.domain!r}")

    conn = get_conn(db_path)
    try:
        results = query_by_element(
            conn,
            session_id,
            req.element,
            domain=req.domain,
            page=req.page,
            limit=req.limit,
        )

        if req.domain:
            results = apply_domain_filter(results, req.domain)

        # Attach health items
        for r in results:
            r.health_items = detect_health(r.body_preview)

        if req.smart_reduce:
            view = smart_reduce(results, req.element, top_n=req.limit)
            return {
                "mode": "smart_reduce",
                "element": view.element,
                "top_cards": view.top_cards,
                "alerts": view.alerts,
                "timeline": view.timeline,
                "daemon_hints": _serialize_daemon_hints(view.daemon_hints),
                "evidence": _serialize_results(view.evidence),
                "total": len(view.evidence),
            }

        # Standard mode: also run daemon inference on the results
        daemon_hints = infer_daemons(req.element, results)
        return {
            "mode": "standard",
            "daemon_hints": _serialize_daemon_hints(daemon_hints),
            "results": _serialize_results(results),
            "total": len(results),
        }
    finally:
        conn.close()


def _serialize_daemon_hints(hints) -> list:
    return [
        {
            "name": h.name,
            "display": h.display,
            "confidence": h.confidence,
            "what_it_does": h.what_it_does,
            "common_symptoms": h.common_symptoms,
            "useful_commands": h.useful_commands,
            "reasons": h.reasons,
            "evidence": [
                {"chunk_id": e.chunk_id, "line_excerpt": e.line_excerpt}
                for e in h.evidence
            ],
        }
        for h in hints
    ]


def _serialize_results(results):
    return [
        {
            "chunk_id": r.chunk_id,
            "domain": r.domain,
            "source_name": r.source_name,
            "title": r.title,
            "body_preview": r.body_preview,
            "hit_count": r.hit_count,
            "relevance_score": round(r.relevance_score, 3),
            "health_items": [
                {
                    "label": h.label,
                    "value": h.value,
                    "unit": h.unit,
                    "status": h.status,
                    "heuristic": h.heuristic,
                }
                for h in r.health_items
            ],
        }
        for r in results
    ]


# ── Raw chunk ─────────────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/chunk/{chunk_id}")
async def get_chunk(session_id: str, chunk_id: int):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT c.chunk_id, c.domain, c.source_name, c.source_path, c.title,
                   c.start_line, c.line_count, ct.body, ct.truncated
            FROM chunks c JOIN chunk_text ct ON ct.chunk_id = c.chunk_id
            WHERE c.session_id = ? AND c.chunk_id = ?
            """,
            [session_id, chunk_id],
        ).fetchone()
        if not row:
            raise HTTPException(404, "Chunk not found")
        (
            chunk_id_,
            domain,
            source_name,
            source_path,
            title,
            start_line,
            line_count,
            body,
            truncated,
        ) = row

        # If body was truncated during ingest, try to read full text from source file
        if truncated and source_path:
            try:
                from pathlib import Path as _P

                src = _P(source_path)
                if src.exists():
                    raw = src.read_bytes()
                    text = raw.decode("utf-8", errors="replace")
                    all_lines = text.splitlines()
                    end = min(start_line + line_count, len(all_lines))
                    body = "\n".join(all_lines[start_line:end])
            except Exception:
                pass  # fall back to stored preview

        return {
            "chunk_id": chunk_id_,
            "domain": domain,
            "source_name": source_name,
            "title": title,
            "start_line": start_line,
            "line_count": line_count,
            "body": body,
        }
    finally:
        conn.close()


# ── Export ────────────────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    element: str
    domain: str | None = None
    format: str = "html"


@router.post("/api/sessions/{session_id}/export")
async def export_session(session_id: str, req: ExportRequest):
    db_path = get_session_db_path(session_id)
    if not db_path.exists():
        raise HTTPException(404, "Session not found")

    if req.domain and req.domain not in ALL_DOMAINS:
        raise HTTPException(400, f"Unknown domain: {req.domain!r}")

    conn = get_conn(db_path)
    try:
        results = query_by_element(
            conn, session_id, req.element, domain=req.domain, limit=200
        )
        if req.domain:
            results = apply_domain_filter(results, req.domain)
        for r in results:
            r.health_items = detect_health(r.body_preview)
    finally:
        conn.close()

    fmt = req.format.lower()
    if fmt == "json":
        content = export_json(session_id, results)
        media_type = "application/json"
        ext = "json"
    elif fmt == "md":
        content = export_md(session_id, results)
        media_type = "text/markdown"
        ext = "md"
    else:
        content = export_html(session_id, results)
        media_type = "text/html"
        ext = "html"

    filename = f"showtech_{session_id}_{req.element.replace('/', '_')}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
