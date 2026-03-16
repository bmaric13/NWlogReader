"""
Dependency graph traversal over graph_nodes + relationships tables.

Provides:
  get_traffic_context(conn, session_id, canonical, max_depth=4)
      → TrafficContext  (chain + vrf info + route leaks)

  get_policies_for_element(conn, session_id, canonical)
      → PoliciesContext  (ACLs, PBR, QoS)
"""
from __future__ import annotations
import json
from collections import deque
from dataclasses import dataclass, field


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    node_type: str      # INTERFACE, PORT_CHANNEL, VLAN, VRF, PROTOCOL, ROUTE_TARGET, …
    name: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    rel_type: str
    a_type: str
    a_value: str
    b_type: str
    b_value: str


@dataclass
class ChainStep:
    node_type: str
    name: str
    rel_to_next: str | None = None   # relationship label leading to the next step
    metadata: dict = field(default_factory=dict)


@dataclass
class VrfInfo:
    name: str
    protocols: list[str]          # BGP, OSPF, …
    exports_rt: list[str]
    imports_rt: list[str]
    leaks_to: list[str]           # VRF names that this VRF leaks into
    leaks_from: list[str]         # VRF names that leak into this VRF


@dataclass
class TrafficContext:
    element: str
    chain: list[ChainStep]        # Eth1/15 → PO10 → Vlan100 → VRF CUSTOMER-A → BGP
    vrf: VrfInfo | None
    neighbors: list[GraphNode]    # other directly connected nodes


@dataclass
class AclPolicy:
    name: str
    direction: str           # "IN" or "OUT"
    node_type: str           # ACL
    uses_prefix_lists: list[str] = field(default_factory=list)


@dataclass
class PbrPolicy:
    route_map: str
    next_hops: list[str]
    uses_acls: list[str]
    uses_prefix_lists: list[str]


@dataclass
class QosPolicy:
    policy_map: str
    direction: str   # "IN" or "OUT"


@dataclass
class PoliciesContext:
    element: str
    acls: list[AclPolicy]
    pbr: list[PbrPolicy]
    qos: list[QosPolicy]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_node(conn, session_id: str, name: str) -> GraphNode | None:
    row = conn.execute(
        "SELECT node_type, name, metadata FROM graph_nodes WHERE session_id=? AND name=?",
        (session_id, name),
    ).fetchone()
    if not row:
        # Case-insensitive fallback (handles "ospf" → "OSPF", etc.)
        row = conn.execute(
            "SELECT node_type, name, metadata FROM graph_nodes WHERE session_id=? AND lower(name)=lower(?)",
            (session_id, name),
        ).fetchone()
    if not row:
        return None
    meta = json.loads(row[2]) if row[2] else {}
    return GraphNode(node_type=row[0], name=row[1], metadata=meta)


