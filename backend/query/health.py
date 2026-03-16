"""R/Y/G threshold health detection for temperatures, PSU, fan, SFP DOM."""
import re
from dataclasses import dataclass


@dataclass
class HealthItem:
    label: str
    value: str
    unit: str
    status: str      # GREEN | YELLOW | RED
    heuristic: bool  # True = threshold derived from heuristic, not explicit data


# Temperature thresholds (Celsius, heuristic)
_TEMP_WARN = 60.0
_TEMP_CRIT = 75.0

# SFP DOM Rx power thresholds (dBm, heuristic)
_RX_WARN_LOW = -20.0
_RX_CRIT_LOW = -25.0

# SFP DOM Tx power thresholds (dBm, heuristic)
_TX_WARN_LOW = -8.0
_TX_CRIT_LOW = -12.0

# Patterns
_TEMP_RE = re.compile(
    r"([A-Za-z0-9/_\s\-]+?)\s+(\d+(?:\.\d+)?)\s*(?:C|°C|Celsius)",
    re.IGNORECASE,
)
_DOM_RX_RE = re.compile(
    r"Rx\s+(?:Power|pwr|Optical\s+Power)[^\d-]*([+-]?\d+(?:\.\d+)?)\s*(?:dBm)?",
    re.IGNORECASE,
)
_DOM_TX_RE = re.compile(
    r"Tx\s+(?:Power|pwr|Optical\s+Power)[^\d-]*([+-]?\d+(?:\.\d+)?)\s*(?:dBm)?",
    re.IGNORECASE,
)
_PSU_RE = re.compile(
    r"(?:Power\s*Supply|PSU|Power)[\s\d]*(?:Status|State|Oper)[:\s]+(\w+)",
    re.IGNORECASE,
)
_FAN_RE = re.compile(
    r"(?:Fan|FAN)[\w\s]*(?:Status|State|Oper)[:\s]+(\w+)",
    re.IGNORECASE,
)
_EXPLICIT_STATUS_RE = re.compile(
    r"(OK|Good|Normal|Warning|Critical|Failure|Fail|Down|Shutdown)",
    re.IGNORECASE,
)

_OK_WORDS = {"ok", "good", "normal", "up", "present", "online"}
_WARN_WORDS = {"warning", "warn", "degraded", "minor", "derating"}
_CRIT_WORDS = {"critical", "crit", "failure", "fail", "down", "shutdown", "absent", "offline"}


def _status_from_word(word: str) -> str:
    w = word.lower()
    if w in _CRIT_WORDS:
        return "RED"
    if w in _WARN_WORDS:
        return "YELLOW"
    if w in _OK_WORDS:
        return "GREEN"
    return "YELLOW"


def detect_health(text: str) -> list[HealthItem]:
    """Parse health metrics from a text block. Returns list of HealthItems."""
    items: list[HealthItem] = []

    # Temperature
    for m in _TEMP_RE.finditer(text):
        label = m.group(1).strip()[-40:]
        val = float(m.group(2))
        if val < 0 or val > 150:
            continue
        if val >= _TEMP_CRIT:
            status = "RED"
        elif val >= _TEMP_WARN:
            status = "YELLOW"
        else:
            status = "GREEN"
        items.append(HealthItem(
            label=f"Temp: {label}",
            value=str(val),
            unit="°C",
            status=status,
            heuristic=True,
        ))

    # SFP DOM Rx power
    for m in _DOM_RX_RE.finditer(text):
        val = float(m.group(1))
        if val <= _RX_CRIT_LOW:
            status = "RED"
        elif val <= _RX_WARN_LOW:
            status = "YELLOW"
        else:
            status = "GREEN"
        items.append(HealthItem(
            label="SFP Rx Power",
            value=str(val),
            unit="dBm",
            status=status,
            heuristic=True,
        ))

    # SFP DOM Tx power
    for m in _DOM_TX_RE.finditer(text):
        val = float(m.group(1))
        if val <= _TX_CRIT_LOW:
            status = "RED"
        elif val <= _TX_WARN_LOW:
            status = "YELLOW"
        else:
            status = "GREEN"
        items.append(HealthItem(
            label="SFP Tx Power",
            value=str(val),
            unit="dBm",
            status=status,
            heuristic=True,
        ))

    # PSU status
    for m in _PSU_RE.finditer(text):
        word = m.group(1)
        status = _status_from_word(word)
        items.append(HealthItem(
            label="PSU",
            value=word,
            unit="",
            status=status,
            heuristic=False,
        ))

    # Fan status
    for m in _FAN_RE.finditer(text):
        word = m.group(1)
        status = _status_from_word(word)
        items.append(HealthItem(
            label="Fan",
            value=word,
            unit="",
            status=status,
            heuristic=False,
        ))

    # Deduplicate by label
    seen: set[str] = set()
    unique: list[HealthItem] = []
    for item in items:
        key = f"{item.label}:{item.value}"
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[:20]
