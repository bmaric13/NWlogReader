"""
ACL / PBR / QoS / prefix-list / route-map policy extraction.

Relationships produced:
  ACL_IN          ACL → INTERFACE/VRF      (inbound)
  ACL_OUT         ACL → INTERFACE/VRF      (outbound)
  PBR_APPLIED     ROUTE_MAP → INTERFACE/VRF
  QOS_IN          POLICY_MAP → INTERFACE   (service-policy input)
  QOS_OUT         POLICY_MAP → INTERFACE   (service-policy output)
  USES_ACL        ROUTE_MAP → ACL
  USES_PREFIX     ROUTE_MAP → PREFIX_LIST
  PERMIT_DENY     ACL → (summary)          stored in node metadata
"""
import re
import json
from backend.normalize.entities import _canonical_iface

# ── ACL definitions ─────────────────────────────────────────────────────────
_ACL_DEF_NXOS = re.compile(
    r"^ip\s+access-list\s+(\S+)", re.IGNORECASE | re.MULTILINE
)
_ACL_DEF_IOS = re.compile(
    r"^ip\s+access-list\s+(?:extended|standard)\s+(\S+)", re.IGNORECASE | re.MULTILINE
)
_ACL_NAMED = re.compile(
    r"^ip\s+access-list\s+(?:extended\s+|standard\s+)?(\S+)", re.IGNORECASE | re.MULTILINE
)

# ACL applied to interface
_ACL_IF_IN = re.compile(r"ip\s+access-group\s+(\S+)\s+in", re.IGNORECASE)
_ACL_IF_OUT = re.compile(r"ip\s+access-group\s+(\S+)\s+out", re.IGNORECASE)
# NX-OS
_ACL_IF_IN_NX = re.compile(r"ip\s+port\s+access-group\s+(\S+)\s+in", re.IGNORECASE)
_ACL_IF_OUT_NX = re.compile(r"ip\s+port\s+access-group\s+(\S+)\s+out", re.IGNORECASE)
# IPv6
_ACL6_IN = re.compile(r"ipv6\s+traffic-filter\s+(\S+)\s+in", re.IGNORECASE)
_ACL6_OUT = re.compile(r"ipv6\s+traffic-filter\s+(\S+)\s+out", re.IGNORECASE)
# Control-plane ACL
_CP_ACL = re.compile(r"ip\s+access-group\s+(\S+)\s+in", re.IGNORECASE)

# ACL applied to VRF (BGP distribute-list)
_ACL_VRF_DIST = re.compile(r"distribute-list\s+(\S+)\s+(in|out)", re.IGNORECASE)

# ── Route-maps ───────────────────────────────────────────────────────────────
_RMAP_DEF = re.compile(
    r"^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)", re.IGNORECASE | re.MULTILINE
)
_RMAP_MATCH_ACL = re.compile(r"match\s+ip\s+address\s+(\S+)", re.IGNORECASE)
_RMAP_MATCH_PFX = re.compile(r"match\s+ip\s+address\s+prefix-list\s+(\S+)", re.IGNORECASE)
_RMAP_NHOP = re.compile(r"set\s+ip\s+next-hop\s+(\S+)", re.IGNORECASE)

# PBR applied to interface
_PBR_APPLIED = re.compile(r"ip\s+policy\s+route-map\s+(\S+)", re.IGNORECASE)

