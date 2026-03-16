"""
VRF relationship extraction.

Relationships produced:
  VRF_MEMBER      INTERFACE → VRF          (interface vrf member / ip vrf forwarding)
  VRF_MEMBER      VLAN → VRF               (SVI with vrf member)
  HAS_PROTOCOL    VRF → PROTOCOL           (bgp/ospf/isis/eigrp per-VRF)
  EXPORTS_RT      VRF → ROUTE_TARGET
  IMPORTS_RT      VRF → ROUTE_TARGET
  ROUTE_LEAKS_TO  VRF → VRF               (when both share a RT)
"""
import re
import json
from backend.normalize.entities import _canonical_iface

# ── Regexes ──────────────────────────────────────────────────────────────────

# "vrf member CUSTOMER-A"  (NX-OS)
_VRF_MEMBER_NXOS = re.compile(r"vrf\s+member\s+(\S+)", re.IGNORECASE)
# "ip vrf forwarding CUSTOMER-A"  (IOS/XE)
_VRF_FWD_IOS = re.compile(r"ip\s+vrf\s+forwarding\s+(\S+)", re.IGNORECASE)
# "vrf forwarding CUSTOMER-A"  (IOS-XE newer)
_VRF_FWD_XE = re.compile(r"vrf\s+forwarding\s+(\S+)", re.IGNORECASE)

# Interface block header
_IFACE_HDR = re.compile(
    r"^interface\s+((?:Eth|Gi|Te|Hu|Fo|Twe|Fa|GigabitEthernet|TenGigabitEthernet|"
    r"Ethernet|FastEthernet|Vlan|loopback|Port-channel|Tunnel)\s*[\d/.]+)",
    re.IGNORECASE | re.MULTILINE,
)

# "vrf context CUSTOMER-A" (NX-OS VRF definition block)
_VRF_CONTEXT = re.compile(r"^vrf\s+context\s+(\S+)", re.IGNORECASE | re.MULTILINE)
# "ip vrf CUSTOMER-A" (IOS VRF definition block)
_IP_VRF_DEF = re.compile(r"^ip\s+vrf\s+(\S+)", re.IGNORECASE | re.MULTILINE)

# show vrf / show ip vrf table lines
_SHOW_VRF_LINE = re.compile(
    r"^(\S+)\s+\d+\s+(Up|Down)\s",
    re.IGNORECASE | re.MULTILINE,
)
_SHOW_IP_VRF_LINE = re.compile(
    r"^(\S+)\s+\d+:\d+\s+ipv[46]",
    re.IGNORECASE | re.MULTILINE,
)

