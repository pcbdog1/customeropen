from __future__ import annotations

import re
from urllib.parse import urlparse

import tldextract


UNKNOWN = "未公开/待核实"
NOT_FOUND = "未找到"

_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_DOT_ATOM_LOCAL_RE = re.compile(
    r"[A-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Z0-9!#$%&'*+/=?^_`{|}~-]+)*",
    re.IGNORECASE | re.ASCII,
)
_TLD_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
)


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_company_name(value: str | None) -> str:
    value = normalize_space(value).lower()
    value = re.sub(r"\b(gmbh|ltd|limited|inc|corp|corporation|llc|bv|b\.v\.|ab|oy|sas|s\.a\.|ag)\b\.?", lambda m: m.group(0), value)
    return value


def normalize_domain(url_or_domain: str | None) -> str:
    return registrable_domain(url_or_domain)


def _normalize_valid_host(
    url_or_domain: str | None,
    *,
    strip_edge_dots: bool,
    strip_www: bool,
) -> str:
    value = normalize_space(url_or_domain).lower()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        host = urlparse(value).hostname or ""
    except ValueError:
        return ""
    if strip_edge_dots:
        host = host.strip(".")
    if strip_www and host.startswith("www."):
        host = host[4:]
    if not host or len(host) > 253 or ".." in host:
        return ""
    try:
        ascii_host = host.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""
    labels = ascii_host.split(".")
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        return ""
    if labels[-1].isdigit():
        return ""
    return ascii_host


def normalize_host(url_or_domain: str | None) -> str:
    return _normalize_valid_host(url_or_domain, strip_edge_dots=True, strip_www=True)


def _normalize_attribution_host(url_or_domain: str | None) -> str:
    return _normalize_valid_host(url_or_domain, strip_edge_dots=False, strip_www=False)


def registrable_domain(url_or_domain: str | None) -> str:
    """Return the company-controlled domain using bundled PSL data only."""
    host = normalize_host(url_or_domain)
    if not host:
        return ""
    extracted = _TLD_EXTRACT(host)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"
    if extracted.suffix:
        return ""

    # Reserved and internal test TLDs are absent from the PSL but remain
    # attributable when they have a conventional domain + suffix shape.
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else ""


def normalize_mailbox(email: str | None) -> str:
    raw_value = str(email or "")
    if not raw_value.isascii():
        return ""
    value = raw_value.strip()
    if value.count("@") != 1:
        return ""
    local, domain_value = value.split("@")
    if not local or len(local) > 64 or not _DOT_ATOM_LOCAL_RE.fullmatch(local):
        return ""
    if any(char.isspace() for char in domain_value) or any(char in domain_value for char in "/\\:?#@"):
        return ""
    raw_labels = domain_value.split(".")
    if any(
        not label
        or label.startswith("-")
        or label.endswith("-")
        or not _HOST_LABEL_RE.fullmatch(label)
        for label in raw_labels
    ):
        return ""
    if raw_labels[0].casefold() == "www":
        return ""
    domain = _normalize_attribution_host(domain_value)
    if domain.startswith("www."):
        return ""
    if not domain or not registrable_domain(domain):
        return ""
    normalized = f"{local.casefold()}@{domain}"
    return normalized if len(normalized) <= 254 else ""


def mailbox_domain(email: str | None) -> str:
    mailbox = normalize_mailbox(email)
    return mailbox.rsplit("@", 1)[1] if mailbox else ""


def hosts_are_attributable(first: str | None, second: str | None) -> bool:
    """Return whether an email host is exact or a parent of an official host."""
    first_host = _normalize_attribution_host(first)
    second_host = _normalize_attribution_host(second)
    if not first_host or not second_host:
        return False
    first_company = registrable_domain(first_host)
    second_company = registrable_domain(second_host)
    if not first_company or first_company != second_company:
        return False
    return first_host == second_host or second_host.endswith("." + first_host)


def dedupe_key(company_name: str | None, website: str | None) -> str:
    return f"{normalize_company_name(company_name)}|{normalize_host(website)}"


def company_country_key(name: str | None, country: str | None) -> str:
    return f"{normalize_company_name(name)}|{normalize_space(country).lower()}"


def country_to_phone_region(country: str) -> str:
    mapping = {
        "Germany": "DE",
        "Netherlands": "NL",
        "Sweden": "SE",
        "UK": "GB",
        "United Kingdom": "GB",
        "France": "FR",
        "Italy": "IT",
        "Spain": "ES",
        "USA": "US",
        "United States": "US",
        "Canada": "CA",
    }
    return mapping.get(country, "US")


def today_iso() -> str:
    from datetime import date

    return date.today().isoformat()
