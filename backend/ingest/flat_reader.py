"""Handle single .txt/.log files or folders of files."""
from pathlib import Path
from typing import Generator


def _read_file(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
        if b"\x00" in raw[:1024]:
            return []  # skip binary
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        return text.splitlines()
    except Exception:
        return []


def stream_flat(path: str | Path) -> Generator[tuple[str, list[str]], None, None]:
    """
    Yield (filename, lines) for a single file or all text files in a directory.
    """
    path = Path(path)
    if path.is_dir():
        exts = {".txt", ".log", ".out", ".text", ""}
        files = sorted(
            f for f in path.rglob("*")
            if f.is_file() and f.suffix.lower() in exts
        )
        for f in files:
            lines = _read_file(f)
            if lines:
                yield f.name, lines
    else:
        lines = _read_file(path)
        if lines:
            yield path.name, lines
