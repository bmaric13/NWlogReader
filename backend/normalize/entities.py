"""Entity normalization: abbreviation expansion, regex extraction, FTS query building."""
import re
from dataclasses import dataclass

# Abbreviation → full name map (case-insensitive keys)
ABBREV_MAP: dict[str, str] = {
    "eth": "Ethernet",
    "gi": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "twe": "TwentyFiveGigE",
    "tge": "TenGigabitEthernet",
    "fa": "FastEthernet",
    "fo": "FortyGigabitEthernet",
    "hu": "HundredGigabitEthernet",
    "po": "Port-channel",
    "port-channel": "Port-channel",
    "portchannel": "Port-channel",
    "vlan": "Vlan",
    "lo": "Loopback",
    "loop": "Loopback",
    "tun": "Tunnel",
    "mg": "mgmt",
    "mgmt": "mgmt",
    "management": "mgmt",
}

# Regex patterns for entity extraction
_IFACE_ABBREVS = (
    r"(?:Hu|HundredGigabitEthernet|Fo|FortyGigabitEthernet|TwentyFiveGigE|Twe|"
    r"Te|TenGigabitEthernet|Gi|GigabitEthernet|Fa|FastEthernet|"
    r"Eth|Ethernet|ethernet)"
)
_IFACE_NUM = r"\d+(?:[/:.]\d+)*(?:\.\d+)?"  # 1/15, 1/0/15, 0/1, 1.100

ENTITY_PATTERNS: dict[str, re.Pattern] = {
    "IFACE": re.compile(
        rf"({_IFACE_ABBREVS}){_IFACE_NUM}",
        re.IGNORECASE,
    ),
    "PO": re.compile(
        r"(?:Port-channel|port-channel|portchannel|Po)(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    "VLAN": re.compile(
        r"(?:Vlan|VLAN|vlan)\s*(\d+)",
        re.IGNORECASE,
    ),
    "LOOPBACK": re.compile(
        r"(?:Loopback|Loopback|Lo)(\d+)",
        re.IGNORECASE,
    ),
    "IP": re.compile(
        r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?:/\d+)?\b",
    ),
    "MODULE": re.compile(
        r"(?:module|mod|slot)\s*(\d+)",
        re.IGNORECASE,
    ),
    "FEX": re.compile(
        r"FEX[-\s]?(\d+)",
        re.IGNORECASE,
    ),
    "VRF": re.compile(
        r"(?:vrf|VRF)\s+(\S+)",
        re.IGNORECASE,
    ),
}

