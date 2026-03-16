"""Domain detection: filename heuristics + content header scanning."""
import re
from pathlib import Path

# Domain constants
LOGS = "LOGS"
INTERFACES = "INTERFACES"
HARDWARE = "HARDWARE"
ROUTING = "ROUTING"
CONFIG = "CONFIG"
PROCESS = "PROCESS"
UNKNOWN = "UNKNOWN"

ALL_DOMAINS = [LOGS, INTERFACES, HARDWARE, ROUTING, CONFIG, PROCESS, UNKNOWN]

# Filename keyword → domain (checked in order, first match wins)
FILENAME_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"log", re.IGNORECASE), LOGS),
    (re.compile(r"syslog", re.IGNORECASE), LOGS),
    (re.compile(r"event", re.IGNORECASE), LOGS),
    (re.compile(r"show_interface|show-interface|interface_stat", re.IGNORECASE), INTERFACES),
    (re.compile(r"counters?", re.IGNORECASE), INTERFACES),
    (re.compile(r"transceiver|sfp|optic|dom", re.IGNORECASE), HARDWARE),
    (re.compile(r"environment|temp|fan|power|psu|sensor", re.IGNORECASE), HARDWARE),
    (re.compile(r"module|linecard|inventory|hardware", re.IGNORECASE), HARDWARE),
    (re.compile(r"route|routing|bgp|ospf|eigrp|isis|rib|fib|adjacen", re.IGNORECASE), ROUTING),
    (re.compile(r"arp|nd\b|neighbor", re.IGNORECASE), ROUTING),
    (re.compile(r"running.?config|startup.?config|config", re.IGNORECASE), CONFIG),
    (re.compile(r"process|crash|core|traceback|stack|reset.reason", re.IGNORECASE), PROCESS),
]

# Content header patterns → domain (checked against title or first N lines)
# NOTE: ordered from most-specific to least-specific; title is checked first.
CONTENT_KEYWORDS: list[tuple[re.Pattern, str]] = [
    # Logs
    (re.compile(r"show\s+log|%[A-Z0-9_]+-\d+-[A-Z0-9_]+:|syslog", re.IGNORECASE), LOGS),
    # Interfaces — must come before hardware to avoid SFP-in-table false positives
    (re.compile(r"show\s+(ip\s+)?interface[s]?(?!\s+transceiver)(\s+status|\s+brief|\s+counters?|\s+trunk|\s+summary)?", re.IGNORECASE), INTERFACES),
    (re.compile(r"show\s+(port-channel|etherchannel|lacp|vpc|vlan|spanning|stp|cdp\s+neighbor|lldp)", re.IGNORECASE), INTERFACES),
    (re.compile(r"show\s+(mac\s+address|arp|ip\s+arp)\b", re.IGNORECASE), INTERFACES),
    # Hardware — only when show transceiver/dom keywords appear, not raw "SFP" in table body
    (re.compile(r"show\s+(interface\s+)?transceiver|show\s+sfp|dom\s+threshold", re.IGNORECASE), HARDWARE),
    (re.compile(r"show\s+(environment|module|inventory|power\s+supply|platform|diag|hardware)", re.IGNORECASE), HARDWARE),
    # Routing
    (re.compile(r"show\s+(ip\s+)?(route|bgp|ospf|eigrp|isis|rib|fib|cef|lisp)", re.IGNORECASE), ROUTING),
    (re.compile(r"show\s+(ip\s+)?(adjacency|prefix|community|as-path)", re.IGNORECASE), ROUTING),
    # Config
    (re.compile(r"show\s+(running|startup)-config|^interface\s+\S+", re.IGNORECASE), CONFIG),
    (re.compile(r"show\s+(policy-map|class-map|access-list|ip\s+access-list|prefix-list|route-map)", re.IGNORECASE), CONFIG),
    # Process
    (re.compile(r"show\s+(process|proc\s)|traceback|core\s+dump|reset\s+reason|crashed", re.IGNORECASE), PROCESS),
]

# Syslog line pattern (NX-OS / IOS)
_SYSLOG_RE = re.compile(r"%[A-Z0-9_]+-\d+-[A-Z0-9_]+:")


def detect_domain(filename: str, first_lines: list[str]) -> str:
    """
    Detect domain from filename + first N lines of content.
    Returns one of the domain constants.
    """
    # 1. Try filename
    name = Path(filename).name
    for pattern, domain in FILENAME_KEYWORDS:
        if pattern.search(name):
            return domain

    # 2. Try content scan
    sample = "\n".join(first_lines[:80])

    # Quick syslog check
    if _SYSLOG_RE.search(sample):
        return LOGS

    for pattern, domain in CONTENT_KEYWORDS:
        if pattern.search(sample):
            return domain

    return UNKNOWN


def domain_from_header(header: str) -> str:
    """Detect domain from a single command header line."""
    return detect_domain("", [header])
