"""
Daemon inference engine — maps syslog tags + keywords → daemon hints.
Returns DaemonHint list enriched from process_glossary.
"""

import re
from dataclasses import dataclass, field
from backend.query.process_glossary import get_process_info

# ── Syslog facility/tag → daemon(s) ──────────────────────────────────────────
# NX-OS syslog format: %FACILITY-SEVERITY-MNEMONIC: message
SYSLOG_TAG_MAP: dict[str, list[str]] = {
    "ETHPORT": ["ethpm"],
    "ETH_PORT_CHANNEL": ["lacp", "ethpm"],
    "LACP": ["lacp"],
    "PORT_CHANNEL": ["lacp"],
    "VPC": ["vpc"],
    "VPC_STKY": ["vpc"],
    "BGP": ["bgp"],
    "BGP-5": ["bgp"],
    "OSPF": ["ospf"],
    "OSPF_MIB": ["ospf"],
    "ISIS": ["isis"],
    "STP": ["stp"],
    "SPANNING_TREE": ["stp"],
    "RSTP": ["stp"],
    "MSTP": ["stp"],
    "L2FM": ["l2fm"],
    "L2_MULTICAST": ["l2fm"],
    "SYSMGR": ["sysmgr"],
    "MODULE": ["hardware"],
    "MODULE-5": ["hardware"],
    "PLATFORM": ["hardware"],
    "KERN": ["hardware"],
    "ARP": ["arp"],
    "ICMP": ["arp"],
    "IPV6": ["arp"],
    # IOS-XE common
    "LINEPROTO": ["ethpm"],
    "LINK": ["ethpm"],
    "UPDOWN": ["ethpm"],
    "DTP": ["stp"],
    "SPANTREE": ["stp"],
    "EC": ["lacp"],  # EtherChannel
    "STORM_CONTROL": ["ethpm"],
    "UDLD": ["ethpm"],
    "CDP": ["ethpm"],
    "IPADJCHANGE": ["ospf", "bgp"],
    "OSPF_ADJ": ["ospf"],
    "BGP_SESSION": ["bgp"],
    "FIB": ["hardware"],
    "EIGRP": ["eigrp"],
    "HSRP": ["hsrp"],
    "VRRP": ["vrrp"],
    "PIM": ["pim"],
    "IGMP": ["pim"],
    "MSDP": ["pim"],
    "CEFSLOT": ["hardware"],
    "OIR": ["hardware"],
    "SYS": ["sysmgr"],
}

