"""
Relationship extraction from indexed chunks.
Runs as a second pass after all chunks are indexed.

Relationship types:
  MEMBER_OF_PO   IFACE → PO         "Eth1/15 is member of Po10"
  PO_IS_VPC      PO → VPC_ID        "Po10 is vPC 10"
  IS_PEER_LINK   PO → VPC_DOMAIN    "Po1 is the peer-link"
  VPC_PEER_KEEPALIVE  VPC_DOMAIN → IP   "vPC peer keepalive 10.0.0.2"
  VPC_DOMAIN     DEVICE → VPC_DOMAIN  "This device is in vPC domain 10"
  STACK_MEMBER   IFACE → MEMBER      "Gi2/0/15 lives on stack member 2"
  MODULE_OWNER   IFACE → MODULE      "Ethernet2/15 lives on module/slot 2"
"""
import re
from dataclasses import dataclass
from backend.normalize.entities import _canonical_iface


# ── Regex patterns ──────────────────────────────────────────────────────────

# IOS/XE port-channel summary:  "1   Po1(SU)   LACP   Gi1/0/1(P)  Gi2/0/15(P)"
# NX-OS port-channel summary:   "10  Po10(SU)  Eth   LACP   Eth1/15(P) Eth1/16(P)"
_PO_SUMMARY_LINE = re.compile(
    r"(?P<po>Po(?:rt-?channel)?\s*\d+)\s*\([A-Z]+\).*?(?P<members>(?:(?:Eth|Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|Ethernet|FastEthernet)\s*[\d/]+\([A-Za-z]+\)\s*)+)",
    re.IGNORECASE,
)
_MEMBER_TOKEN = re.compile(
    r"((?:Eth|Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|Ethernet|FastEthernet)\s*[\d/]+)\s*\([A-Za-z]+\)",
    re.IGNORECASE,
)

# Config: "channel-group 10 mode active"  inside an interface block
_CHAN_GROUP = re.compile(r"channel-group\s+(\d+)\s+mode", re.IGNORECASE)
_IFACE_BLOCK_HDR = re.compile(
    r"^interface\s+((?:Eth|Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|Ethernet|FastEthernet)\s*[\d/]+)",
    re.IGNORECASE | re.MULTILINE,
)

# show vpc: "vPC domain id   : 10"
_VPC_DOMAIN_ID = re.compile(r"vPC\s+domain\s+id\s*[:\s]+(\d+)", re.IGNORECASE)
# show vpc: peer-keepalive destination
_VPC_PEER_KA = re.compile(
    r"peer.?keepalive\s+(?:destination|dst)\s+(?:is\s+)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    re.IGNORECASE,
)
# show vpc: peer-link port-channel
_VPC_PEER_LINK = re.compile(r"peer.?link\s+port.?channel\s*[:\s]+(port-channel\d+|po\d+)", re.IGNORECASE)
# show vpc table: "10   Po10   up   success"
_VPC_TABLE_LINE = re.compile(
    r"^\s*(\d+)\s+(Po(?:rt-?channel)?\s*\d+)\s+\S+\s+\S+",
    re.IGNORECASE | re.MULTILINE,
)

# IOS StackWise: Gi2/0/15 → member 2 (first number)
_IOS_STACK_IFACE = re.compile(
    r"((?:Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|FastEthernet)\s*)(\d+)/(\d+)/(\d+)",
    re.IGNORECASE,
)
# NX-OS slot: Ethernet2/15 → slot 2 (first number)
_NXOS_SLOT_IFACE = re.compile(
    r"(Eth(?:ernet)?\s*)(\d+)/(\d+)",
    re.IGNORECASE,
)


# ── DB helpers ──────────────────────────────────────────────────────────────

def _upsert_rel(buf: list, session_id, rel_type, a_type, a_val, b_type, b_val,
                chunk_id=None, confidence="MED"):
    """Append a relationship row to buf; caller flushes with executemany."""
    buf.append((session_id, rel_type, a_type, a_val, b_type, b_val, chunk_id, confidence))


def _flush_rels(conn, buf: list) -> None:
    if not buf:
        return
    conn.executemany(
        """
        INSERT INTO relationships
            (session_id, rel_type, a_type, a_value, b_type, b_value, evidence_chunk_id, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, rel_type, a_value, b_value) DO NOTHING
        """,
        buf,
    )
    buf.clear()


# ── Main extraction pass ─────────────────────────────────────────────────────

def extract_relationships(conn, session_id: str) -> int:
    """
    Run after all chunks are indexed. Scans chunk bodies for relationship signals.
    Returns count of relationships inserted.
    """
    rows = conn.execute(
        "SELECT chunk_id, domain, title, body FROM chunks JOIN chunk_text USING(chunk_id) WHERE session_id=?",
        (session_id,),
    ).fetchall()

    before = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE session_id=?", (session_id,)
    ).fetchone()[0]

    buf: list = []
    for row in rows:
        chunk_id, domain, title, body = row[0], row[1], row[2], row[3]
        _extract_from_chunk(buf, session_id, chunk_id, domain, title, body)

    conn.begin()
    _flush_rels(conn, buf)
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE session_id=?", (session_id,)
    ).fetchone()[0]
    return after - before