# ── QoS / Service-policy ────────────────────────────────────────────────────
_POLICY_MAP_DEF = re.compile(r"^policy-map\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_SVC_IN = re.compile(r"service-policy\s+input\s+(\S+)", re.IGNORECASE)
_SVC_OUT = re.compile(r"service-policy\s+output\s+(\S+)", re.IGNORECASE)

# ── Prefix lists ─────────────────────────────────────────────────────────────
_PFX_LIST = re.compile(
    r"^ip\s+prefix-list\s+(\S+)\s+(?:seq\s+\d+\s+)?(permit|deny)\s+(\S+)",
    re.IGNORECASE | re.MULTILINE,
)

# Interface block header
_IFACE_HDR = re.compile(
    r"^interface\s+((?:Eth|Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|"
    r"Ethernet|FastEthernet|Vlan|Loopback|Port-channel|Tunnel)\s*[\d/.]+)",
    re.IGNORECASE | re.MULTILINE,
)


def extract_policy_relationships(conn, session_id: str) -> int:
    rows = conn.execute(
        "SELECT chunk_id, domain, title, body FROM chunks JOIN chunk_text USING(chunk_id) WHERE session_id=?",
        (session_id,),
    ).fetchall()

    before = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE session_id=?", (session_id,)
    ).fetchone()[0]

    # Two-pass: first catalog all definitions, then wire applications
    acl_names: set[str] = set()
    rmap_names: dict[str, dict] = {}   # name → {acls, prefix_lists, nhops}
    pmap_names: set[str] = set()

    rel_buf: list = []
    node_buf: list = []   # (session_id, node_type, name, metadata)

    for row in rows:
        chunk_id, domain, title, body = row[0], row[1], row[2], row[3]
        if _is_config(domain, title):
            _catalog_defs(rel_buf, node_buf, session_id, chunk_id, body,
                          acl_names, rmap_names, pmap_names)

    for row in rows:
        chunk_id, domain, title, body = row[0], row[1], row[2], row[3]
        if _is_config(domain, title):
            _wire_applications(rel_buf, node_buf, session_id, chunk_id, body,
                               rmap_names)

    conn.begin()
    if node_buf:
        conn.executemany(
            "INSERT INTO graph_nodes (session_id, node_type, name, metadata) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            node_buf,
        )
    if rel_buf:
        conn.executemany(
            """
            INSERT INTO relationships
                (session_id, rel_type, a_type, a_value, b_type, b_value, evidence_chunk_id, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, rel_type, a_value, b_value) DO NOTHING
            """,
            rel_buf,
        )
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE session_id=?", (session_id,)
    ).fetchone()[0]
    return after - before


def _is_config(domain: str, title: str) -> bool:
    tl = title.lower()
    return domain in ("CONFIG", "UNKNOWN") or "config" in tl or "running" in tl


def _catalog_defs(rel_buf, node_buf, session_id, chunk_id, body,
                  acl_names, rmap_names, pmap_names):
    # ACL definitions
    for m in _ACL_NAMED.finditer(body):
        name = m.group(1)
        acl_names.add(name)
        _node(node_buf, session_id, "ACL", name)

    # Route-map definitions + their referenced ACLs/prefix-lists
    for m in _RMAP_DEF.finditer(body):
        name = m.group(1)
        rmap_names.setdefault(name, {"acls": [], "prefix_lists": [], "nhops": []})
        # scan the clause body (between this and next route-map entry)
        clause_start = m.end()
        next_m = _RMAP_DEF.search(body, clause_start)
        clause = body[clause_start: next_m.start() if next_m else len(body)]

        for am in _RMAP_MATCH_PFX.finditer(clause):
            pl = am.group(1)
            rmap_names[name]["prefix_lists"].append(pl)
            _node(node_buf, session_id, "PREFIX_LIST", pl)
            _edge(rel_buf, node_buf, session_id, "USES_PREFIX", "ROUTE_MAP", name,
                  "PREFIX_LIST", pl, chunk_id)

        for am in _RMAP_MATCH_ACL.finditer(clause):
            acl = am.group(1)
            # skip if it's actually a prefix-list (already handled above)
            if acl not in rmap_names[name]["prefix_lists"]:
                rmap_names[name]["acls"].append(acl)
                _edge(rel_buf, node_buf, session_id, "USES_ACL", "ROUTE_MAP", name,
                      "ACL", acl, chunk_id)

        for nm in _RMAP_NHOP.finditer(clause):
            rmap_names[name]["nhops"].append(nm.group(1))

        _node(node_buf, session_id, "ROUTE_MAP", name,
              meta={"nhops": rmap_names[name]["nhops"]})

    # Policy-map definitions
    for m in _POLICY_MAP_DEF.finditer(body):
        name = m.group(1)
        pmap_names.add(name)
        _node(node_buf, session_id, "POLICY_MAP", name)

    # Prefix-list definitions
    for m in _PFX_LIST.finditer(body):
        name = m.group(1)
        _node(node_buf, session_id, "PREFIX_LIST", name)


