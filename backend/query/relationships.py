"""
Query relationship graph for a given element.
Returns ownership, port-channel membership, vPC context, and peer info.
"""
import json
import re
from dataclasses import dataclass, field


@dataclass
class OwnershipInfo:
    """Where does this interface physically live?"""
    platform_hint: str          # "IOS-StackWise" | "NX-OS-Modular" | "NX-OS-Fixed" | "unknown"
    member_or_slot: str         # e.g. "member 2" or "slot 1"
    member_num: str             # raw number, e.g. "2"
    health_filter_hint: str     # query term to use when filtering Hardware domain


@dataclass
class RelationshipContext:
    ownership: OwnershipInfo | None
    port_channels: list[str]           # canonical PO names
    vpc_ids: list[str]                 # vPC IDs for each PO
    is_peer_link: bool
    vpc_domain_id: str
    vpc_peer_keepalive: str
    vpc_peer_link: str
    hostname: str
    platform: str
    serial: str
    mgmt_ip: str
    peer_session: dict | None          # populated later by correlate logic


def get_relationships(
    conn,
    session_id: str,
    canonical: str,
) -> RelationshipContext:
    """
    Return the full relationship context for a given element canonical name.
    """
    # 1. Ownership (STACK_MEMBER or MODULE_OWNER)
    ownership = _get_ownership(conn, session_id, canonical)

    # 2. Port-channel membership
    po_rows = conn.execute(
        """
        SELECT b_value FROM relationships
        WHERE session_id=? AND rel_type='MEMBER_OF_PO'
          AND lower(a_value)=lower(?)
        """,
        (session_id, canonical),
    ).fetchall()
    port_channels = [r[0] for r in po_rows]

    # 3. vPC IDs for each PO
    vpc_ids: list[str] = []
    for po in port_channels:
        rows = conn.execute(
            """
            SELECT b_value FROM relationships
            WHERE session_id=? AND rel_type='PO_IS_VPC'
              AND lower(a_value)=lower(?)
            """,
            (session_id, po),
        ).fetchall()
        vpc_ids.extend(r[0] for r in rows)

    # 4. Is this element itself a peer-link member?
    pl_row = conn.execute(
        """
        SELECT COUNT(*) FROM relationships
        WHERE session_id=? AND rel_type='IS_PEER_LINK'
          AND (lower(a_value)=lower(?) OR lower(a_value) IN (
              SELECT lower(b_value) FROM relationships
              WHERE session_id=? AND rel_type='MEMBER_OF_PO' AND lower(a_value)=lower(?)
          ))
        """,
        (session_id, canonical, session_id, canonical),
    ).fetchone()
    is_peer_link = pl_row[0] > 0

    # 5. vPC domain / peer info
    vpc_domain_id = _single(conn, session_id, "VPC_DOMAIN", "VPC_DOMAIN_ID")
    vpc_peer_keepalive = _single(conn, session_id, "VPC_PEER_KEEPALIVE", "IP")
    vpc_peer_link_val = _single(conn, session_id, "IS_PEER_LINK", b_type="PO")

    # 6. Device info
    dev = conn.execute(
        "SELECT hostname, platform, serial, mgmt_ip, vpc_peer_link FROM device_info WHERE session_id=?",
        [session_id],
    ).fetchone()
    if dev:
        hostname, platform, serial, mgmt_ip, peer_link_from_dev = dev[0], dev[1], dev[2], dev[3], dev[4]
    else:
        hostname = platform = serial = mgmt_ip = peer_link_from_dev = None

    return RelationshipContext(
        ownership=ownership,
        port_channels=port_channels,
        vpc_ids=vpc_ids,
        is_peer_link=is_peer_link,
        vpc_domain_id=vpc_domain_id or "",
        vpc_peer_keepalive=vpc_peer_keepalive or "",
        vpc_peer_link=peer_link_from_dev or vpc_peer_link_val or "",
        hostname=hostname or "",
        platform=platform or "",
        serial=serial or "",
        mgmt_ip=mgmt_ip or "",
        peer_session=None,
    )