# Routing protocol per VRF
# NX-OS: "router bgp 65000" then "vrf CUSTOMER-A"
# IOS-XE: "router bgp 65000" -> "address-family ipv4 vrf CUSTOMER-A"
_BGP_STMT = re.compile(r"^router\s+bgp\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_OSPF_STMT = re.compile(r"^router\s+ospf\s+\d+\s+vrf\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_EIGRP_STMT = re.compile(r"^router\s+eigrp\s+\d+.*?vrf\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_ISIS_STMT = re.compile(r"^router\s+isis\s+(\S+)", re.IGNORECASE | re.MULTILINE)
# "address-family ipv4 vrf CUSTOMER-A"
_AF_VRF = re.compile(r"address-family\s+\S+\s+vrf\s+(\S+)", re.IGNORECASE)
# "vrf CUSTOMER-A" inside a router block
_VRF_SUBSECTION = re.compile(r"^\s{1,4}vrf\s+(\S+)", re.IGNORECASE | re.MULTILINE)

# OSPF on interface: "ip ospf 1 area 0" (IOS/XE) or "ospf 1 area 0" (NX-OS)
# Per-interface protocol config patterns
_OSPF_IF   = re.compile(r"(?:ip\s+)?ospf\s+\d+\s+area\s+\S+", re.IGNORECASE)
_ISIS_IF   = re.compile(r"\bisis\s+(?:enable\s+\S+|circuit-type|network|metric|hello)", re.IGNORECASE)
_EIGRP_IF  = re.compile(r"(?:ip\s+)?eigrp\s+\d+(?:\s+(?:enable|bandwidth-percent|summary)|\s*$)", re.IGNORECASE | re.MULTILINE)
_HSRP_IF   = re.compile(r"\bstandby\s+\d+\s+ip\b", re.IGNORECASE)
_VRRP_IF   = re.compile(r"\bvrrp\s+\d+\s+ip\b", re.IGNORECASE)
_PIM_IF    = re.compile(r"\bip\s+pim\s+(?:sparse|dense|passive|sparse-dense)", re.IGNORECASE)
_BGP_SRC   = re.compile(r"\bneighbor\s+\S+\s+update-source\s+(\S+)", re.IGNORECASE)

# Spanning-tree port table line:
#   "Gi1/0/1  Desg FWD 4  128.1  P2p"   (IOS/XE)
#   "Eth1/15  Desg FWD 20000  128.15  P2p"  (NX-OS)
# Role column: Root|Desg|Altn|Back|Mast  State: BLK|LRN|FWD|LIS|STP
_STP_PORT_LINE = re.compile(
    r"^\s*((?:Gi|Te|Hu|Fo|Twe|Fa|Eth(?:ernet)?|Po(?:rt-?channel)?)\s*[\d/]+)"
    r"\s+(?:Root|Desg|Altn|Back|Mast|Bkn|BDY)\s+(?:BLK|LRN|FWD|LIS|STP|DIS)\s",
    re.IGNORECASE | re.MULTILINE,
)
# Spanning-tree VLAN header: "VLAN0010" or "VLAN 10"
_STP_VLAN_HDR = re.compile(r"VLAN0*(\d+)", re.IGNORECASE)

# Route targets
_RT_IMPORT = re.compile(r"route-target\s+import\s+(\S+)", re.IGNORECASE)
_RT_EXPORT = re.compile(r"route-target\s+export\s+(\S+)", re.IGNORECASE)

# VRF-to-VRF static routes: "ip route vrf A 0.0.0.0/0 ... global"
_STATIC_CROSS = re.compile(
    r"ip\s+route\s+vrf\s+(\S+).*?\bglobal\b", re.IGNORECASE
)


def extract_vrf_relationships(conn, session_id: str) -> int:
    """Second-pass VRF extractor. Returns count of new relationships."""
    rows = conn.execute(
        "SELECT chunk_id, domain, title, body FROM chunks JOIN chunk_text USING(chunk_id) WHERE session_id=?",
        (session_id,),
    ).fetchall()

    before = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE session_id=?", (session_id,)
    ).fetchone()[0]

    # Collect RT→VRF maps for later leak resolution
    rt_imports: dict[str, list[str]] = {}   # RT → [VRFs that import it]
    rt_exports: dict[str, list[str]] = {}   # RT → [VRFs that export it]

    rel_buf: list = []
    node_buf: list = []

    for row in rows:
        chunk_id, domain, title, body = row[0], row[1], row[2], row[3]
        _process_chunk(rel_buf, node_buf, session_id, chunk_id, domain, title, body,
                       rt_imports, rt_exports)

    # Build VRF→VRF route-leak edges
    _build_leak_edges(rel_buf, node_buf, session_id, rt_imports, rt_exports)

    conn.begin()
    if node_buf:
        conn.executemany(
            "INSERT INTO graph_nodes (session_id, node_type, name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
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


def _process_chunk(rel_buf, node_buf, session_id, chunk_id, domain, title, body,
                   rt_imports, rt_exports):
    title_l = title.lower()

    # ── VRF catalog from show vrf / show ip vrf ──────────────────────────────
    if "show vrf" in title_l or "show ip vrf" in title_l:
        for m in _SHOW_VRF_LINE.finditer(body):
            _node(node_buf, session_id, "VRF", m.group(1))
        for m in _SHOW_IP_VRF_LINE.finditer(body):
            _node(node_buf, session_id, "VRF", m.group(1))

    # ── Config blocks ────────────────────────────────────────────────────────
    if domain in ("CONFIG", "UNKNOWN") or "config" in title_l or "running" in title_l:
        _parse_config(rel_buf, node_buf, session_id, chunk_id, body, rt_imports, rt_exports)

    # ── Spanning-tree show output ─────────────────────────────────────────────
    if "spanning" in title_l or "stp" in title_l:
        _parse_stp(rel_buf, node_buf, session_id, chunk_id, body)


def _parse_config(rel_buf, node_buf, session_id, chunk_id, body, rt_imports, rt_exports):
    # Catalog VRF definitions
    for m in _VRF_CONTEXT.finditer(body):
        vrf = m.group(1)
        if vrf.lower() == "management":
            continue
        _node(node_buf, session_id, "VRF", vrf)

    for m in _IP_VRF_DEF.finditer(body):
        _node(node_buf, session_id, "VRF", m.group(1))

    # ── Interface → VRF via config blocks ───────────────────────────────────
    segments = _IFACE_HDR.split(body)
    i = 1
    while i + 1 < len(segments):
        iface_raw = segments[i].strip()
        iface_body = segments[i + 1]
        i += 2
        iface_canon = _try_canon(iface_raw)

        itype = _iface_node_type(iface_canon)

        for pat in (_VRF_MEMBER_NXOS, _VRF_FWD_IOS, _VRF_FWD_XE):
            m = pat.search(iface_body)
            if m:
                vrf = m.group(1)
                _node(node_buf, session_id, "VRF", vrf)
                _node(node_buf, session_id, itype, iface_canon)
                _edge(rel_buf, node_buf, session_id, "VRF_MEMBER",
                      itype, iface_canon, "VRF", vrf, chunk_id)
                break

        # Per-interface routing/switching protocol detection
        for pat, proto in [
            (_OSPF_IF,  "OSPF"),
            (_ISIS_IF,  "ISIS"),
            (_EIGRP_IF, "EIGRP"),
            (_HSRP_IF,  "HSRP"),
            (_VRRP_IF,  "VRRP"),
            (_PIM_IF,   "PIM"),
        ]:
            if pat.search(iface_body):
                _node(node_buf, session_id, itype, iface_canon)
                _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL",
                      itype, iface_canon, "PROTOCOL", proto, chunk_id)

        # BGP update-source: "neighbor X update-source Loopback0" in interface config
        for bm in _BGP_SRC.finditer(iface_body):
            src_iface = _try_canon(bm.group(1))
            _node(node_buf, session_id, "INTERFACE", src_iface)
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL",
                  "INTERFACE", src_iface, "PROTOCOL", "BGP", chunk_id)

        # Spanning-tree config on interface (portfast, bpduguard, etc.)
        if re.search(r"\bspanning-tree\b", iface_body, re.IGNORECASE):
            _node(node_buf, session_id, itype, iface_canon)
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL",
                  itype, iface_canon, "PROTOCOL", "STP", chunk_id)

    # ── Routing protocols → VRF ──────────────────────────────────────────────
    # OSPF: "router ospf 1 vrf CUSTOMER-A"
    for m in _OSPF_STMT.finditer(body):
        vrf = m.group(1)
        _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL", "VRF", vrf, "PROTOCOL", "OSPF", chunk_id)

    # OSPF default VRF: "router ospf N" without vrf keyword
    for m in re.finditer(r"^router\s+ospf\s+\d+\s*$", body, re.IGNORECASE | re.MULTILINE):
        _node(node_buf, session_id, "PROTOCOL", "OSPF")

    # EIGRP per-VRF
    for m in _EIGRP_STMT.finditer(body):
        vrf = m.group(1)
        _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL", "VRF", vrf, "PROTOCOL", "EIGRP", chunk_id)

    # BGP: scan "router bgp N" block for sub-VRFs
    bgp_m = _BGP_STMT.search(body)
    if bgp_m:
        # NX-OS VRF subsections
        for sm in _VRF_SUBSECTION.finditer(body, bgp_m.start()):
            vrf = sm.group(1)
            if vrf.lower() in ("default", "management"):
                continue
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL", "VRF", vrf, "PROTOCOL", "BGP", chunk_id)
        # IOS-XE address-family vrf
        for sm in _AF_VRF.finditer(body, bgp_m.start()):
            vrf = sm.group(1)
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL", "VRF", vrf, "PROTOCOL", "BGP", chunk_id)

        # BGP update-source: "neighbor X update-source Loopback0"
        for sm in _BGP_SRC.finditer(body, bgp_m.start()):
            src_iface = _try_canon(sm.group(1))
            _node(node_buf, session_id, "INTERFACE", src_iface)
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL",
                  "INTERFACE", src_iface, "PROTOCOL", "BGP", chunk_id)

    # ── Route targets ────────────────────────────────────────────────────────
    # Find vrf-context blocks and their RT statements
    for ctx_m in _VRF_CONTEXT.finditer(body):
        vrf = ctx_m.group(1)
        ctx_start = ctx_m.end()
        # Find end of this VRF block (next vrf context or end of body)
        next_ctx = _VRF_CONTEXT.search(body, ctx_start)
        ctx_end = next_ctx.start() if next_ctx else len(body)
        vrf_block = body[ctx_start:ctx_end]

        for rt_m in _RT_IMPORT.finditer(vrf_block):
            rt = rt_m.group(1)
            _edge(rel_buf, node_buf, session_id, "IMPORTS_RT", "VRF", vrf, "ROUTE_TARGET", rt, chunk_id)
            rt_imports.setdefault(rt, []).append(vrf)

        for rt_m in _RT_EXPORT.finditer(vrf_block):
            rt = rt_m.group(1)
            _edge(rel_buf, node_buf, session_id, "EXPORTS_RT", "VRF", vrf, "ROUTE_TARGET", rt, chunk_id)
            rt_exports.setdefault(rt, []).append(vrf)

    # IOS-XE: address-family ipv4 vrf X + rt statements
    for af_m in _AF_VRF.finditer(body):
        vrf = af_m.group(1)
        af_start = af_m.end()
        af_block = body[af_start:af_start + 500]
        for rt_m in _RT_IMPORT.finditer(af_block):
            rt = rt_m.group(1)
            _edge(rel_buf, node_buf, session_id, "IMPORTS_RT", "VRF", vrf, "ROUTE_TARGET", rt, chunk_id)
            rt_imports.setdefault(rt, []).append(vrf)
        for rt_m in _RT_EXPORT.finditer(af_block):
            rt = rt_m.group(1)
            _edge(rel_buf, node_buf, session_id, "EXPORTS_RT", "VRF", vrf, "ROUTE_TARGET", rt, chunk_id)
            rt_exports.setdefault(rt, []).append(vrf)

    # Cross-VRF static route leaks (ip route vrf A ... global)
    for m in _STATIC_CROSS.finditer(body):
        vrf = m.group(1)
        _edge(rel_buf, node_buf, session_id, "ROUTE_LEAKS_TO", "VRF", vrf, "VRF", "default", chunk_id, "MED")


