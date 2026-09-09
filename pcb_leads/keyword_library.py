from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_PATH = PROJECT_ROOT / "keywords" / "hardware_keyword_library.json"
DEFAULT_LEARNED_PATH = PROJECT_ROOT / "keywords" / "learned_keywords.json"
DEFAULT_GROWTH_STATE_PATH = PROJECT_ROOT / "keywords" / "keyword_growth_state.json"

_PRODUCT_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9+/-]*\s+){1,4}"
    r"(?:equipment|systems?|devices?|controllers?|instruments?|cameras?|sensors?|"
    r"terminals?|gateways?|servers?|scanners?|monitors?|robots?|electronics|hardware)\b",
    re.IGNORECASE,
)
_JOB_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9+/-]*\s+){0,3}"
    r"(?:Engineer|Manager|Director|Buyer|CTO)\b",
)
_PROCUREMENT_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9+/-]*\s+){0,3}"
    r"(?:Manufacturing|Assembly|Prototype|Sourcing|Supplier|Build)\b",
    re.IGNORECASE,
)
_REJECTED_LEARNED_TERMS = {
    "software",
    "software system",
    "pcb manufacturer",
    "pcba manufacturer",
    "contract electronics manufacturer",
}


@dataclass(frozen=True)
class KeywordLibrary:
    industries: dict[str, tuple[str, ...]]
    job_titles: tuple[str, ...]
    procurement_terms: tuple[str, ...]
    templates: tuple[str, ...]


DOMAIN_ENRICHMENT_INTENTS = (
    "@domain",
    "email",
    "contact",
    "sales",
    "info",
    "Impressum",
    "Kontakt",
    "PDF",
)

PRODUCT_DRIVEN_KEYWORDS = (
    "battery management system manufacturer",
    "motor controller manufacturer",
    "servo drive manufacturer",
    "industrial controller manufacturer",
    "PLC manufacturer",
    "embedded controller manufacturer",
    "sensor module manufacturer",
    "industrial camera manufacturer",
    "machine vision camera manufacturer",
    "data acquisition system manufacturer",
    "test measurement equipment manufacturer",
    "laboratory instrument manufacturer",
    "medical device electronics manufacturer",
    "ultrasound device manufacturer",
    "patient monitor manufacturer",
    "IVD analyzer manufacturer",
    "wearable medical device manufacturer",
    "EV charger manufacturer",
    "solar inverter manufacturer",
    "energy storage controller manufacturer",
    "power electronics manufacturer",
    "RF module manufacturer",
    "microwave equipment manufacturer",
    "GNSS receiver manufacturer",
    "IoT gateway manufacturer",
    "edge AI device manufacturer",
    "industrial PC manufacturer",
    "FPGA board manufacturer",
    "AI camera manufacturer",
    "lidar manufacturer",
    "radar sensor manufacturer",
    "access control manufacturer",
    "security electronics manufacturer",
    "environmental monitoring device manufacturer",
    "water quality sensor manufacturer",
    "industrial printer manufacturer",
    "RFID reader manufacturer",
    "barcode scanner manufacturer",
    "payment terminal manufacturer",
    "vending machine electronics manufacturer",
    "audio equipment manufacturer",
    "professional audio manufacturer",
    "LED display controller manufacturer",
    "agricultural electronics manufacturer",
    "marine electronics manufacturer",
    "railway signaling equipment manufacturer",
)


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default.copy()
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append_unique(values: list[str], additions: Iterable[str]) -> None:
    known = {value.casefold() for value in values}
    for value in additions:
        normalized = value.casefold()
        if normalized in known:
            continue
        values.append(value)
        known.add(normalized)


def load_keyword_library(
    path: Path = DEFAULT_LIBRARY_PATH,
    learned_path: Path = DEFAULT_LEARNED_PATH,
) -> KeywordLibrary:
    data = _read_json(path, {})
    industries = {
        str(industry): [str(product) for product in products]
        for industry, products in data.get("industries", {}).items()
    }
    learned = _read_json(learned_path, {})
    for industry, products in learned.get("industries", {}).items():
        target = industries.setdefault(str(industry), [])
        _append_unique(target, (str(product) for product in products))
    for evidence in learned.get("evidence", []):
        if evidence.get("term_type") != "products":
            continue
        industry = str(evidence.get("industry") or "").strip()
        term = str(evidence.get("term") or "").strip()
        if industry and term:
            _append_unique(industries.setdefault(industry, []), [term])
    job_titles = [str(value) for value in data.get("job_titles", [])]
    procurement_terms = [str(value) for value in data.get("procurement_terms", [])]
    _append_unique(job_titles, (str(value) for value in learned.get("job_titles", [])))
    _append_unique(procurement_terms, (str(value) for value in learned.get("procurement_terms", [])))
    policy = data.get("combination_policy", {})
    return KeywordLibrary(
        industries={industry: tuple(products) for industry, products in industries.items()},
        job_titles=tuple(job_titles),
        procurement_terms=tuple(procurement_terms),
        templates=tuple(str(value) for value in policy.get("templates", [])),
    )


