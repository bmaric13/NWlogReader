"""
Extract device-level metadata from indexed chunks and store in device_info table.
Runs once per session after ingestion completes.
"""
import json
import re

_HOSTNAME_RE = re.compile(r"^hostname\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_HOSTNAME_SWITCH = re.compile(r"switch\s+name\s*[:\s]+(\S+)", re.IGNORECASE)
_SERIAL_RE = re.compile(
    r"(?:system\s+serial\s+number|serial\s+(?:num(?:ber)?|no\.?))\s*[:\s]+([A-Z0-9]{6,20})",
    re.IGNORECASE,
)
_MGMT_IP_RE = re.compile(
    r"(?:interface\s+(?:mgmt\d*|Management\d*)[\s\S]{0,200}?ip\s+address\s+|"
    r"ip address\s+)(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    re.IGNORECASE,
)
_PLATFORM_RE = re.compile(
    r"(?:Cisco\s+)?(?:Nexus\s+\d+|Catalyst\s+\d+|ASR\s+\d+|ISR\s+\d+|N\d[KV]\d*)\s*\S*",
    re.IGNORECASE,
)
_VPC_DOMAIN_ID = re.compile(r"vPC\s+domain\s+id\s*[:\s]+(\d+)", re.IGNORECASE)
_VPC_PEER_KA = re.compile(
    r"peer.?keepalive\s+(?:destination|dst)\s+(?:is\s+)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    re.IGNORECASE,
)
_VPC_PEER_LINK = re.compile(
    r"peer.?link\s+port.?channel\s*[:\s]+(port-channel\d+|po\d+)",
    re.IGNORECASE,
)
_STACK_MEMBERS = re.compile(r"\bswitch\s+(\d+)\b", re.IGNORECASE)


def extract_device_info(conn, session_id: str) -> dict:
    """Scan high-signal chunks and populate device_info table."""
    # Pull bodies of version/config/vpc chunks
    rows = conn.execute(
        """
        SELECT ct.body, c.title, c.domain
        FROM chunks c JOIN chunk_text ct ON ct.chunk_id = c.chunk_id
        WHERE c.session_id = ?
          AND (lower(c.title) LIKE '%version%'
               OR lower(c.title) LIKE '%hostname%'
               OR lower(c.title) LIKE '%vpc%'
               OR lower(c.title) LIKE '%inventory%'
               OR lower(c.title) LIKE '%config%'
               OR c.domain = 'CONFIG')
        ORDER BY c.line_count DESC
        LIMIT 30
        """,
        (session_id,),
    ).fetchall()

    info = {
        "session_id": session_id,
        "hostname": None,
        "platform": None,
        "serial": None,
        "mgmt_ip": None,
        "vpc_domain_id": None,
        "vpc_peer_keepalive": None,
        "vpc_peer_link": None,
        "stack_members": None,
    }

    seen_members: set[str] = set()

    for row in rows:
        body, title, domain = row[0], row[1], row[2]

        if not info["hostname"]:
            m = _HOSTNAME_RE.search(body) or _HOSTNAME_SWITCH.search(body)
            if m:
                info["hostname"] = m.group(1)

        if not info["serial"]:
            m = _SERIAL_RE.search(body)
            if m:
                info["serial"] = m.group(1)

        if not info["platform"]:
            m = _PLATFORM_RE.search(body)
            if m:
                info["platform"] = m.group(0).strip()

        if not info["mgmt_ip"]:
            m = _MGMT_IP_RE.search(body)
            if m:
                ip = m.group(1)
                if not ip.startswith("0.") and ip != "255.255.255.255":
                    info["mgmt_ip"] = ip

        if not info["vpc_domain_id"]:
            m = _VPC_DOMAIN_ID.search(body)
            if m:
                info["vpc_domain_id"] = m.group(1)

        if not info["vpc_peer_keepalive"]:
            m = _VPC_PEER_KA.search(body)
            if m:
                info["vpc_peer_keepalive"] = m.group(1)

        if not info["vpc_peer_link"]:
            m = _VPC_PEER_LINK.search(body)
            if m:
                info["vpc_peer_link"] = m.group(1)

        for m in _STACK_MEMBERS.finditer(body):
            seen_members.add(m.group(1))

    if seen_members:
        info["stack_members"] = json.dumps(sorted(seen_members, key=int))

    conn.execute(
        """
        INSERT INTO device_info
            (session_id, hostname, platform, serial, mgmt_ip,
             vpc_domain_id, vpc_peer_keepalive, vpc_peer_link, stack_members)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            hostname           = excluded.hostname,
            platform           = excluded.platform,
            serial             = excluded.serial,
            mgmt_ip            = excluded.mgmt_ip,
            vpc_domain_id      = excluded.vpc_domain_id,
            vpc_peer_keepalive = excluded.vpc_peer_keepalive,
            vpc_peer_link      = excluded.vpc_peer_link,
            stack_members      = excluded.stack_members
        """,
        (
            info["session_id"], info["hostname"], info["platform"],
            info["serial"], info["mgmt_ip"], info["vpc_domain_id"],
            info["vpc_peer_keepalive"], info["vpc_peer_link"], info["stack_members"],
        ),
    )
    conn.commit()
    return info
