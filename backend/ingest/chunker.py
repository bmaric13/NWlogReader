"""Split file content into indexable chunks."""
import re
from dataclasses import dataclass, field

# IOS/XE command delimiter patterns
_IOS_DELIM = re.compile(
    r"^(?:-{3,}\s*(?:show\s+\S[^\n]*)\s*-{3,}|"  # ---- show xyz ----
    r"show\s+\S[^\n]*)",                            # show xyz (bare)
    re.IGNORECASE,
)

# NX-OS-style section headers: !!!/###/+++/===/<...> show xyz
_NXOS_DELIM = re.compile(
    r"^(?:[!#+=~]{2,}\s*show\s+\S[^\n]*\s*[!#+=~]*|"   # !! ### +++ === show xyz
    r"<{2,}\s*show\s+\S[^\n]*\s*>*|"                     # << show xyz >>
    r"`show\s+[^`]+`)",                                   # `show xyz`
    re.IGNORECASE,
)

# Lines that are "banner noise" — not real section titles
_BANNER_SKIP = re.compile(
    r"show\s+tech.{0,60}will\s+take|"   # "show tech[-support] [brief/detail] will take"
    r"will\s+take.{0,80}minutes|"        # "will take [approximately] N-N minutes"
    r"approximately\s+\d+.{0,30}minutes|"
    r"please\s+wait|"
    r"collecting\s+(show|tech|data)|"
    r"show\s+alignment|"                 # KiTTY terminal artifact
    r"^-{10,}$|"                         # long dashes-only separator
    r"^\s*={10,}\s*$",                   # long equals-only separator
    re.IGNORECASE,
)

# Larger chunks = fewer DB rows = faster ingest + better context per result.
# Logs: can be 5k+ lines; "show" command outputs are natural sections so
# we let them grow to 5000 lines before force-splitting.
MAX_CHUNK_LINES = 5000
SPLIT_AT = 4800


@dataclass
class Chunk:
    source_name: str
    title: str
    lines: list[str]
    start_line: int = 0


# Config stanza headers — lines at column 0 that start a new config block
_CONFIG_STANZA_RE = re.compile(
    r"^(?:interface|ip\s+dhcp\s+(?:pool|excluded)|"
    r"router\s+(?:ospf|bgp|eigrp|isis|rip)|"
    r"ip\s+(?:access-list|vrf|prefix-list|community-list|sla|nat)|"
    r"vrf\s+definition|policy-map|class-map|route-map|object-group|"
    r"spanning-tree\s+mst\s+configuration|"
    r"line\s+(?:vty|con|aux|tty)|"
    r"crypto\s+(?:map|isakmp|keyring|pki)|"
    r"track\s+\d|redundancy|control-plane|"
    r"ntp\s+server|snmp-server\s+host|"
    r"vlan\s+\d)\b",
    re.IGNORECASE,
)

_IS_CONFIG_TITLE = re.compile(
    r"(?:running[-\s]config|startup[-\s]config|current[-\s]config)", re.IGNORECASE
)


def _split_config_stanzas(ch: Chunk) -> list[Chunk]:
    """
    Sub-chunk a running/startup-config block by top-level stanza.
    Each interface, dhcp pool, router section etc. becomes its own chunk.
    Ungrouped global lines are kept as a 'global config' chunk.
    """
    stanzas: list[Chunk] = []
    global_lines: list[str] = []
    cur_title: str | None = None
    cur_lines: list[str] = []
    cur_start: int = ch.start_line

    def _flush(title: str | None, lines: list[str], start: int) -> None:
        content = [l for l in lines if l.strip() and l.strip() != "!"]
        if not content:
            return
        if title is None:
            title = f"{ch.title} [global]"
        stanzas.append(Chunk(source_name=ch.source_name, title=title,
                             lines=lines, start_line=start))

    for i, line in enumerate(ch.lines):
        stripped = line.strip()
        if stripped == "!" or not stripped:
            if cur_title is not None:
                cur_lines.append(line)
            else:
                global_lines.append(line)
            continue

        # Top-level stanza header: no leading whitespace, matches pattern
        if not line[0].isspace() and _CONFIG_STANZA_RE.match(stripped):
            if cur_title is not None:
                _flush(cur_title, cur_lines, cur_start)
            else:
                _flush(None, global_lines, ch.start_line)
                global_lines = []
            cur_title = stripped.rstrip()
            cur_lines = [line]
            cur_start = ch.start_line + i
        else:
            if cur_title is not None:
                cur_lines.append(line)
            else:
                global_lines.append(line)

    # Flush last stanza and any remaining global lines
    if cur_title is not None:
        _flush(cur_title, cur_lines, cur_start)
    else:
        _flush(None, global_lines, ch.start_line)

    # If no stanzas were found, return original chunk unchanged
    return stanzas if len(stanzas) > 1 else [ch]