def _edges_from(conn, session_id: str, value: str) -> list[GraphEdge]:
    rows = conn.execute(
        "SELECT rel_type, a_type, a_value, b_type, b_value FROM relationships "
        "WHERE session_id=? AND a_value=?",
        (session_id, value),
    ).fetchall()
    return [GraphEdge(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _edges_to(conn, session_id: str, value: str) -> list[GraphEdge]:
    rows = conn.execute(
        "SELECT rel_type, a_type, a_value, b_type, b_value FROM relationships "
        "WHERE session_id=? AND b_value=?",
        (session_id, value),
    ).fetchall()
    return [GraphEdge(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _node_meta(conn, session_id: str, name: str) -> dict:
    row = conn.execute(
        "SELECT metadata FROM graph_nodes WHERE session_id=? AND name=?",
        (session_id, name),
    ).fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return {}


# ── Traffic context (chain traversal) ────────────────────────────────────────

# Relationship types that form the "forwarding path" chain, in traversal priority
_CHAIN_RELS = (
    "MEMBER_OF_PO",     # INTERFACE → PORT_CHANNEL
    "PO_IS_VPC",        # PORT_CHANNEL → (vpc id)
    "VRF_MEMBER",       # INTERFACE/VLAN → VRF
    "HAS_PROTOCOL",     # VRF → PROTOCOL
)

# Chain follows these rel types in this order of preference
_CHAIN_FORWARD: list[tuple[str, str]] = [
    # (rel_type, direction) — "fwd" = follow a_value→b_value, "rev" = b_value→a_value
    ("MEMBER_OF_PO", "fwd"),
    ("VRF_MEMBER", "fwd"),
    ("HAS_PROTOCOL", "fwd"),
    ("ROUTE_LEAKS_TO", "fwd"),
]

_CHAIN_NODE_TYPES = {
    "INTERFACE", "PORT_CHANNEL", "LOOPBACK", "VLAN",
    "VRF", "PROTOCOL", "ROUTE_TARGET",
}


def get_traffic_context(
    conn,
    session_id: str,
    canonical: str,
    max_depth: int = 6,
) -> TrafficContext:
    """BFS from canonical element following chain relationships."""
    chain: list[ChainStep] = []
    visited: set[str] = set()
    neighbors: list[GraphNode] = []

    # Start node
    start = _get_node(conn, session_id, canonical)
    if not start:
        # Create a virtual start node
        start = GraphNode(node_type="INTERFACE", name=canonical)

    # Use the actual stored name (may differ in case from user input)
    start_name = start.name

    queue: deque[tuple[GraphNode, int, str | None]] = deque()
    queue.append((start, 0, None))
    visited.add(start_name)

    # Ordered chain path (not just BFS tree)
    chain_path: list[ChainStep] = [ChainStep(
        node_type=start.node_type,
        name=start.name,
        metadata=start.metadata,
    )]
    chain_names: list[str] = [start_name]

    # Extend chain greedily: from current tail, find best next node
    current_name = start_name
    depth = 0
    while depth < max_depth:
        found_next = False
        for rel_type, direction in _CHAIN_FORWARD:
            if direction == "fwd":
                edges = [e for e in _edges_from(conn, session_id, current_name)
                         if e.rel_type == rel_type]
                for edge in edges:
                    nxt = edge.b_value
                    if nxt not in visited and edge.b_type in _CHAIN_NODE_TYPES:
                        visited.add(nxt)
                        meta = _node_meta(conn, session_id, nxt)
                        chain_path[-1].rel_to_next = rel_type
                        chain_path.append(ChainStep(
                            node_type=edge.b_type,
                            name=nxt,
                            metadata=meta,
                        ))
                        chain_names.append(nxt)
                        current_name = nxt
                        found_next = True
                        break
            if found_next:
                break
        if not found_next:
            break
        depth += 1

    # Collect all direct neighbors (not on chain) for context
    chain_set = set(chain_names)
    for edge in _edges_from(conn, session_id, start_name):
        if edge.b_value not in chain_set:
            meta = _node_meta(conn, session_id, edge.b_value)
            neighbors.append(GraphNode(edge.b_type, edge.b_value, meta))
    for edge in _edges_to(conn, session_id, start_name):
        if edge.a_value not in chain_set:
            meta = _node_meta(conn, session_id, edge.a_value)
            neighbors.append(GraphNode(edge.a_type, edge.a_value, meta))

    # Build VRF info if a VRF is in the chain
    vrf_info = None
    for step in chain_path:
        if step.node_type == "VRF":
            vrf_info = _build_vrf_info(conn, session_id, step.name)
            break

    return TrafficContext(
        element=canonical,
        chain=chain_path,
        vrf=vrf_info,
        neighbors=neighbors,
    )


def _build_vrf_info(conn, session_id: str, vrf_name: str) -> VrfInfo:
    protocols: list[str] = []
    exports_rt: list[str] = []
    imports_rt: list[str] = []
    leaks_to: list[str] = []
    leaks_from: list[str] = []

    for edge in _edges_from(conn, session_id, vrf_name):
        if edge.rel_type == "HAS_PROTOCOL":
            protocols.append(edge.b_value)
        elif edge.rel_type == "EXPORTS_RT":
            exports_rt.append(edge.b_value)
        elif edge.rel_type == "IMPORTS_RT":
            imports_rt.append(edge.b_value)
        elif edge.rel_type == "ROUTE_LEAKS_TO":
            leaks_to.append(edge.b_value)

    for edge in _edges_to(conn, session_id, vrf_name):
        if edge.rel_type == "ROUTE_LEAKS_TO":
            leaks_from.append(edge.a_value)

    return VrfInfo(
        name=vrf_name,
        protocols=protocols,
        exports_rt=exports_rt,
        imports_rt=imports_rt,
        leaks_to=leaks_to,
        leaks_from=leaks_from,
    )


# ── Policy context ────────────────────────────────────────────────────────────

def get_policies_for_element(
    conn,
    session_id: str,
    canonical: str,
) -> PoliciesContext:
    """Return all ACL, PBR, and QoS policies affecting canonical element."""
    acls: list[AclPolicy] = []
    pbr: list[PbrPolicy] = []
    qos: list[QosPolicy] = []

    # Edges pointing TO the element (ACL→IFACE, ROUTE_MAP→IFACE, POLICY_MAP→IFACE)
    for edge in _edges_to(conn, session_id, canonical):
        if edge.rel_type in ("ACL_IN", "ACL_OUT"):
            direction = "IN" if edge.rel_type == "ACL_IN" else "OUT"
            # Find prefix-lists this ACL uses (via ROUTE_MAP → USES_PREFIX, but ACL itself
            # may also appear in route-map USES_ACL edges; collect from graph)
            acls.append(AclPolicy(
                name=edge.a_value,
                direction=direction,
                node_type=edge.a_type,
            ))

        elif edge.rel_type == "PBR_APPLIED":
            rmap = edge.a_value
            meta = _node_meta(conn, session_id, rmap)
            nhops = meta.get("nhops", [])
            uses_acls: list[str] = []
            uses_pfx: list[str] = []
            for re_edge in _edges_from(conn, session_id, rmap):
                if re_edge.rel_type == "USES_ACL":
                    uses_acls.append(re_edge.b_value)
                elif re_edge.rel_type == "USES_PREFIX":
                    uses_pfx.append(re_edge.b_value)
            pbr.append(PbrPolicy(
                route_map=rmap,
                next_hops=nhops,
                uses_acls=uses_acls,
                uses_prefix_lists=uses_pfx,
            ))

        elif edge.rel_type in ("QOS_IN", "QOS_OUT"):
            direction = "IN" if edge.rel_type == "QOS_IN" else "OUT"
            qos.append(QosPolicy(policy_map=edge.a_value, direction=direction))

    return PoliciesContext(
        element=canonical,
        acls=acls,
        pbr=pbr,
        qos=qos,
    )


# ── Serialization helpers (for JSON API) ─────────────────────────────────────

def traffic_context_to_dict(ctx: TrafficContext) -> dict:
    return {
        "element": ctx.element,
        "chain": [
            {
                "node_type": s.node_type,
                "name": s.name,
                "rel_to_next": s.rel_to_next,
                "metadata": s.metadata,
            }
            for s in ctx.chain
        ],
        "vrf": (
            {
                "name": ctx.vrf.name,
                "protocols": ctx.vrf.protocols,
                "exports_rt": ctx.vrf.exports_rt,
                "imports_rt": ctx.vrf.imports_rt,
                "leaks_to": ctx.vrf.leaks_to,
                "leaks_from": ctx.vrf.leaks_from,
            }
            if ctx.vrf else None
        ),
        "neighbors": [
            {"node_type": n.node_type, "name": n.name, "metadata": n.metadata}
            for n in ctx.neighbors
        ],
    }


def policies_context_to_dict(ctx: PoliciesContext) -> dict:
    return {
        "element": ctx.element,
        "acls": [
            {"name": a.name, "direction": a.direction, "node_type": a.node_type,
             "uses_prefix_lists": a.uses_prefix_lists}
            for a in ctx.acls
        ],
        "pbr": [
            {
                "route_map": p.route_map,
                "next_hops": p.next_hops,
                "uses_acls": p.uses_acls,
                "uses_prefix_lists": p.uses_prefix_lists,
            }
            for p in ctx.pbr
        ],
        "qos": [
            {"policy_map": q.policy_map, "direction": q.direction}
            for q in ctx.qos
        ],
    }
