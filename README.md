# NWlogReader

NWlogReader je SaaS alat dizajniran specifično za mala i srednja poduzeća u Hrvatskoj, koji će vam omogućiti brzo i jednostavno analizirati vaše log datoteke. Ovo je idealan alat za poslodavce koji žele riješiti problema performansi sistema bez potrebe za velikim inovacijama.

NWlogReader nudi vam pristup vašim log datotekama u realnom vremenu, što vam omogućava brzo detektirati i ispraviti probleme. Alat je intuitivno koristan i ne zahtijeva specifične IT znanosti, čime ga čini dostupnim svim poslodavcima bez obzira na njihov nivo tehnološkog znanja.

NWlogReader vam omogućava brzo i jednostavno analizirati vaše log datoteke, što vam omogućava brzo detektirati i ispraviti probleme. Alat je intuitivno koristan i ne zahtijeva specifične IT znanosti, čime ga čini dostupnim svim poslodavcima bez obzira na njihov nivo tehnološkog znanja.

## How to Use

NWlogReader je alat koji vam omogućava da analizirate Cisco show-tech dump datoteke interaktivno. Sljedeće su koraci za njegovu instalaciju i korištenje:

1. **Klonirajte repozitorijum:**
   ```bash
   git clone https://github.com/bmar/nwlogreader.git
   cd nwlogreader
   ```

2. **Stvorite virtualno okruženje i aktivirajte ga:**
   - Linux/macOS:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   - Windows:
     ```cmd
     python -m venv .venv
     .venv\Scripts\activate
     ```

3. **Instalirajte potrebne ovisnosti:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Pokrenite aplikaciju:**
   ```bash
   python app.py
   ```