# ── Keyword patterns → daemon(s) ─────────────────────────────────────────────
# Each entry: (compiled_regex, [daemon_keys], weight)
# weight: 2=strong (specific), 1=weak (generic)
KEYWORD_RULES: list[tuple[re.Pattern, list[str], int]] = [
    # Interface / physical
    (re.compile(r"\berr[_-]?disabl|errdisable\b", re.IGNORECASE), ["ethpm"], 2),
    (
        re.compile(r"\blink\s+(up|down|flap|not connected)\b", re.IGNORECASE),
        ["ethpm"],
        2,
    ),
    (re.compile(r"\bSFP|transceiver|optic|DOM\b", re.IGNORECASE), ["ethpm"], 2),
    (
        re.compile(r"\bCRC\b|\binput error|\bout error|overrun\b", re.IGNORECASE),
        ["ethpm", "hardware"],
        2,
    ),
    (re.compile(r"\bduplex|speed mismatch|autoneg\b", re.IGNORECASE), ["ethpm"], 2),
    (re.compile(r"\bUDLD\b", re.IGNORECASE), ["ethpm"], 2),
    # Port-channel / LACP
    (re.compile(r"\bport-channel|portchannel\b", re.IGNORECASE), ["lacp"], 1),
    (re.compile(r"\bLACP|lacp\b", re.IGNORECASE), ["lacp"], 2),
    (re.compile(r"\bbundle[d]?|suspended|not bundling\b", re.IGNORECASE), ["lacp"], 2),
    (re.compile(r"\betherchannel\b", re.IGNORECASE), ["lacp"], 2),
    # STP
    (re.compile(r"\bBPDU|bpdu\b", re.IGNORECASE), ["stp"], 2),
    (re.compile(r"\bspanning.?tree|STP\b", re.IGNORECASE), ["stp"], 2),
    (re.compile(r"\broot guard|bpdu guard|TCN\b", re.IGNORECASE), ["stp"], 2),
    (re.compile(r"\btopology change\b", re.IGNORECASE), ["stp"], 2),
    # vPC
    (re.compile(r"\bvPC|vpc\b", re.IGNORECASE), ["vpc"], 2),
    (re.compile(r"\bpeer.?link|peer.?keepalive\b", re.IGNORECASE), ["vpc"], 2),
    (re.compile(r"\borphan port\b", re.IGNORECASE), ["vpc"], 2),
    (
        re.compile(r"\bvpc consistency|type.?1 inconsistency\b", re.IGNORECASE),
        ["vpc"],
        2,
    ),
    # L2 forwarding
    (re.compile(r"\bMAC\s+(address|table|learn|aging)\b", re.IGNORECASE), ["l2fm"], 2),
    (
        re.compile(r"\bvlan\b.*\b(flood|forward|black.?hole)\b", re.IGNORECASE),
        ["l2fm"],
        2,
    ),
    # BGP
    (re.compile(r"\bBGP\b", re.IGNORECASE), ["bgp"], 1),
    (
        re.compile(r"\bneighbor.*reset|bgp.*down|bgp.*session\b", re.IGNORECASE),
        ["bgp"],
        2,
    ),
    (re.compile(r"\bhold.?timer|notification\s+sent\b", re.IGNORECASE), ["bgp"], 2),
    # OSPF
    (re.compile(r"\bOSPF\b", re.IGNORECASE), ["ospf"], 1),
    (
        re.compile(r"\badjacency.*down|dead interval|DR election\b", re.IGNORECASE),
        ["ospf"],
        2,
    ),
    # ISIS
    (re.compile(r"\bIS-IS|ISIS\b", re.IGNORECASE), ["isis"], 1),
    (re.compile(r"\bIIH|LSP\s+(flood|purge|expir)\b", re.IGNORECASE), ["isis"], 2),
    # EIGRP
    (re.compile(r"\bEIGRP\b", re.IGNORECASE), ["eigrp"], 1),
    (re.compile(r"\bEIGRP.*neighbor|stuck in active\b", re.IGNORECASE), ["eigrp"], 2),
    # HSRP / VRRP
    (re.compile(r"\bHSRP\b", re.IGNORECASE), ["hsrp"], 1),
    (re.compile(r"\bVRRP\b", re.IGNORECASE), ["vrrp"], 1),
    (re.compile(r"\bstandby.*active|standby.*preempt\b", re.IGNORECASE), ["hsrp"], 2),
    # PIM / Multicast
    (re.compile(r"\bPIM\b", re.IGNORECASE), ["pim"], 1),
    (re.compile(r"\bIGMP\b", re.IGNORECASE), ["pim"], 1),
    (
        re.compile(r"\bRP\s+(address|reachable|unreachable)\b", re.IGNORECASE),
        ["pim"],
        2,
    ),
    (re.compile(r"\bmulticast.*join|mroute|mrib\b", re.IGNORECASE), ["pim"], 2),
    # ARP/ND
    (re.compile(r"\bARP\b", re.IGNORECASE), ["arp"], 1),
    (
        re.compile(r"\bgratuitous ARP|arp.*conflict|duplicate IP\b", re.IGNORECASE),
        ["arp"],
        2,
    ),
    (
        re.compile(r"\bneighbor discovery|ND\s+(entry|resolve)\b", re.IGNORECASE),
        ["arp"],
        2,
    ),
    # Hardware / ASIC
    (re.compile(r"\bparity error|ECC|memory error\b", re.IGNORECASE), ["hardware"], 2),
    (re.compile(r"\bASIC|linecard reset|OIR\b", re.IGNORECASE), ["hardware"], 2),
    (re.compile(r"\bfabric drop|backplane\b", re.IGNORECASE), ["hardware"], 2),
    # Sysmgr / crash
    (
        re.compile(
            r"\bcore dump|process restarted|hap.?reset|killed.*signal\b", re.IGNORECASE
        ),
        ["sysmgr"],
        2,
    ),
    (re.compile(r"\bservice.*crashed|daemon.*restart\b", re.IGNORECASE), ["sysmgr"], 2),
]

_SYSLOG_RE = re.compile(r"%([A-Z0-9_]+)(?:-\d+)?-[A-Z0-9_]+:")


@dataclass
class EvidenceRef:
    chunk_id: int
    line_excerpt: str


