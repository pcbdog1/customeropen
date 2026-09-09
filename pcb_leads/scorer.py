from __future__ import annotations

import re

from .keyword_library import all_products, industry_names, load_keyword_library

HARDWARE_TERMS = {
    "hardware",
    "electronics",
    "embedded",
    "firmware",
    "sensor",
    "controller",
    "device",
    "robot",
    "robotics",
    "pcb",
    "pcba",
    "smt",
    "board",
    "medical device",
    "industrial control",
    "machine vision",
}
HARDWARE_TERMS.update(product.casefold() for product in all_products())

DIRECT_SIGNAL_TERMS = {
    "hardware engineer",
    "embedded engineer",
    "electronics engineer",
    "firmware engineer",
    "pcb design",
    "pcba",
    "smt assembly",
    "procurement",
    "supplier",
    "tender",
    "contract manufacturing",
}
_KEYWORD_LIBRARY = load_keyword_library()
DIRECT_SIGNAL_TERMS.update(title.casefold() for title in _KEYWORD_LIBRARY.job_titles)
DIRECT_SIGNAL_TERMS.update(
    term.casefold()
    for term in _KEYWORD_LIBRARY.procurement_terms
    if any(
        signal in term.casefold()
        for signal in ("procurement", "sourcing", "rfq", "pcb assembly", "pcba", "contract manufacturing", "npi")
    )
)

TARGET_INDUSTRIES = {
    "robotics",
    "automation",
    "industrial automation",
    "medical device",
    "industrial electronics",
    "iot",
    "ai hardware",
    "embedded",
    "sensor",
    "instrumentation",
}
TARGET_INDUSTRIES.update(industry.casefold() for industry in industry_names())

PEER_MANUFACTURER_TERMS = {
    "pcb manufacturer",
    "printed circuit board manufacturer",
    "pcba manufacturer",
    "smt assembly services",
    "contract electronics manufacturer",
}


def _contains_any(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def is_likely_peer_manufacturer(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in PEER_MANUFACTURER_TERMS)


def score_candidate(text: str, industry: str, has_contact: bool) -> tuple[int, str]:
    lowered_industry = industry.lower()
    has_direct_signal = _contains_any(text, DIRECT_SIGNAL_TERMS)
    has_hardware = _contains_any(text, HARDWARE_TERMS)
    in_target = any(term in lowered_industry for term in TARGET_INDUSTRIES) or _contains_any(text, TARGET_INDUSTRIES)

    if has_hardware and has_direct_signal:
        return 5, "clear hardware product plus direct signal such as hiring, procurement, PCB/PCBA, or engineering need"
    if has_hardware:
        return 4, "clear hardware product evidence but no direct procurement or hiring signal found"
    if in_target:
        return 3, "target industry match with weaker public hardware evidence"
    if has_contact:
        return 2, "public business contact exists but PCB/PCBA fit is weak"
    return 1, "not enough evidence for PCB/PCBA prospecting"


def infer_industry(text: str, fallback: str) -> str:
    lowered = text.lower()
    if "robot" in lowered:
        return "Robotics"
    if "medical device" in lowered or "healthcare" in lowered:
        return "Medical Device"
    if "sensor" in lowered:
        return "Sensor / Instrumentation"
    if "automation" in lowered or "industrial control" in lowered:
        return "Industrial Automation"
    if "iot" in lowered or "embedded" in lowered:
        return "IoT / Embedded System"
    return fallback


def demand_signal_from_text(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(hardware|embedded|electronics|firmware) engineer\b", lowered):
        return "招聘硬件/嵌入式/电子/固件工程师"
    if any(term in lowered for term in ("procurement", "supplier", "tender", "rfq")):
        return "采购/招标/供应商需求"
    if any(term in lowered for term in ("pcb", "pcba", "smt", "control board")):
        return "公开页面包含 PCB/PCBA/控制板信号"
    return "官网产品包含电子硬件"