def _split_large(chunks: list[Chunk]) -> list[Chunk]:
    """Further split chunks: sub-chunk config stanzas, then hard-split by line count."""
    out: list[Chunk] = []
    for ch in chunks:
        # Sub-chunk running/startup config by stanza first
        if _IS_CONFIG_TITLE.search(ch.title) and len(ch.lines) > 50:
            sub = _split_config_stanzas(ch)
            for s in sub:
                if len(s.lines) <= MAX_CHUNK_LINES:
                    out.append(s)
                else:
                    for i in range(0, len(s.lines), SPLIT_AT):
                        part_lines = s.lines[i:i + SPLIT_AT]
                        suffix = f" [part {i // SPLIT_AT + 1}]" if i > 0 else ""
                        out.append(Chunk(source_name=s.source_name,
                                         title=s.title + suffix,
                                         lines=part_lines,
                                         start_line=s.start_line + i))
        elif len(ch.lines) <= MAX_CHUNK_LINES:
            out.append(ch)
        else:
            for i in range(0, len(ch.lines), SPLIT_AT):
                part_lines = ch.lines[i : i + SPLIT_AT]
                suffix = f" [part {i // SPLIT_AT + 1}]" if i > 0 else ""
                out.append(
                    Chunk(
                        source_name=ch.source_name,
                        title=ch.title + suffix,
                        lines=part_lines,
                        start_line=ch.start_line + i,
                    )
                )
    return out


def chunk_nxos(name: str, lines: list[str]) -> list[Chunk]:
    """
    NX-OS: typically one command per file.
    If file has internal headers (!! show ...), split on those.
    Otherwise treat whole file as one chunk.
    """
    has_internal = any(_NXOS_DELIM.match(l.strip()) for l in lines[:200])
    if has_internal:
        return _split_by_delim(name, lines, _NXOS_DELIM)

    title = _derive_title_from_name(name)
    if _BANNER_SKIP.search(title):
        title = name
    chunks = [Chunk(source_name=name, title=title, lines=lines, start_line=0)]
    return _split_large(chunks)


def chunk_ios(name: str, lines: list[str]) -> list[Chunk]:
    """
    IOS/XE: split on `---- show xyz ----` or bare `show xyz` command headers.
    """
    chunks = _split_by_delim(name, lines, _IOS_DELIM)
    if not chunks:
        chunks = [Chunk(source_name=name, title=name, lines=lines, start_line=0)]
    return _split_large(chunks)


def chunk_auto(name: str, lines: list[str]) -> list[Chunk]:
    """
    Auto-detect format for flat single-file ingestion.
    Tries both NX-OS and IOS/XE delimiters and uses whichever produces more chunks
    (avoids KiTTY/terminal artifacts tricking the NX-OS pattern into tiny chunk counts).
    """
    nxos_chunks = _split_by_delim(name, lines, _NXOS_DELIM)
    ios_chunks  = _split_by_delim(name, lines, _IOS_DELIM)
    if nxos_chunks and (not ios_chunks or len(nxos_chunks) >= len(ios_chunks)):
        return _split_large(nxos_chunks)
    if ios_chunks:
        return _split_large(ios_chunks)
    if nxos_chunks:
        return _split_large(nxos_chunks)
    return _split_large([Chunk(source_name=name, title=name, lines=lines, start_line=0)])


# Lines threshold above which we use the fast full-text scan instead of the
# Python line-by-line loop.  The break-even is around 500 lines on most CPUs.
_FAST_THRESHOLD = 500


def _split_by_delim(name: str, lines: list[str], pattern: re.Pattern) -> list[Chunk]:
    """
    Split on delimiter pattern.
    For large inputs uses re.finditer on the full joined text (pure C, GIL released),
    which is 10-50× faster than a Python for-loop over millions of lines.
    For small inputs falls back to the original line-by-line approach.
    """
    if not lines:
        return []
    if len(lines) > _FAST_THRESHOLD:
        return _split_fast(name, lines, pattern)
    return _split_slow(name, lines, pattern)


def _split_fast(name: str, lines: list[str], pattern: re.Pattern) -> list[Chunk]:
    """
    Full-text fast split.

    Strategy for large files (joins once, then uses str.find for O(n) C-speed
    pre-filtering instead of running the full regex on all 600+ MB):

    1. Join lines into one string  (pure C, ~1 s for 600 MB)
    2. Find all candidate positions with text.find(seed) in a loop
       (Boyer-Moore in C, ~0.1 s per seed)
    3. Run mp.match() only on the candidate lines (~1 µs × ~1050 = ~1 ms)
    4. Build chunks from the resulting header list

    For smaller files (< 2 M chars) we fall through to the original finditer path.
    """
    if not lines:
        return []

    text = "\n".join(lines)
    mp = re.compile(pattern.pattern, pattern.flags | re.MULTILINE)

    if len(text) > 2_000_000:
        headers = _headers_via_find(mp, text)
    else:
        headers = _headers_from_finditer(mp, text)

    if not headers:
        return []
    return _build_chunks(name, text, headers)