# Combined pattern for fast first-pass detection
_ALL_IFACE_RE = re.compile(
    rf"({_IFACE_ABBREVS})\s*(\d+(?:[/:.]\d+)*(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass
class Entity:
    raw: str
    normalized: str
    canonical: str
    entity_type: str


def normalize_token(token: str) -> str:
    """Expand abbreviations to full interface name (case-insensitive)."""
    lower = token.lower().rstrip()
    # If token is already a full expanded form, return canonical capitalization
    for full in ABBREV_MAP.values():
        if lower == full.lower():
            return full
    # Expand abbreviation — must be an exact (whole-token) match, not a prefix
    for abbrev, full in sorted(ABBREV_MAP.items(), key=lambda x: -len(x[0])):
        if lower == abbrev.lower():
            return full
    return token


def _canonical_iface(raw: str) -> str:
    """Produce canonical interface string: expand abbrev, normalize spacing."""
    # Split on first digit boundary
    m = re.match(r"([A-Za-z\-]+)\s*(.+)", raw)
    if not m:
        return raw
    prefix, suffix = m.group(1), m.group(2)
    expanded = normalize_token(prefix)
    # Normalize suffix: remove spaces around / : .
    suffix = re.sub(r"\s*([/:.])\\s*", r"\1", suffix.strip())
    return expanded + suffix


def extract_entities(text: str) -> list[Entity]:
    """Extract all entities from a block of text. Returns deduplicated list."""
    seen: set[str] = set()
    results: list[Entity] = []

    def _add(raw: str, canonical: str, etype: str) -> None:
        key = f"{etype}:{canonical.lower()}"
        if key not in seen:
            seen.add(key)
            normalized = canonical
            results.append(Entity(raw=raw, normalized=normalized, canonical=canonical, entity_type=etype))

    # Interface (covers Eth, Gi, Te, etc.)
    for m in _ALL_IFACE_RE.finditer(text):
        raw = m.group(0).strip()
        canonical = _canonical_iface(raw)
        _add(raw, canonical, "IFACE")

    # Port-channel
    for m in ENTITY_PATTERNS["PO"].finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        _add(raw, f"Port-channel{num}", "PO")

    # VLAN
    for m in ENTITY_PATTERNS["VLAN"].finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        _add(raw, f"Vlan{num}", "VLAN")

    # Loopback
    for m in ENTITY_PATTERNS["LOOPBACK"].finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        _add(raw, f"Loopback{num}", "LOOPBACK")

    # IP address
    for m in ENTITY_PATTERNS["IP"].finditer(text):
        raw = m.group(0).strip()
        _add(raw, raw, "IP")

    # Module
    for m in ENTITY_PATTERNS["MODULE"].finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        _add(raw, f"module{num}", "MODULE")

    # FEX
    for m in ENTITY_PATTERNS["FEX"].finditer(text):
        raw = m.group(0).strip()
        num = m.group(1)
        _add(raw, f"FEX{num}", "FEX")

    # VRF
    for m in ENTITY_PATTERNS["VRF"].finditer(text):
        raw = m.group(0).strip()
        vrf_name = m.group(1)
        if vrf_name.lower() not in ("member", "aware", "table", "definition"):
            _add(raw, f"VRF:{vrf_name}", "VRF")

    return results


def build_fts_query(user_input: str) -> str:
    """Build FTS5 query with all known variants of an interface name."""
    user_input = user_input.strip()

    # Try to parse as interface
    m = re.match(r"([A-Za-z\-]+)\s*(\d+(?:[/:.]\d+)*(?:\.\d+)?)", user_input)
    if not m:
        # Fallback: quote the literal
        escaped = user_input.replace('"', '""')
        return f'"{escaped}"'

    prefix, num = m.group(1), m.group(2)
    expanded = normalize_token(prefix)

    variants: list[str] = []
    seen_v: set[str] = set()

    def _v(s: str) -> None:
        low = s.lower()
        if low not in seen_v:
            seen_v.add(low)
            escaped = s.replace('"', '""')
            variants.append(f'"{escaped}"')

    # Full expanded form
    _v(f"{expanded}{num}")
    _v(f"{expanded} {num}")

    # Original prefix form (if different)
    _v(f"{prefix}{num}")
    _v(f"{prefix} {num}")

    # All known abbreviations that map to the same full name
    for abbrev, full in ABBREV_MAP.items():
        if full.lower() == expanded.lower() and abbrev.lower() != prefix.lower():
            _v(f"{abbrev}{num}")
            _v(f"{abbrev} {num}")
            _v(f"{abbrev.capitalize()}{num}")

    return " OR ".join(variants)


def parse_user_element(user_input: str) -> dict:
    """Parse user element input and return metadata for querying."""
    user_input = user_input.strip()

    # Interface pattern
    m = re.match(r"([A-Za-z\-]+)\s*(\d+(?:[/:.]\d+)*(?:\.\d+)?)", user_input, re.IGNORECASE)
    if m:
        prefix, num = m.group(1), m.group(2)
        canonical = _canonical_iface(user_input)
        return {
            "raw": user_input,
            "canonical": canonical,
            "type": "IFACE",
            "fts_query": build_fts_query(user_input),
        }

    # VLAN
    m = re.match(r"(?:vlan)?\s*(\d+)$", user_input, re.IGNORECASE)
    if m:
        num = m.group(1)
        return {
            "raw": user_input,
            "canonical": f"Vlan{num}",
            "type": "VLAN",
            "fts_query": f'"Vlan{num}" OR "vlan {num}" OR "VLAN{num}"',
        }

    # IP
    m = re.match(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", user_input)
    if m:
        return {
            "raw": user_input,
            "canonical": user_input,
            "type": "IP",
            "fts_query": f'"{user_input}"',
        }

    # VRF — only when user explicitly types "vrf <name>"
    m = re.match(r"vrf\s+(\S+)", user_input, re.IGNORECASE)
    if m:
        return {
            "raw": user_input,
            "canonical": f"VRF:{m.group(1)}",
            "type": "VRF",
            "fts_query": f'"VRF {m.group(1)}" OR "vrf {m.group(1)}"',
        }

    # Bare keyword / free-text search (dhcp, bgp, spanning, etc.)
    escaped = user_input.replace('"', '""')
    return {
        "raw": user_input,
        "canonical": user_input,
        "type": "KEYWORD",
        "fts_query": f'"{escaped}"',
    }