5. **Otvorite pretraživač i pristupite stranici:**
   [http://localhost:8999](http://localhost:8999)

6. **Učitajte show-tech datoteku:**
   - Kliknite na "Upload" dugme.
   - Odaberite ili pretražite vašu show-tech datoteku (plain text ili .tgz).

7. **Analizirajte i pregledajte podatke:**
   - Aplikacija će automatski parsirati i normalizirati datoteku.
   - Pregledajte analizirane podatke kroz različite sekcije (interfaces, routes, VRFs, BGP peers, processes, health).

8. **Izvješćivanje o napretku:**
   - Aplikacija će prikazivati napredak u obradi datoteke preko SSE (Server-Sent Events).

Nadamo se daNWlogReader vam omogući odličnu analizu vaših Cisco show-tech dumpova!

## Deploy

### Deployment Options for NWlogReader

#### 1. Docker Compose (Local/LAN)
**Steps:**
- Create a `docker-compose.yml` file to define the services, networks, and volumes required for the project.
- Define a service for each major component of the application (e.g., web server, database).
- Use environment variables to manage configuration settings.

**Example `docker-compose.yml`:**
```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - .:/app
    environment:
      - FLASK_ENV=development

  db:
    image: postgres:latest
    environment:
      - POSTGRES_DB=nwlogreader
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
    volumes:
      - db_data:/var/lib/postgresql/data

volumes:
  db_data:
```

**Deployment Command:**
```sh
docker-compose up --build
```

#### 2. Bare Metal (npm run build + PM2 or systemd)
**Steps:**
- Build the project using `npm run build` if applicable.
- Install dependencies using `pip install -r requirements.txt`.
- Use PM2 to manage the application process for production environments.
- Alternatively, use systemd for service management.

**Example PM2 Configuration (`ecosystem.config.js`):**
```js
module.exports = {
  apps: [{
    name: 'nwlogreader',
    script: 'app.py',
    env: {
      NODE_ENV: 'production'
    }
  }]
};
```

**Deployment Commands:**
- Install PM2 globally:
  ```sh
  npm install -g pm2
  ```
- Start the application using PM2:
  ```sh
  pm2 start ecosystem.config.js
  ```

**Example systemd Service (`nwlogreader.service`):**
```ini
[Unit]
Description=Node.js Application
After=network.target

[Service]
User=youruser
Group=yourgroup
WorkingDirectory=/path/to/nwlogreader
ExecStart=/usr/bin/npm start --prefix /path/to/nwlogreader
Restart=always

[Install]
WantedBy=multi-user.target
```

**Deployment Commands:**
- Enable and start the service:
  ```sh
  sudo systemctl enable nwlogreader.service
  sudo systemctl start nwlogreader.service
  ```

#### 3. Cloud (Vercel / Railway / Fly.io)
**Steps:**
- **Vercel:**
  - Create a Vercel account.
  - Import your project from GitHub or GitLab.
  - Configure environment variables and build settings as needed.

**Example `vercel.json`:**
```json
{
  "version": 2,
  "builds": [
    { "src": "app.py", "use": "@vercel/python" }
  ],
  "routes": [
    { "src": "/(.*)", "dest": "/" }
  ]
}
```

**Deployment Command:**
- Install Vercel CLI and login:
  ```sh
  npm install -g vercel
  vercel login
  ```
- Deploy the project:
  ```sh
  vercel
  ```

**Example Railway Configuration (`railway.toml`):**
```toml
[project]
name = "nwlogreader"

[build]
command = "pip install -r requirements.txt && python app.py"
entrypoint = "python app.py"

[web]
port = 5000
```

**Deployment Command:**
- Install Railway CLI and login:
  ```sh
  npm install -g @railway/cli
  railway login
  ```
- Deploy the project:
  ```sh
  railway up
  ```

**Example Fly.io Configuration (`fly.toml`):**
```toml
app = "nwlogreader"

[env]
FLASK_ENV = "production"

[[services]]
internal_port = 5000
processes = ["web"]

[build]
image = "python:3.9"
```

**Deployment Command:**
- Install Fly.io CLI and login:
  ```sh
  curl -L https://fly.io/install.sh | sh
  fly auth login
  ```
- Deploy the project:
  ```sh
  fly deploy
  ```

These deployment options provide a range of choices depending on your specific requirements, whether you prefer local development, production environments, or cloud services.

## Architecture

### Deep Technical Explanation of NWlogReader

#### Overview
NWlogReader is a Python-based application designed to parse, normalize, and store Cisco show-tech dump files (plain text or `.tgz`) using DuckDB for structured querying. The application provides an interactive frontend for browsing and querying the parsed data without relying on external services.

#### Project Structure
The project is organized into several key directories and files:

- **`.gitignore`**: Specifies files and directories to be ignored by Git.
- **`INSTALL.md`**: Contains installation instructions for different operating systems.
- **`LICENSE`**: MIT License file.
- **`README.md`**: Provides an overview of the project, features, architecture, supported platforms, and usage instructions.
- **`app.py`**: The entry point of the FastAPI application.
- **`profile_ingest.py`**: A script to profile the ingestion process for large files (e.g., 600MB).
- **`requirements.txt`**: Lists project dependencies.
- **`api/`**: Contains API routes and related logic.
- **`backend/`**: Houses backend functionality, including database operations, session management, parsing, normalization, and querying.
- **`static/`**: Contains static files for the frontend.

#### Key Components

##### 1. `app.py`
This is the main entry point of the FastAPI application. It sets up the FastAPI app, includes API routes from `api/routes.py`, mounts the static files directory, and starts the Uvicorn server to run the application on port 8999.

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import router

app = FastAPI(title="Show Tech Reader", version="1.0.0")

# API routes
app.include_router(router)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    return FileResponse("static/index.html")
```

##### 2. `api/routes.py`
This file defines all the HTTP endpoints for the application, including uploading files, querying data, exporting results, and handling server-sent events (SSE) for progress updates.

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.session import create_session, list_sessions, get_session_db_path, delete_session
from backend.db import get_conn
from backend.ingest.pipeline import ingest
from backend.query.filter1 import query_by_element
from backend.query.filter2 import apply_domain_filter
from backend.query.smart_reduce import smart_reduce
from backend.query.health import detect_health
from backend.query.daemons import infer_daemons
from backend.query.process_glossary import glossary_as_dict
from backend.query.relationships import get_relationships, find_peer_session, serialize as rel_serialize
from backend.query.graph import (
    get_traffic_context, get_policies_for_element,
    traffic_context_to_dict, policies_context_to_dict,
)
from backend.export.formatter import export_md, export_html, export_json

router = APIRouter()

@router.post("/upload/")
async def upload_file(file: UploadFile = File(...), background_tasks: BackgroundTasks = Depends()):
    session_id = create_session(original_filename=file.filename)
    background_tasks.add_task(ingest, file.file, session_id)
    return {"session_id": session_id}

@router.get("/query/")
async def query_data(session_id: str, element: str = Query(None)):
    conn = get_conn(get_session_db_path(session_id))
    results = query_by_element(conn, element)
    return {"results": results}
```

##### 3. `backend/session.py`
This module manages the lifecycle of sessions, including creating new sessions, listing all sessions, and resolving paths to session data.

```python
import json
import random
import string
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

def create_session(original_filename: str = "") -> dict:
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
        # ...
    }
    return meta
```

##### 4. `backend/ingest/pipeline.py`
This module handles the ingestion of show-tech files into the database. It reads the file, parses it, normalizes the data, and inserts it into the DuckDB database.

```python
import duckdb
from pathlib import Path

from backend.db import open_for_ingest as init_db
from backend.session import get_session_db_path

def ingest(file, session_id):
    conn = open_for_ingest(get_session_db_path(session_id))
    # Parse and insert data into the database
    # ...
```

##### 5. `backend/query/` Directory
This directory contains various modules for querying the parsed data, including filtering by element, applying domain filters, smart reduction, health detection, daemon inference, glossary processing, relationship extraction, and graph generation.

```python
from backend.db import get_conn
from backend.session import get_session_db_path

def query_by_element(conn, element):
    # Query the database for chunks that match the given element
    # ...
```

##### 6. `backend/export/formatter.py`
This module exports query results in different formats (Markdown, HTML, JSON).

```python
import json
from backend.query.filter1 import ChunkResult

def export_json(session_id: str, results: list[ChunkResult]) -> str:
    data = {
        "session_id": session_id,
        "count": len(results),
        "results": [
            {
                "chunk_id": r.chunk_id,
                "domain": r.domain,
                # ...
            }
            for r in results
        ],
    }
    return json.dumps(data, indent=2)
```

#### External Dependencies
- **FastAPI**: A modern, fast (high-performance) web framework for building APIs with Python 3.7+ based on standard Python type hints.
- **DuckDB**: An embedded SQL database management system that provides a high-level API and is designed to be fast and efficient.
- **SSE Starlette**: A library for handling server-sent events in FastAPI.

#### Limitations and Known Issues
- The application assumes that the input files are well-formed and contain valid Cisco show-tech data. Errors in file format can lead to parsing failures.
- The use of DuckDB indexes can be a performance bottleneck, especially with large datasets. Additional optimizations may be needed for better query performance.
- The application does not handle concurrent uploads or queries efficiently. Scaling the application may require additional infrastructure (e.g., using a distributed database system).

#### Conclusion
NWlogReader provides a comprehensive solution for parsing, normalizing, and querying Cisco show-tech files. Its modular architecture makes it easy to extend and maintain. However, users should be aware of its limitations and ensure that input data is valid and well-formed.
