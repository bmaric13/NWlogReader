"""Stream .tgz/.tar.gz members in priority order without full extraction.
Handles nested TAR and TGZ archives (e.g. svc_ifc_techsup_nxos.tar inside a TGZ)."""
import io
import re
import tarfile
from pathlib import Path
from typing import Generator

from backend.normalize.domain import FILENAME_KEYWORDS

# Priority: lower = process first (more important domains first)
_RANK_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"log|syslog|event", re.IGNORECASE), 0),
    (re.compile(r"interface|counters?|transceiver|sfp", re.IGNORECASE), 1),
    (re.compile(r"environment|module|inventory|hardware|sensor|fan|power|psu", re.IGNORECASE), 2),
    (re.compile(r"route|routing|bgp|ospf|eigrp|rib|fib|arp|adjacen", re.IGNORECASE), 3),
    (re.compile(r"config|running|startup", re.IGNORECASE), 4),
    (re.compile(r"process|crash|core|traceback|reset", re.IGNORECASE), 5),
]

# Skip members larger than this to avoid OOM (500 MB)
_MAX_MEMBER_BYTES = 500 * 1024 * 1024


def rank_member(name: str) -> int:
    """Return priority rank 0-6 for a tar member filename."""
    stem = Path(name).name
    for pattern, rank in _RANK_PATTERNS:
        if pattern.search(stem):
            return rank
    return 6


def _has_tar_magic(raw: bytes) -> bool:
    """Detect POSIX TAR by 'ustar' magic at offset 257."""
    return len(raw) > 262 and raw[257:262] in (b"ustar", b"ustar ")


def _is_gzip(raw: bytes) -> bool:
    return raw[:2] == b"\x1f\x8b"


def _decode_text(raw: bytes) -> str | None:
    """Decode bytes as text. Returns None if it looks binary."""
    if b"\x00" in raw[:1024]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _stream_tar_bytes(
    raw: bytes,
    prefix: str,
    depth: int = 0,
) -> Generator[tuple[str, list[str]], None, None]:
    """
    Recursively stream text members from a TAR or TGZ given as raw bytes.
    depth prevents unbounded recursion.
    """
    if depth > 3:
        return
    try:
        fobj = io.BytesIO(raw)
        with tarfile.open(fileobj=fobj, mode="r:*") as inner_tf:
            members = [m for m in inner_tf.getmembers() if m.isfile() and m.size <= _MAX_MEMBER_BYTES]
            members.sort(key=lambda m: (rank_member(m.name), m.name))
            for member in members:
                try:
                    f = inner_tf.extractfile(member)
                    if f is None:
                        continue
                    inner_raw = f.read()
                    name = f"{prefix}/{member.name}"
                    # Recurse into nested archives
                    if _is_gzip(inner_raw) or _has_tar_magic(inner_raw):
                        yield from _stream_tar_bytes(inner_raw, name, depth + 1)
                        continue
                    text = _decode_text(inner_raw)
                    if text is None:
                        continue
                    lines = text.splitlines()
                    if lines:
                        yield name, lines
                except Exception:
                    continue
    except Exception:
        pass


def stream_members(
    path: str | Path,
) -> Generator[tuple[str, list[str]], None, None]:
    """
    Open a .tgz archive, sort members by rank, yield (member_name, lines).
    Recursively unpacks nested TAR/TGZ files (e.g. techsup tars inside debug exports).
    Skips directories and binary files.
    """
    path = Path(path)
    with tarfile.open(str(path), "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.isfile() and m.size <= _MAX_MEMBER_BYTES]
        members.sort(key=lambda m: (rank_member(m.name), m.name))

        for member in members:
            try:
                f = tf.extractfile(member)
                if f is None:
                    continue
                raw = f.read()

                # Nested archive? Recurse into it.
                if _is_gzip(raw) or _has_tar_magic(raw):
                    yield from _stream_tar_bytes(raw, member.name, depth=1)
                    continue

                # Skip binary files
                text = _decode_text(raw)
                if text is None:
                    continue

                lines = text.splitlines()
                if lines:
                    yield member.name, lines
            except Exception:
                continue