def industry_names(path: Path = DEFAULT_LIBRARY_PATH) -> tuple[str, ...]:
    return tuple(load_keyword_library(path).industries)


def all_products(path: Path = DEFAULT_LIBRARY_PATH) -> tuple[str, ...]:
    library = load_keyword_library(path)
    return tuple(product for products in library.industries.values() for product in products)


def products_for(industry: str, path: Path = DEFAULT_LIBRARY_PATH) -> tuple[str, ...]:
    library = load_keyword_library(path)
    products = library.industries.get(industry)
    if products:
        return products
    normalized = industry.casefold()
    for category_products in library.industries.values():
        for index, product in enumerate(category_products):
            if product.casefold() == normalized:
                return category_products[index:] + category_products[:index]
    return (industry,)


def primary_product(
    industry: str,
    rotation: int = 0,
    path: Path = DEFAULT_LIBRARY_PATH,
    learned_path: Path = DEFAULT_LEARNED_PATH,
) -> str:
    del learned_path
    data = _read_json(path, {})
    industries = {
        str(name): tuple(str(product) for product in products)
        for name, products in data.get("industries", {}).items()
    }
    products = industries.get(industry)
    if not products:
        normalized = industry.casefold()
        for category_products in industries.values():
            for index, product in enumerate(category_products):
                if product.casefold() == normalized:
                    products = category_products[index:] + category_products[:index]
                    break
            if products:
                break
    if not products:
        return industry
    return products[rotation % len(products)]


def build_query_batch(
    country: str,
    industry: str,
    max_queries: int = 10,
    rotation: int = 0,
    library_path: Path = DEFAULT_LIBRARY_PATH,
) -> list[str]:
    library = load_keyword_library(library_path)
    products = products_for(industry, library_path)
    jobs = library.job_titles or ("Hardware Engineer",)
    procurement = library.procurement_terms or ("PCB Assembly",)
    limit = max(0, min(int(max_queries), 6))
    primary = products[rotation % len(products)]
    job = jobs[rotation % len(jobs)]
    rotating_procurement = procurement[rotation % len(procurement)]
    candidates = [
        f'{country} "{industry}" company manufacturer contact',
        f'{country} "{primary}" manufacturer',
        f'{country} "{job}" electronics',
        f'{country} (PCB OR PCBA OR "{rotating_procurement}") RFQ OR tender OR sourcing',
        f'{country} electronics ("info@" OR "sales@" OR "contact@")',
        f'{country} hardware manufacturer embedded electronics contact',
    ]
    return list(dict.fromkeys(candidates))[:limit]


def email_first_queries(
    country: str,
    industry: str,
    rotation: int = 0,
    max_queries: int = 20,
) -> list[str]:
    compatibility_intents = ("contact", "email", "sales@", "info@", "Impressum", "Kontakt")
    queries = [f'{country} "{industry}" "{intent}"' for intent in compatibility_intents]
    product_count = max(0, int(max_queries) - len(queries))
    for offset in range(product_count):
        product = PRODUCT_DRIVEN_KEYWORDS[(rotation + offset) % len(PRODUCT_DRIVEN_KEYWORDS)]
        queries.append(f'{country} "{product}" "contact"')
    return list(dict.fromkeys(queries))[: max(0, int(max_queries))]


def domain_enrichment_queries(domain: str, max_queries: int | None = None) -> list[str]:
    normalized = domain.strip().casefold().removeprefix("www.")
    if not normalized:
        return []
    queries = []
    for intent in DOMAIN_ENRICHMENT_INTENTS:
        if intent == "@domain":
            query = f'"@{normalized}"'
        elif intent == "PDF":
            query = f'site:{normalized} filetype:pdf email OR contact'
        else:
            query = f'site:{normalized} "{intent}"'
        queries.append(query)
    limit = len(queries) if max_queries is None else max(0, int(max_queries))
    return queries[:limit]


