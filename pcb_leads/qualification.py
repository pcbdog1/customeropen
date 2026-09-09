"""Company identity and hardware-prospect qualification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


GENERIC_COMPANY_NAMES = {
    "about",
    "contact",
    "contact us",
    "disclaimer",
    "home",
    "homepage",
    "imprint",
    "impressum",
    "legal",
    "legal notice",
    "legal and affiliate disclaimer",
    "privacy",
    "privacy policy",
    "terms",
    "terms and conditions",
}

LEGAL_SUFFIXES = "GmbH|AG|Ltd|Limited|Inc|LLC|BV|AB|Oy|SAS|SA"
LEGAL_NAME_RE = re.compile(
    rf"\b([A-Za-z0-9][A-Za-z0-9&.'()\-/ ]{{1,80}}\s+(?:{LEGAL_SUFFIXES}))\b",
    re.I,
)

HARDWARE_SIGNAL_TERMS = {
    "industrial": ("industrial",),
    "sensor": ("sensor", "sensing"),
    "controller": ("controller", "control board"),
    "robotics": ("robot", "robotics"),
    "embedded": ("embedded",),
    "electronics": ("electronic", "electronics"),
    "device": ("device",),
    "actuator": ("actuator",),
    "instrument": ("instrument",),
    "gateway": ("gateway",),
    "automation": ("automation",),
    "medical": ("medical",),
    "automotive": ("automotive",),
    "aerospace": ("aerospace",),
    "power": ("power electronics", "power system"),
    "battery": ("battery",),
    "motor": ("motor",),
    "camera": ("camera",),
    "measurement": ("measurement", "measuring"),
}
BROAD_CONTEXT_SIGNALS = frozenset({"industrial", "automation", "medical", "automotive", "aerospace"})

EXCLUSION_TERMS = {
    "marketing/SEO": (
        "marketing agency",
        "digital marketing",
        "seo agency",
        "seo services",
        "seo consultant",
        "advertising agency",
        "lead generation agency",
    ),
    "recruitment": (
        "recruitment agency",
        "staffing agency",
        "job board",
        "executive search firm",
        "talent agency",
    ),
    "directory operator": (
        "business directory",
        "company directory",
        "supplier directory",
        "manufacturer directory",
        "directory listing",
        "yellow pages",
    ),
    "government": (
        "government agency",
        "government department",
        "government ministry",
        "ministry of",
        "municipal government",
        "official government website",
    ),
    "education blog": (
        "education blog",
        "university blog",
        "college blog",
        "academic blog",
        "student blog",
    ),
    "unrelated ecommerce": (
        "online fashion store",
        "fashion ecommerce",
        "clothing store",
        "apparel store",
        "beauty store",
        "jewelry store",
        "home decor store",
        "dropshipping store",
    ),
    "PCB/EMS peer": (
        "pcb assembly",
        "printed circuit board assembly",
        "pcb manufacturer",
        "printed circuit board manufacturer",
        "pcba manufacturer",
        "smt assembly services",
        "ems",
        "contract electronics manufacturer",
    ),
}

CONDITIONAL_EXCLUSION_TERMS = {
    "software-only": (
        "saas",
        "software platform",
        "software development services",
        "software services",
        "cloud platform",
        "web application",
    ),
    "consulting-only": (
        "consulting services",
        "consultancy",
        "management consulting",
        "engineering consulting company",
        "automation consulting",
        "design consulting",
    ),
}

IDENTITY_NOUN_PATTERNS = {
    "software-only": (
        r"software\s+(?:company|vendor|developer|platform)",
        r"saas(?:\s+(?:company|provider|platform|business))?",
        r"cloud\s+(?:company|provider|platform|business)",
    ),
    "consulting-only": (
        r"consulting(?:\s+(?:company|firm|business))?",
        r"consultancy",
    ),
    "travel/tourism": (
        r"travel\s+(?:agency|publisher|site|website)",
        r"travel\s+magazine",
        r"travel\s+blog(?:\s+(?:tourism\s+)?affiliate(?:\s+guide)?)?",
        r"tourism\s+(?:operator|publisher|site|website)",
        r"(?:travel|tourism)\s+(?:tourism\s+)?affiliate\s+(?:site|website)",
    ),
    "news/media": (
        r"(?:[a-z0-9&'.-]+\s+){0,3}news\s+(?:publication|publisher|website|portal)",
        r"media\s+outlet",
        r"(?:online|digital|technology)\s+magazine(?:\s+operator)?",
        r"magazine\s+publisher",
        r"press\s+release\s+service",
    ),
}

IDENTITY_OPERATION_OBJECT_PATTERNS = {
    **IDENTITY_NOUN_PATTERNS,
    "software-only": (
        *IDENTITY_NOUN_PATTERNS["software-only"],
        r"software\s+development\s+services?",
    ),
    "consulting-only": (
        *IDENTITY_NOUN_PATTERNS["consulting-only"],
        r"consulting\s+services?",
    ),
}

_FIRST_PARTY_COPULA = (
    r"(?:we\s+are|we're|(?:our|this|the)\s+(?:company|business|site|publication)\s+is)"
)
_FIRST_PARTY_OPERATOR_SUBJECT = (
    r"(?:we|(?:our|this|the)\s+(?:company|business|site|publication))"
)
_NAMED_SUBJECT = r"[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,5}"
_OPERATOR_ACTION = r"(?:develops?|hosts?|offers?|operates?|provides?|publishes?|runs?|sells?)"
_DIRECT_IDENTITY_CONTINUATION = (
    r"(?=\s*(?:$|[.,;:]|\s+(?:covers?|covering|guides?|operates?|publishes?|publishing|"
    r"reports?|reporting|reviews?|reviewing|runs?|running|tourism\s+affiliate)))"
)


def _identity_term_group(terms: tuple[str, ...]) -> str:
    return "(?:" + "|".join(terms) + ")"


def _compile_identity_patterns(signal: str) -> tuple[re.Pattern[str], ...]:
    identity_nouns = _identity_term_group(IDENTITY_NOUN_PATTERNS[signal])
    operation_objects = _identity_term_group(IDENTITY_OPERATION_OBJECT_PATTERNS[signal])
    return (
        re.compile(
            rf"(?:^|[.!?;]\s*){_FIRST_PARTY_COPULA}\s+(?:an?\s+)?{identity_nouns}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){_NAMED_SUBJECT}\s+(?i:is)\s+(?i:(?:an?\s+)?{identity_nouns})\b"
        ),
        re.compile(
            rf"\b{_FIRST_PARTY_OPERATOR_SUBJECT}\s+{_OPERATOR_ACTION}\s+(?:an?\s+)?{operation_objects}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){_NAMED_SUBJECT}\s+(?i:{_OPERATOR_ACTION}\s+(?:an?\s+)?{operation_objects})\b"
        ),
        re.compile(
            rf"\b{_FIRST_PARTY_OPERATOR_SUBJECT}\s+{_OPERATOR_ACTION}\s+[^.!?;]{{1,160}}?\s+as\s+"
            rf"(?:an?\s+)?{operation_objects}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){_NAMED_SUBJECT}\s+{_OPERATOR_ACTION}\s+[^.!?;]{{1,160}}?\s+as\s+"
            rf"(?:an?\s+)?{operation_objects}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:^|[.!?;]\s*)(?:(?:this|an?)\s+)?{identity_nouns}\b{_DIRECT_IDENTITY_CONTINUATION}",
            re.IGNORECASE,
        ),
    )


CONTEXTUAL_EXCLUSION_PATTERNS = {
    signal: _compile_identity_patterns(signal) for signal in IDENTITY_NOUN_PATTERNS
}

_PHYSICAL_OBJECT_PATTERN = (
    r"printed circuit boards?|control boards?|embedded systems?|"
    r"sensors?|controllers?|robots?|actuators?|instruments?|gateways?|"
    r"devices?|equipment|electronics?|cameras?|batter(?:y|ies)|motors?|adapters?"
)
_PHYSICAL_OBJECT_RE = re.compile(rf"\b(?:{_PHYSICAL_OBJECT_PATTERN})\b", re.IGNORECASE)
_PHYSICAL_ACTION_RE = re.compile(
    r"\b(?:"
    r"manufactur(?:e|es|ed|ing)|manufacturers?|"
    r"design(?:s|ed|ing)?|"
    r"produc(?:e|es|ed|ing)|production|"
    r"sell(?:s|ing)?|sold|sales?"
    r")\b",
    re.IGNORECASE,
)
_NONPHYSICAL_LINK_WORDS = frozenset(
    {
        "analytics",
        "application",
        "applications",
        "app",
        "apps",
        "cloud",
        "consulting",
        "dashboard",
        "dashboards",
        "for",
        "platform",
        "saas",
        "service",
        "services",
        "software",
        "to",
        "with",
    }
)
_THIRD_PARTY_ACTION_WORDS = frozenset(
    {
        "allow",
        "allows",
        "client",
        "clients",
        "customer",
        "customers",
        "enable",
        "enables",
        "help",
        "helps",
        "partner",
        "partners",
        "serve",
        "serves",
        "serving",
        "support",
        "supports",
        "supplier",
        "suppliers",
        "vendor",
        "vendors",
    }
)


@dataclass(frozen=True)
class QualificationResult:
    eligible: bool
    hardware_signals: tuple[str, ...]
    exclusion_signals: tuple[str, ...]


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", value or "")).strip().casefold()


def is_generic_company_name(name: str) -> bool:
    return _normalize_name(name) in GENERIC_COMPANY_NAMES


def _legal_company_name(texts: list[str]) -> str:
    for text in texts:
        match = LEGAL_NAME_RE.search(text or "")
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,;:")
    return ""


def _hostname_company_name(website: str) -> str:
    host = (urlparse(website or "").hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) < 2:
        return ""
    multi_label_suffixes = {"co.uk", "com.au", "co.nz", "co.jp", "co.in"}
    suffix = ".".join(labels[-2:])
    stem = labels[-3] if len(labels) >= 3 and suffix in multi_label_suffixes else labels[-2]
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", stem) if part)


def resolve_company_name(
    title: str,
    website: str,
    page_texts: list[str],
    directory_name: str = "",
) -> str:
    """Resolve a company name from attributable page evidence, then domain fallback."""
    contextual_exclusions = _contextual_exclusion_signals(page_texts)
    if "travel/tourism" in contextual_exclusions:
        return ""

    legal_name = _legal_company_name(page_texts)
    if legal_name:
        return "" if "travel/tourism" in _contextual_exclusion_signals([], legal_name) else legal_name

    hostname_name = _hostname_company_name(website)
    if hostname_name:
        return "" if "travel/tourism" in _contextual_exclusion_signals([], hostname_name) else hostname_name
    if directory_name and not is_generic_company_name(directory_name):
        resolved = re.sub(r"\s+", " ", directory_name).strip()
        return "" if "travel/tourism" in _contextual_exclusion_signals([], resolved) else resolved
    return ""


def _is_query_like(text: str) -> bool:
    words = text.split()
    return 2 <= len(words) <= 6 and not re.search(r"[.!?,;:]", text)


def _contains_term(text: str, term: str) -> bool:
    words = [re.escape(word) for word in re.split(r"\s+", term.strip()) if word]
    if not words:
        return False
    return bool(re.search(rf"(?<!\w){'[^\\w]+'.join(words)}(?!\w)", text))


def _short_physical_link(value: str) -> bool:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return len(words) <= 5 and not (_NONPHYSICAL_LINK_WORDS & set(words))


def _contextual_exclusion_signals(texts: list[str], company_name: str = "") -> tuple[str, ...]:
    evidence = [text for text in texts if text]
    combined = " ".join(evidence)
    identity_evidence = [*evidence]
    if len(evidence) > 1:
        identity_evidence.append(combined)
    if company_name and combined:
        identity_evidence.append(f"{company_name} {combined}")

    company_identity = {
        signal
        for signal, terms in IDENTITY_NOUN_PATTERNS.items()
        if company_name and re.search(rf"\b{_identity_term_group(terms)}\b", company_name, re.IGNORECASE)
    }
    return tuple(
        signal
        for signal, patterns in CONTEXTUAL_EXCLUSION_PATTERNS.items()
        if signal in company_identity
        or any(pattern.search(text) for pattern in patterns for text in identity_evidence)
    )


def _has_first_party_action_prefix(clause: str, action_start: int) -> bool:
    prefix = clause[:action_start]
    match = re.search(
        r"\b(?:we|our(?:\s+(?:company|business|team))?)\b(?P<link>(?:\s+[a-z]+){0,5}\s*)$",
        prefix,
        re.IGNORECASE,
    )
    if not match:
        return False
    words = set(re.findall(r"[a-z]+", match.group("link").casefold()))
    return not (_THIRD_PARTY_ACTION_WORDS & words)


def _has_explicit_physical_product_context(text: str) -> bool:
    clauses = re.split(r"[.!?;]+", text)
    for clause in clauses:
        objects = list(_PHYSICAL_OBJECT_RE.finditer(clause))
        if not objects:
            continue

        for action in _PHYSICAL_ACTION_RE.finditer(clause):
            if not _has_first_party_action_prefix(clause, action.start()):
                continue
            for physical_object in objects:
                starts_after_action = physical_object.start() >= action.end()
                action_link = (
                    clause[action.end() : physical_object.start()]
                    if starts_after_action
                    else clause[physical_object.end() : action.start()]
                )
                if _short_physical_link(action_link):
                    return True
    return False


def hardware_qualification(texts: list[str], *, company_name: str = "") -> QualificationResult:
    all_text = " ".join(text.casefold() for text in texts if text)
    evidence = [text.casefold() for text in texts if text and not _is_query_like(text)]
    combined = " ".join(evidence)
    hardware_signals = tuple(
        signal for signal, terms in HARDWARE_SIGNAL_TERMS.items() if any(_contains_term(combined, term) for term in terms)
    )
    contextual_exclusions = _contextual_exclusion_signals(texts, company_name)
    exclusions = [
        signal for signal, terms in EXCLUSION_TERMS.items() if any(_contains_term(all_text, term) for term in terms)
    ]
    exclusions.extend(signal for signal in contextual_exclusions if signal not in exclusions)
    product_signals = tuple(signal for signal in hardware_signals if signal not in BROAD_CONTEXT_SIGNALS)
    has_physical_product_context = _has_explicit_physical_product_context(combined)
    if not has_physical_product_context:
        exclusions.extend(
            signal
            for signal, terms in CONDITIONAL_EXCLUSION_TERMS.items()
            if any(_contains_term(all_text, term) for term in terms) and signal not in exclusions
        )
    exclusion_signals = tuple(exclusions)
    eligible = len(hardware_signals) >= 2 and bool(product_signals) and not exclusion_signals
    return QualificationResult(eligible, hardware_signals, exclusion_signals)