# Seed strings searched case-sensitively in the joined text.
# Cisco show-tech output always uses lowercase "show" so no lowercasing needed.
# Each seed pinpoints a possible header line; the full regex validates it.
_FIND_SEEDS = (
    "`show ",       # NX-OS backtick      ← most common, check first
    "\n`show ",     # same, mid-file
    "!! show ",     # NX-OS !! delimited
    "## show ",
    "++ show ",
    "== show ",
    "<< show ",
    "--- show ",    # IOS/XE ---- show xyz ----
    "\nshow ",      # bare show command at line start
    "\nShow ",      # capitalised variant (uncommon but present in some IOS XE)
)


def _headers_via_find(
    mp: re.Pattern,
    text: str,
) -> list[tuple[int, int, str]]:
    """
    Fast header scan: str.find (Boyer-Moore, pure C, no string copy overhead)
    locates candidate positions; mp.match validates each in < 1 µs.
    """
    candidate_line_starts: set[int] = set()
    for seed in _FIND_SEEDS:
        pos = text.find(seed)
        while pos != -1:
            ls = text.rfind("\n", 0, pos)
            candidate_line_starts.add(0 if ls == -1 else ls + 1)
            pos = text.find(seed, pos + 1)

    if not candidate_line_starts:
        return []

    # Dense-header guard: if too many candidates, fall back to finditer.
    if len(candidate_line_starts) > len(text) / 2000:
        return _headers_from_finditer(mp, text)

    headers: list[tuple[int, int, str]] = []
    for ls in sorted(candidate_line_starts):
        le = text.find("\n", ls)
        le = len(text) if le == -1 else le
        m = mp.match(text, ls, le)
        if not m:
            continue
        candidate = _clean_header(m.group(0))
        if _BANNER_SKIP.search(candidate):
            continue
        body_start = le + 1 if le < len(text) else len(text)
        headers.append((ls, body_start, candidate))

    return headers


def _headers_from_finditer(
    mp: re.Pattern, text: str
) -> list[tuple[int, int, str]]:
    """Original full-text scan (small files or dense header format)."""
    headers: list[tuple[int, int, str]] = []
    for m in mp.finditer(text):
        candidate = _clean_header(m.group(0))
        if _BANNER_SKIP.search(candidate):
            continue
        body_start = m.end()
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        headers.append((m.start(), body_start, candidate))
    return headers


def _build_chunks(name: str, text: str, headers: list[tuple[int, int, str]]) -> list[Chunk]:
    """Build Chunk objects from a sorted list of (h_start, body_start, title)."""
    chunks: list[Chunk] = []

    # Preamble before first header
    if headers[0][0] > 0:
        preamble = text[: headers[0][0]].splitlines()
        if any(l.strip() for l in preamble):
            chunks.append(Chunk(source_name=name, title=name, lines=preamble, start_line=0))

    running_lines = 0
    running_pos = 0

    for i, (h_start, body_start, title) in enumerate(headers):
        body_end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]

        running_lines += text.count("\n", running_pos, body_start)
        running_pos = body_start

        body_lines = body.splitlines()
        if any(l.strip() for l in body_lines):
            chunks.append(
                Chunk(source_name=name, title=title, lines=body_lines, start_line=running_lines)
            )

    return chunks


def _split_slow(name: str, lines: list[str], pattern: re.Pattern) -> list[Chunk]:
    """Original line-by-line split (used for small files < _FAST_THRESHOLD lines)."""
    chunks: list[Chunk] = []
    current_title = name
    current_start = 0
    current_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if pattern.match(stripped):
            candidate_title = _clean_header(line)
            if _BANNER_SKIP.search(candidate_title):
                current_lines.append(line)
                continue
            if current_lines:
                if any(l.strip() for l in current_lines):
                    chunks.append(
                        Chunk(
                            source_name=name,
                            title=current_title,
                            lines=current_lines,
                            start_line=current_start,
                        )
                    )
            current_title = candidate_title
            current_start = i
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines and any(l.strip() for l in current_lines):
        chunks.append(
            Chunk(
                source_name=name,
                title=current_title,
                lines=current_lines,
                start_line=current_start,
            )
        )

    return chunks


def _clean_header(line: str) -> str:
    """Strip delimiter chars from a header line."""
    return re.sub(r"^[-!#`\s]+|[-!#`\s]+$", "", line).strip()


def _derive_title_from_name(name: str) -> str:
    """Convert a NX-OS filename like 'show_interface_brief' → 'show interface brief'."""
    stem = name.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(txt|log|out|text)$", "", stem, flags=re.IGNORECASE)
    if stem.lower().startswith("show"):
        stem = stem.replace("_", " ").replace("-", " ")
    return stem