def _default_growth_state() -> dict:
    return {
        "version": "1.0.0",
        "consecutive_zero_result_runs": 0,
        "expansion_level": 0,
        "last_expansion_at": "",
        "last_industry": "",
        "last_product": "",
        "used_combinations": [],
        "expansion_history": [],
    }


def load_growth_state(state_path: Path = DEFAULT_GROWTH_STATE_PATH) -> dict:
    state = _default_growth_state()
    state.update(_read_json(state_path, {}))
    return state


def record_search_outcome(
    found_count: int,
    *,
    industry: str = "",
    product: str = "",
    state_path: Path = DEFAULT_GROWTH_STATE_PATH,
) -> dict:
    state = load_growth_state(state_path)
    state["last_industry"] = industry
    state["last_product"] = product
    if found_count > 0:
        state["consecutive_zero_result_runs"] = 0
    else:
        state["consecutive_zero_result_runs"] = int(state.get("consecutive_zero_result_runs", 0)) + 1

    if state["consecutive_zero_result_runs"] >= 3:
        state["consecutive_zero_result_runs"] = 0
        state["expansion_level"] = int(state.get("expansion_level", 0)) + 1
        timestamp = datetime.now().isoformat(timespec="seconds")
        state["last_expansion_at"] = timestamp
        state.setdefault("expansion_history", []).append(
            {
                "expanded_at": timestamp,
                "expansion_level": state["expansion_level"],
                "industry": industry,
                "product": product,
                "reason": "three consecutive zero-result searches",
            }
        )
    _write_json(state_path, state)
    return state


def _normalize_term(value: str) -> str:
    return " ".join(value.strip(" .,:;-\t\r\n").split())


def _unique_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        value = _normalize_term(match.group(0))
        normalized = value.casefold()
        if not value or normalized in seen or normalized in _REJECTED_LEARNED_TERMS:
            continue
        if "@" in value or len(value.split()) > 6:
            continue
        seen.add(normalized)
        found.append(value)
    return found


def _known_terms(library: KeywordLibrary, learned: dict) -> set[str]:
    values: Iterable[str] = (
        product
        for products in library.industries.values()
        for product in products
    )
    known = {value.casefold() for value in values}
    known.update(value.casefold() for value in library.job_titles)
    known.update(value.casefold() for value in library.procurement_terms)
    known.update(value.casefold() for value in learned.get("products", []))
    known.update(value.casefold() for value in learned.get("job_titles", []))
    known.update(value.casefold() for value in learned.get("procurement_terms", []))
    return known


def learn_public_terms(
    text: str,
    *,
    industry: str,
    source_url: str,
    learned_path: Path = DEFAULT_LEARNED_PATH,
    library_path: Path = DEFAULT_LIBRARY_PATH,
) -> list[str]:
    if not source_url.startswith(("http://", "https://")):
        return []
    learned = _read_json(
        learned_path,
        {
            "version": "1.0.0",
            "updated_at": "",
            "industries": {},
            "products": [],
            "job_titles": [],
            "procurement_terms": [],
            "evidence": [],
        },
    )
    library = load_keyword_library(library_path)
    known = _known_terms(library, learned)
    candidates = [
        ("products", value) for value in _unique_matches(_PRODUCT_PATTERN, text)
    ]
    candidates.extend(("job_titles", value) for value in _unique_matches(_JOB_PATTERN, text))
    candidates.extend(
        ("procurement_terms", value)
        for value in _unique_matches(_PROCUREMENT_PATTERN, text)
    )

    added: list[str] = []
    for term_type, value in candidates:
        normalized = value.casefold()
        if normalized in known:
            continue
        learned.setdefault(term_type, []).append(value)
        learned.setdefault("evidence", []).append(
            {
                "term": value,
                "term_type": term_type,
                "industry": industry,
                "source_url": source_url,
                "discovered_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        known.add(normalized)
        added.append(value)
        if len(added) >= 6:
            break

    if added:
        learned["updated_at"] = datetime.now().date().isoformat()
        if industry not in library.industries:
            learned.setdefault("industries", {}).setdefault(industry, [])
        _write_json(learned_path, learned)
    return added
