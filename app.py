"""Show Tech Reader — entry point."""
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import router

# Ensure required directories exist
for d in ("input", "work", "output", "static"):
    Path(d).mkdir(exist_ok=True)

app = FastAPI(title="Show Tech Reader", version="1.0.0")

# API routes
app.include_router(router)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


def _open_browser():
    webbrowser.open("http://localhost:8999")


if __name__ == "__main__":
    # Open browser after a short delay
    threading.Timer(1.2, _open_browser).start()
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8999,
        reload=False,
        log_level="info",
    )