def _parse_stp(rel_buf, node_buf, session_id, chunk_id, body):
    """
    Parse 'show spanning-tree' output.
    Each VLAN block lists its member ports with role/state.
    Creates: HAS_PROTOCOL INTERFACE → STP
             STP_VLAN     INTERFACE → VLAN  (which VLAN's STP instance this port is in)
    """
    # Split body into per-VLAN blocks
    vlan_blocks = _STP_VLAN_HDR.split(body)
    # vlan_blocks: [pre, vlan_id, block, vlan_id, block, ...]
    i = 1
    while i + 1 < len(vlan_blocks):
        vlan_id = vlan_blocks[i]
        block   = vlan_blocks[i + 1]
        i += 2
        vlan_canon = f"Vlan{vlan_id}"
        for pm in _STP_PORT_LINE.finditer(block):
            iface_raw = pm.group(1).strip()
            try:
                iface_canon = _canonical_iface(iface_raw)
            except Exception:
                iface_canon = iface_raw
            itype = _iface_node_type(iface_canon)
            _node(node_buf, session_id, itype, iface_canon)
            _edge(rel_buf, node_buf, session_id, "HAS_PROTOCOL",
                  itype, iface_canon, "PROTOCOL", "STP", chunk_id)
            _edge(rel_buf, node_buf, session_id, "STP_VLAN",
                  itype, iface_canon, "VLAN", vlan_canon, chunk_id)