def _get_ownership(conn, session_id, canonical) -> OwnershipInfo | None:
    # Try explicit STACK_MEMBER first
    row = conn.execute(
        """
        SELECT b_value FROM relationships
        WHERE session_id=? AND rel_type='STACK_MEMBER'
          AND lower(a_value)=lower(?)
        LIMIT 1
        """,
        (session_id, canonical),
    ).fetchone()
    if row:
        num = re.search(r"\d+", row[0]).group(0)
        return OwnershipInfo(
            platform_hint="IOS-StackWise",
            member_or_slot=f"Stack member {num}",
            member_num=num,
            health_filter_hint=f"switch {num}",
        )

    # Try MODULE_OWNER (NX-OS slot)
    row = conn.execute(
        """
        SELECT b_value FROM relationships
        WHERE session_id=? AND rel_type='MODULE_OWNER'
          AND lower(a_value)=lower(?)
        LIMIT 1
        """,
        (session_id, canonical),
    ).fetchone()
    if row:
        num = re.search(r"\d+", row[0]).group(0)
        return OwnershipInfo(
            platform_hint="NX-OS-Modular",
            member_or_slot=f"Slot/module {num}",
            member_num=num,
            health_filter_hint=f"module {num}",
        )

    # Infer from name alone
    # IOS: Gi2/0/15 → member 2
    m = re.match(r"(?:GigabitEthernet|TenGigabitEthernet|FastEthernet|HundredGigabitEthernet)(\d+)/\d+/\d+", canonical, re.IGNORECASE)
    if m:
        num = m.group(1)
        return OwnershipInfo(
            platform_hint="IOS-StackWise",
            member_or_slot=f"Stack member {num}",
            member_num=num,
            health_filter_hint=f"switch {num}",
        )

    # NX-OS: Ethernet2/15 → slot 2 (only meaningful for modular chassis)
    m = re.match(r"Ethernet(\d+)/\d+", canonical, re.IGNORECASE)
    if m:
        num = m.group(1)
        return OwnershipInfo(
            platform_hint="NX-OS",
            member_or_slot=f"Slot {num}",
            member_num=num,
            health_filter_hint=f"module {num}",
        )

    return None


def _single(conn, session_id, rel_type, b_type=None, a_type=None) -> str | None:
    """Fetch a single b_value for a rel_type."""
    clauses = ["session_id=?", "rel_type=?"]
    params: list = [session_id, rel_type]
    if b_type:
        clauses.append("b_type=?")
        params.append(b_type)
    if a_type:
        clauses.append("a_type=?")
        params.append(a_type)
    row = conn.execute(
        f"SELECT b_value FROM relationships WHERE {' AND '.join(clauses)} LIMIT 1",
        params,
    ).fetchone()
    return row[0] if row else None


def find_peer_session(
    all_sessions: list[dict],
    this_session_id: str,
    vpc_peer_keepalive: str,
    hostname: str,
) -> dict | None:
    """
    Look across all sessions to find the vPC peer dump.
    Match criteria: peer keepalive IP matches another session's mgmt_ip.
    """
    if not vpc_peer_keepalive:
        return None
    for s in all_sessions:
        if s.get("session_id") == this_session_id:
            continue
        if s.get("mgmt_ip") == vpc_peer_keepalive:
            return s
    return None


def serialize(ctx: RelationshipContext) -> dict:
    own = None
    if ctx.ownership:
        own = {
            "platform_hint": ctx.ownership.platform_hint,
            "member_or_slot": ctx.ownership.member_or_slot,
            "member_num": ctx.ownership.member_num,
            "health_filter_hint": ctx.ownership.health_filter_hint,
        }
    return {
        "ownership": own,
        "port_channels": ctx.port_channels,
        "vpc_ids": ctx.vpc_ids,
        "is_peer_link": ctx.is_peer_link,
        "vpc_domain_id": ctx.vpc_domain_id,
        "vpc_peer_keepalive": ctx.vpc_peer_keepalive,
        "vpc_peer_link": ctx.vpc_peer_link,
        "hostname": ctx.hostname,
        "platform": ctx.platform,
        "serial": ctx.serial,
        "mgmt_ip": ctx.mgmt_ip,
        "peer_session": ctx.peer_session,
    }