def _extract_from_chunk(buf, session_id, chunk_id, domain, title, body):
    title_low = title.lower()

    # ── Port-channel summary (NX-OS + IOS) ──────────────────────────────────
    if "port-channel" in title_low or "etherchannel" in title_low or "po summary" in title_low:
        _parse_po_summary(buf, session_id, chunk_id, body)

    # ── Channel-group config ─────────────────────────────────────────────────
    if "running" in title_low or "config" in title_low or domain == "CONFIG":
        _parse_channel_group_config(buf, session_id, chunk_id, body)

    # ── vPC (show vpc) ───────────────────────────────────────────────────────
    if "vpc" in title_low:
        _parse_vpc(buf, session_id, chunk_id, body)

    # ── Interface-level membership hints ────────────────────────────────────
    # "Ethernet1/15 is a member of port-channel 10"
    _parse_member_hints(buf, session_id, chunk_id, body)

    # ── Stack / slot ownership from any chunk ────────────────────────────────
    _infer_ownership(buf, session_id, chunk_id, body)


def _parse_po_summary(buf, session_id, chunk_id, body):
    """Parse port-channel summary tables → MEMBER_OF_PO relationships."""
    for m in _PO_SUMMARY_LINE.finditer(body):
        po_raw = m.group("po").strip()
        po_canonical = _norm_po(po_raw)
        members_str = m.group("members")
        for mem in _MEMBER_TOKEN.finditer(members_str):
            iface_raw = mem.group(1).strip()
            iface_canon = _canonical_iface(iface_raw)
            _upsert_rel(buf, session_id,
                        "MEMBER_OF_PO", "IFACE", iface_canon,
                        "PO", po_canonical, chunk_id, "HIGH")


def _parse_channel_group_config(buf, session_id, chunk_id, body):
    """Parse 'interface X / channel-group N mode' config blocks."""
    # Split on interface headers, then look for channel-group inside each block
    segments = _IFACE_BLOCK_HDR.split(body)
    # segments: [pre, iface1, body1, iface2, body2, ...]
    i = 1
    while i + 1 < len(segments):
        iface_raw = segments[i].strip()
        iface_body = segments[i + 1]
        i += 2
        m = _CHAN_GROUP.search(iface_body)
        if m:
            iface_canon = _canonical_iface(iface_raw)
            po_canon = f"Port-channel{m.group(1)}"
            _upsert_rel(buf, session_id,
                        "MEMBER_OF_PO", "IFACE", iface_canon,
                        "PO", po_canon, chunk_id, "HIGH")


def _parse_vpc(buf, session_id, chunk_id, body):
    """Parse show vpc output → VPC_DOMAIN, PO_IS_VPC, peer-link, peer keepalive."""
    # Domain ID
    dm = _VPC_DOMAIN_ID.search(body)
    domain_id = dm.group(1) if dm else None
    if domain_id:
        _upsert_rel(buf, session_id,
                    "VPC_DOMAIN", "DEVICE", "self",
                    "VPC_DOMAIN_ID", domain_id, chunk_id, "HIGH")

    # Peer keepalive
    pkm = _VPC_PEER_KA.search(body)
    if pkm:
        _upsert_rel(buf, session_id,
                    "VPC_PEER_KEEPALIVE", "VPC_DOMAIN", domain_id or "?",
                    "IP", pkm.group(1), chunk_id, "HIGH")

    # Peer-link
    plm = _VPC_PEER_LINK.search(body)
    if plm:
        pl_canon = _norm_po(plm.group(1))
        _upsert_rel(buf, session_id,
                    "IS_PEER_LINK", "PO", pl_canon,
                    "VPC_DOMAIN", domain_id or "?", chunk_id, "HIGH")

    # vPC table: vpc_id → port-channel
    for m in _VPC_TABLE_LINE.finditer(body):
        vpc_id = m.group(1)
        po_raw = m.group(2).strip()
        po_canon = _norm_po(po_raw)
        _upsert_rel(buf, session_id,
                    "PO_IS_VPC", "PO", po_canon,
                    "VPC_ID", f"vPC{vpc_id}", chunk_id, "HIGH")


def _parse_member_hints(buf, session_id, chunk_id, body):
    """Catch freeform lines like 'Eth1/15 is member of port-channel 10'."""
    pattern = re.compile(
        r"((?:Eth|Gi|Te|Ethernet|GigabitEthernet|TenGigabitEthernet)\s*[\d/]+)"
        r".*?(?:member(?:\s+of)?|belongs\s+to)\s+"
        r"((?:port.?channel|Po)\s*\d+)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(body):
        iface_canon = _canonical_iface(m.group(1).strip())
        po_canon = _norm_po(m.group(2).strip())
        _upsert_rel(buf, session_id,
                    "MEMBER_OF_PO", "IFACE", iface_canon,
                    "PO", po_canon, chunk_id, "MED")


def _infer_ownership(buf, session_id, chunk_id, body):
    """
    Infer stack member / slot from interface names found in the chunk.
    IOS: Gi2/0/15 → member 2
    NX-OS: Ethernet2/15 → slot 2
    Only insert LOW confidence since it's purely name-based.
    """
    # IOS StackWise
    for m in _IOS_STACK_IFACE.finditer(body):
        member = m.group(2)
        iface_raw = m.group(0)
        iface_canon = _canonical_iface(iface_raw)
        _upsert_rel(buf, session_id,
                    "STACK_MEMBER", "IFACE", iface_canon,
                    "MEMBER", f"member{member}", chunk_id, "HIGH")

    # NX-OS slot
    for m in _NXOS_SLOT_IFACE.finditer(body):
        slot = m.group(2)
        iface_raw = m.group(0)
        iface_canon = _canonical_iface(iface_raw)
        _upsert_rel(buf, session_id,
                    "MODULE_OWNER", "IFACE", iface_canon,
                    "MODULE", f"slot{slot}", chunk_id, "MED")


def _norm_po(raw: str) -> str:
    """Normalize Port-channel name."""
    num = re.search(r"(\d+)", raw)
    return f"Port-channel{num.group(1)}" if num else raw.strip()