def _build_leak_edges(rel_buf, node_buf, session_id, rt_imports, rt_exports):
    """VRF A exports RT X, VRF B imports RT X → VRF A ROUTE_LEAKS_TO VRF B."""
    for rt, exporters in rt_exports.items():
        importers = rt_imports.get(rt, [])
        for exporter in exporters:
            for importer in importers:
                if exporter != importer:
                    _edge(rel_buf, node_buf, session_id, "ROUTE_LEAKS_TO",
                          "VRF", exporter, "VRF", importer, None, "HIGH")


def _node(node_buf: list, session_id, node_type, name):
    node_buf.append((session_id, node_type, name))


def _edge(rel_buf: list, node_buf: list, session_id, rel_type, a_type, a_val, b_type, b_val,
          chunk_id=None, confidence="HIGH"):
    rel_buf.append((session_id, rel_type, a_type, a_val, b_type, b_val, chunk_id, confidence))
    # Auto-register nodes
    _node(node_buf, session_id, a_type, a_val)
    _node(node_buf, session_id, b_type, b_val)


def _iface_node_type(canonical: str) -> str:
    c = canonical.lower()
    if c.startswith("vlan"):
        return "VLAN"
    if c.startswith("port-channel"):
        return "PORT_CHANNEL"
    if c.startswith("loopback"):
        return "LOOPBACK"
    return "INTERFACE"


def _try_canon(raw: str) -> str:
    try:
        return _canonical_iface(raw)
    except Exception:
        return raw.strip()
