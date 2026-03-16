# NWlogReader

**NWlogReader** is an interactive Cisco show-tech dump analyzer. Upload a `show tech-support` file (plain text or `.tgz`) and it is parsed, normalized, and stored in DuckDB for structured browsing and querying — no external services required.

## Features

- Upload plain text or `.tgz` show-tech files
- Automatic parsing and normalization for IOS, IOS XE, NX-OS, ASA/Firepower
- Structured querying via DuckDB (interfaces, routes, VRFs, BGP peers, processes, health)
- Vanilla JS frontend — no build step, no dependencies
- Fully self-contained — no cloud services, no API keys

## Architecture

```
app.py              FastAPI entry point, serves frontend on :8999
api/routes.py       HTTP endpoints (upload, query, export, SSE progress)
backend/
  ingest/           Show-tech parsing pipeline (chunker, indexer, extractors)
  normalize/        Entity normalization (interfaces, VRFs, domains)
  query/            DuckDB query layer (health, routing, BGP, processes)
  db.py             DuckDB session manager
  session.py        Per-upload session state
static/             Vanilla JS + HTML/CSS frontend
```

## Supported Platforms

| Platform       | Parser Support |
|----------------|---------------|
| IOS            | Full          |
| IOS XE         | Full          |
| NX-OS          | Full          |
| ASA/Firepower  | Partial       |

## Usage

1. Install dependencies (see [INSTALL.md](INSTALL.md))
2. Run: `python app.py`
3. Open [http://localhost:8999](http://localhost:8999)
4. Upload a show-tech file
5. Browse interfaces, routes, VRFs, BGP peers, daemons, and health data

## Requirements

- Python 3.9+
- Dependencies: `fastapi`, `uvicorn`, `duckdb`, `python-multipart`, `aiofiles`, `sse-starlette`

## License

MIT — see [LICENSE](LICENSE)
