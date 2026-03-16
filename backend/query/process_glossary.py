"""Process / daemon glossary: human-readable descriptions + next commands."""
from dataclasses import dataclass, field


@dataclass
class ProcessInfo:
    short: str                           # internal key, e.g. "lacp"
    display: str                         # e.g. "LACP"
    what_it_does: str                    # one sentence
    common_symptoms: list[str]
    useful_commands: dict[str, list[str]]  # platform → command list


PROCESS_GLOSSARY: dict[str, ProcessInfo] = {
    "ethpm": ProcessInfo(
        short="ethpm",
        display="ethpm",
        what_it_does="Ethernet/port manager — owns link state machines, bring-up/bring-down sequences, speed/duplex negotiation, and optics events.",
        common_symptoms=[
            "Link flapping (up/down cycles)",
            "Port stuck in down/down or err-disabled state",
            "Speed or duplex mismatch warnings",
            "SFP/optics insertion/removal events",
            "CRC / input error spikes",
        ],
        useful_commands={
            "NX-OS": [
                "show interface {iface}",
                "show interface {iface} counters",
                "show interface {iface} transceiver details",
                "show logging | grep {iface}",
                "debug ethpm all (use carefully)",
            ],
            "IOS/XE": [
                "show interface {iface}",
                "show interface {iface} counters",
                "show logging | include {iface}",
            ],
        },
    ),

    "lacp": ProcessInfo(
        short="lacp",
        display="LACP",
        what_it_does="Link Aggregation Control Protocol process — negotiates LACP PDUs and keeps port-channel membership synchronized with the remote peer.",
        common_symptoms=[
            "Port-channel member suspended or not bundling",
            "LACP PDU timeout / mismatch",
            "Port-channel down while physical links are up",
            "Inconsistent LACP system-id or key mismatch",
        ],
        useful_commands={
            "NX-OS": [
                "show lacp interface {iface}",
                "show port-channel summary",
                "show lacp counters",
                "show lacp neighbor",
            ],
            "IOS/XE": [
                "show etherchannel summary",
                "show lacp neighbor detail",
                "show lacp {po_num} internal",
            ],
        },
    ),

    "l2fm": ProcessInfo(
        short="l2fm",
        display="l2fm",
        what_it_does="Layer-2 forwarding manager — programs MAC address tables, manages VLAN flooding domains, and syncs L2 state to the hardware forwarding plane.",
        common_symptoms=[
            "MAC address not learning or disappearing",
            "Unexpected flooding / traffic blackholing",
            "VLAN forwarding broken on specific port",
            "L2 inconsistency across vPC peers",
        ],
        useful_commands={
            "NX-OS": [
                "show mac address-table interface {iface}",
                "show mac address-table vlan {vlan}",
                "show vlan id {vlan}",
                "show l2route mac all",
            ],
            "IOS/XE": [
                "show mac address-table interface {iface}",
                "show vlan id {vlan}",
            ],
        },
    ),

    "stp": ProcessInfo(
        short="stp",
        display="STP",
        what_it_does="Spanning Tree Protocol process — computes port roles and states (Root/Designated/Blocking) and reacts to topology change notifications (TCN).",
        common_symptoms=[
            "Port unexpectedly blocking or inconsistent state",
            "Topology Change Notification (TCN) storms",
            "BPDU guard / root guard triggered",
            "STP reconvergence causing brief outage",
        ],
        useful_commands={
            "NX-OS": [
                "show spanning-tree interface {iface}",
                "show spanning-tree detail",
                "show spanning-tree vlan {vlan} detail",
            ],
            "IOS/XE": [
                "show spanning-tree interface {iface} detail",
                "show spanning-tree vlan {vlan}",
                "show spanning-tree detail",
            ],
        },
    ),

    "vpc": ProcessInfo(
        short="vpc",
        display="vPC",
        what_it_does="Virtual Port-Channel control process (NX-OS) — manages the peer-link, runs consistency checks, and controls vPC member port state and orphan handling.",
        common_symptoms=[
            "vPC consistency check failure",
            "Orphan port activation / deactivation",
            "Peer-link down causing vPC suspension",
            "Split-brain scenario after peer-keepalive loss",
            "Type-1 inconsistency (configuration mismatch)",
        ],
        useful_commands={
            "NX-OS": [
                "show vpc",
                "show vpc consistency-parameters global",
                "show vpc consistency-parameters interface port-channel {num}",
                "show vpc role",
                "show vpc peer-keepalive",
            ],
        },
    ),

    "bgp": ProcessInfo(
        short="bgp",
        display="BGP",
        what_it_does="Border Gateway Protocol routing process — manages neighbor sessions, sends/receives UPDATE messages, applies policy, and installs routes into the RIB.",
        common_symptoms=[
            "Neighbor session reset or flapping",
            "Missing or withdrawn prefixes",
            "Route policy (route-map) misconfiguration",
            "BGP hold-timer expiry",
            "Memory or CPU pressure causing slow updates",
        ],
        useful_commands={
            "NX-OS": [
                "show bgp summary",
                "show bgp neighbors {ip}",
                "show bgp neighbors {ip} advertised-routes",
                "show bgp neighbors {ip} received-routes",
                "show bgp process",
            ],
            "IOS/XE": [
                "show bgp summary",
                "show bgp neighbors {ip}",
                "show ip bgp {ip} longer-prefixes",
            ],
            "ASA": [
                "show bgp summary",
                "show bgp neighbors",
            ],
        },
    ),

    "ospf": ProcessInfo(
        short="ospf",
        display="OSPF",
        what_it_does="OSPF routing process — maintains adjacencies, floods LSAs, runs SPF, and installs routes via the RIB.",
        common_symptoms=[
            "Adjacency stuck in EXSTART/EXCHANGE or flapping",
            "LSDB mismatch between neighbors",
            "Route not installed despite adjacency being up",
            "Dead-interval mismatch or MTU mismatch",
        ],
        useful_commands={
            "NX-OS": [
                "show ip ospf neighbor",
                "show ip ospf interface {iface}",
                "show ip ospf database",
                "show ip route ospf",
            ],
            "IOS/XE": [
                "show ip ospf neighbor",
                "show ip ospf interface {iface}",
                "show ip ospf database",
            ],
        },
    ),

    "isis": ProcessInfo(
        short="isis",
        display="IS-IS",
        what_it_does="IS-IS routing process — maintains adjacencies via IIH hellos, floods LSPs, runs SPF, and installs routes into the RIB.",
        common_symptoms=[
            "Adjacency drops or flapping",
            "LSP flooding storms",
            "Route instability or missing prefixes",
            "Authentication mismatch",
        ],
        useful_commands={
            "NX-OS": [
                "show isis adjacency",
                "show isis database detail",
                "show isis interface {iface}",
            ],
            "IOS/XE": [
                "show isis neighbors",
                "show isis database",
            ],
        },
    ),

    "sysmgr": ProcessInfo(
        short="sysmgr",
        display="sysmgr",
        what_it_does="NX-OS System Manager — supervises all platform daemons, handles process restarts, crash collection, and service lifecycle (start/stop/hap-reset).",
        common_symptoms=[
            "Daemon crash / core dump generated",
            "Service restarted by sysmgr",
            "Multiple processes bouncing in sequence",
            "HAP reset (high-availability process reset)",
            "\"last reset reason\" shows software fault",
        ],
        useful_commands={
            "NX-OS": [
                "show system internal sysmgr service name {daemon}",
                "show processes",
                "show cores",
                "show logging | include SYSMGR",
            ],
        },
    ),

    "hardware": ProcessInfo(
        short="hardware",
        display="Hardware/ASIC",
        what_it_does="Forwarding ASIC or linecard hardware — handles packet forwarding, counter collection, and reports anomalies (parity errors, fabric drops, etc.).",
        common_symptoms=[
            "Parity / ECC memory errors",
            "Fabric / crossbar drops",
            "Hardware CRC or FCS errors",
            "Linecard reset or OIR event",
        ],
        useful_commands={
            "NX-OS": [
                "show hardware internal errors",
                "show module",
                "show hardware capacity",
                "show interface {iface} counters errors",
            ],
            "IOS/XE": [
                "show platform resources",
                "show interfaces {iface} counters errors",
            ],
        },
    ),

    "arp": ProcessInfo(
        short="arp",
        display="ARP/ND",
        what_it_does="ARP/Neighbor Discovery process — resolves L3 addresses to MAC addresses and maintains the adjacency table used by CEF/FIB.",
        common_symptoms=[
            "ARP resolution failing or timing out",
            "Stale ARP entries causing drops",
            "Duplicate IP / gratuitous ARP storm",
            "IPv6 ND not resolving",
        ],
        useful_commands={
            "NX-OS": [
                "show ip arp {ip}",
                "show ip arp vrf {vrf}",
                "show ipv6 neighbor",
                "show ip arp statistics",
            ],
            "IOS/XE": [
                "show arp",
                "show ip arp {ip}",
                "show ipv6 neighbors",
            ],
        },
    ),

    "eigrp": ProcessInfo(
        short="eigrp",
        display="EIGRP",
        what_it_does="EIGRP routing process — maintains neighbor adjacencies via hellos, exchanges topology tables using DUAL algorithm, and installs routes into the RIB.",
        common_symptoms=[
            "Neighbor adjacency dropped or not forming",
            "Stuck-in-Active (SIA) route causing reset",
            "Route flapping or missing prefixes",
            "K-value mismatch between peers",
        ],
        useful_commands={
            "IOS/XE": [
                "show ip eigrp neighbors",
                "show ip eigrp topology",
                "show ip eigrp interfaces {iface}",
                "show ip route eigrp",
            ],
            "NX-OS": [
                "show ip eigrp neighbors",
                "show ip eigrp topology",
                "show ip eigrp interfaces {iface}",
            ],
        },
    ),

    "hsrp": ProcessInfo(
        short="hsrp",
        display="HSRP",
        what_it_does="Hot Standby Router Protocol — provides first-hop redundancy by electing an Active and Standby router sharing a virtual IP/MAC.",
        common_symptoms=[
            "HSRP state flapping (Active ↔ Speak ↔ Standby)",
            "Dual-Active condition (split-brain)",
            "Virtual IP not reachable from hosts",
            "Preemption causing unnecessary transitions",
        ],
        useful_commands={
            "IOS/XE": [
                "show standby brief",
                "show standby {iface}",
            ],
            "NX-OS": [
                "show hsrp brief",
                "show hsrp {iface}",
            ],
        },
    ),

    "vrrp": ProcessInfo(
        short="vrrp",
        display="VRRP",
        what_it_does="Virtual Router Redundancy Protocol — provides first-hop redundancy; Master router owns the virtual IP and responds to ARP for it.",
        common_symptoms=[
            "Master election flapping",
            "Virtual IP unreachable",
            "Timer mismatch between peers",
        ],
        useful_commands={
            "IOS/XE": [
                "show vrrp brief",
                "show vrrp {iface}",
            ],
            "NX-OS": [
                "show vrrp brief",
                "show vrrp {iface}",
            ],
        },
    ),

    "pim": ProcessInfo(
        short="pim",
        display="PIM / Multicast",
        what_it_does="Protocol Independent Multicast — builds multicast distribution trees (PIM-SM uses shared/source trees via RP; PIM-DM uses flood-and-prune).",
        common_symptoms=[
            "RP not reachable or misconfigured",
            "Multicast group not joined (IGMP issue)",
            "mroute not installed in MRIB/FIB",
            "PIM neighbor not forming on interface",
        ],
        useful_commands={
            "NX-OS": [
                "show ip pim neighbor",
                "show ip mroute {group}",
                "show ip igmp groups",
                "show ip pim interface {iface}",
            ],
            "IOS/XE": [
                "show ip pim neighbor",
                "show ip mroute",
                "show ip igmp groups",
                "show ip pim interface {iface}",
            ],
        },
    ),
}


def get_process_info(name: str) -> ProcessInfo | None:
    return PROCESS_GLOSSARY.get(name.lower())


def glossary_as_dict() -> dict:
    """Serialize for JSON API response."""
    out = {}
    for key, info in PROCESS_GLOSSARY.items():
        out[key] = {
            "short": info.short,
            "display": info.display,
            "what_it_does": info.what_it_does,
            "common_symptoms": info.common_symptoms,
            "useful_commands": info.useful_commands,
        }
    return out