def _wire_applications(rel_buf, node_buf, session_id, chunk_id, body, rmap_names):
    """Wire ACLs, PBR, QoS onto interfaces by scanning interface config blocks."""
    segments = _IFACE_HDR.split(body)
    i = 1
    while i + 1 < len(segments):
        iface_raw = segments[i].strip()
        iface_body = segments[i + 1]
        i += 2

        try:
            iface_canon = _canonical_iface(iface_raw)
        except Exception:
            iface_canon = iface_raw.strip()

        itype = _iface_type(iface_canon)
        _node(node_buf, session_id, itype, iface_canon)

        # ACLs (inbound)
        for pat, rel in [(_ACL_IF_IN, "ACL_IN"), (_ACL_IF_IN_NX, "ACL_IN"),
                         (_ACL6_IN, "ACL_IN")]:
            for m in pat.finditer(iface_body):
                acl = m.group(1)
                _node(node_buf, session_id, "ACL", acl)
                _edge(rel_buf, node_buf, session_id, rel, "ACL", acl, itype, iface_canon, chunk_id)

        # ACLs (outbound)
        for pat, rel in [(_ACL_IF_OUT, "ACL_OUT"), (_ACL_IF_OUT_NX, "ACL_OUT"),
                         (_ACL6_OUT, "ACL_OUT")]:
            for m in pat.finditer(iface_body):
                acl = m.group(1)
                _node(node_buf, session_id, "ACL", acl)
                _edge(rel_buf, node_buf, session_id, rel, "ACL", acl, itype, iface_canon, chunk_id)

        # PBR
        for m in _PBR_APPLIED.finditer(iface_body):
            rmap = m.group(1)
            nhops = rmap_names.get(rmap, {}).get("nhops", [])
            _node(node_buf, session_id, "ROUTE_MAP", rmap, meta={"nhops": nhops})
            _edge(rel_buf, node_buf, session_id, "PBR_APPLIED", "ROUTE_MAP", rmap, itype, iface_canon, chunk_id)

        # QoS
        for m in _SVC_IN.finditer(iface_body):
            pm = m.group(1)
            _node(node_buf, session_id, "POLICY_MAP", pm)
            _edge(rel_buf, node_buf, session_id, "QOS_IN", "POLICY_MAP", pm, itype, iface_canon, chunk_id)
        for m in _SVC_OUT.finditer(iface_body):
            pm = m.group(1)
            _node(node_buf, session_id, "POLICY_MAP", pm)
            _edge(rel_buf, node_buf, session_id, "QOS_OUT", "POLICY_MAP", pm, itype, iface_canon, chunk_id)


def _node(node_buf: list, session_id, node_type, name, meta=None):
    node_buf.append((session_id, node_type, name, json.dumps(meta) if meta else None))


def _edge(rel_buf: list, node_buf: list, session_id, rel_type, a_type, a_val, b_type, b_val,
          chunk_id=None, confidence="HIGH"):
    rel_buf.append((session_id, rel_type, a_type, a_val, b_type, b_val, chunk_id, confidence))
    _node(node_buf, session_id, a_type, a_val)
    _node(node_buf, session_id, b_type, b_val)


def _iface_type(canonical: str) -> str:
    c = canonical.lower()
    if c.startswith("vlan"):
        return "VLAN"
    if c.startswith("port-channel"):
        return "PORT_CHANNEL"
    if c.startswith("loopback"):
        return "LOOPBACK"
    return "INTERFACE"