@dataclass
class DaemonHint:
    name: str
    confidence: str  # "HIGH" | "MED" | "LOW"
    reasons: list[str]
    evidence: list[EvidenceRef]
    # Enriched from glossary
    display: str = ""
    what_it_does: str = ""
    common_symptoms: list[str] = field(default_factory=list)
    useful_commands: dict[str, list[str]] = field(default_factory=dict)


def infer_daemons(
    element: str,
    results,  # list[ChunkResult]
) -> list[DaemonHint]:
    """
    Analyse chunk results and infer which daemons are likely involved.
    Returns DaemonHint list sorted by confidence (HIGH first).
    """
    # score_map: daemon → {score, reasons, evidence}
    score_map: dict[str, dict] = {}

    def _ensure(name: str) -> dict:
        if name not in score_map:
            score_map[name] = {"score": 0, "reasons": [], "evidence": []}
        return score_map[name]

    for result in results:
        body = result.body_preview
        chunk_id = result.chunk_id

        # 1. Syslog tag scanning
        for m in _SYSLOG_RE.finditer(body):
            tag = m.group(1).upper()
            for partial, daemons in SYSLOG_TAG_MAP.items():
                if tag.startswith(partial):
                    for d in daemons:
                        entry = _ensure(d)
                        entry["score"] += 3  # syslog tags are high signal
                        reason = f"Syslog tag %{tag} matches"
                        if reason not in entry["reasons"]:
                            entry["reasons"].append(reason)
                        line = _find_line(body, m.group(0))
                        if line and len(entry["evidence"]) < 3:
                            entry["evidence"].append(
                                EvidenceRef(chunk_id=chunk_id, line_excerpt=line[:160])
                            )

        # 2. Keyword rules
        for pattern, daemons, weight in KEYWORD_RULES:
            matches = list(pattern.finditer(body))
            if not matches:
                continue
            for d in daemons:
                entry = _ensure(d)
                entry["score"] += weight * min(len(matches), 3)
                kw = matches[0].group(0)
                reason = f'Keyword "{kw}" matched ({len(matches)}×)'
                if reason not in entry["reasons"] and len(entry["reasons"]) < 5:
                    entry["reasons"].append(reason)
                if len(entry["evidence"]) < 3:
                    line = _find_line(body, kw)
                    if line:
                        entry["evidence"].append(
                            EvidenceRef(chunk_id=chunk_id, line_excerpt=line[:160])
                        )

    if not score_map:
        return []

    max_score = max(v["score"] for v in score_map.values()) or 1

    hints: list[DaemonHint] = []
    for name, data in score_map.items():
        if data["score"] == 0:
            continue
        ratio = data["score"] / max_score
        if ratio >= 0.66:
            confidence = "HIGH"
        elif ratio >= 0.33:
            confidence = "MED"
        else:
            confidence = "LOW"

        info = get_process_info(name)
        hint = DaemonHint(
            name=name,
            confidence=confidence,
            reasons=data["reasons"][:5],
            evidence=data["evidence"][:3],
            display=info.display if info else name.upper(),
            what_it_does=info.what_it_does if info else "",
            common_symptoms=info.common_symptoms if info else [],
            useful_commands=_substitute_commands(
                info.useful_commands if info else {}, element
            ),
        )
        hints.append(hint)

    # Sort: HIGH → MED → LOW, then by score
    order = {"HIGH": 0, "MED": 1, "LOW": 2}
    hints.sort(key=lambda h: (order[h.confidence], -score_map[h.name]["score"]))
    return hints


def _find_line(body: str, keyword: str) -> str:
    """Return the first line in body containing keyword."""
    for line in body.splitlines():
        if keyword.lower() in line.lower():
            return line.strip()
    return ""


def _substitute_commands(
    commands: dict[str, list[str]], element: str
) -> dict[str, list[str]]:
    """Replace {iface} / {vlan} / {ip} / {po_num} placeholders."""
    result = {}
    for platform, cmds in commands.items():
        result[platform] = [
            c.replace("{iface}", element)
            .replace("{vlan}", element)
            .replace("{ip}", element)
            .replace(
                "{po_num}",
                re.sub(
                    r"^(?:Port-channel|portchannel|Po)\s*",
                    "",
                    element,
                    flags=re.IGNORECASE,
                ).strip(),
            )
            for c in cmds
        ]
    return result
