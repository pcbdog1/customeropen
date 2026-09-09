from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import phonenumbers

from .utils import UNKNOWN, normalize_domain, normalize_space

FREE_EMAIL_DOMAINS = {
    "yahoo.com",
    "googlemail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "gmx.com",
    "gmx.de",
}

ROLE_PREFIXES = {
    "sales",
    "info",
    "contact",
    "hello",
    "office",
    "support",
    "business",
    "purchasing",
    "procurement",
    "engineering",
    "enquiries",
    "inquiries",
    "mail",
    "service",
}

PRIVATE_OR_NOISY_PREFIXES = {
    "jobs",
    "career",
    "careers",
    "hr",
    "privacy",
    "legal",
    "press",
    "media",
    "webmaster",
    "postmaster",
    "admin",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w+-])", re.I)
RESOURCE_TLDS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "css",
    "js",
    "ico",
    "pdf",
    "zip",
}
NON_CONTACT_EMAIL_DOMAINS = {
    "wixpress.com",
    "sentry.io",
    "sentry-next.wixpress.com",
}
PLACEHOLDER_EMAILS = {
    "example@domain.com",
    "email@example.com",
    "johnsmith@example.com",
    "name@example.com",
    "name@example.org",
    "yourname@example.com",
}

GENERIC_COMPANY_EMAIL = "Generic Company Email"
PUBLIC_WORK_EMAIL = "Public Work Email"
PRIVATE_EMAIL = "Private Email"
CONTACT_FORM_ONLY = "Contact Form Only"
EMAIL_NOT_FOUND = "Not Found"


@dataclass(frozen=True)
class ContactCandidate:
    email: str
    email_type: str
    contact_method: str
    contact_form_url: str


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def visible_text(html: str) -> str:
    soup = make_soup(html)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return normalize_space(unescape(soup.get_text(" ")))


def extract_title(html: str) -> str:
    soup = make_soup(html)
    if soup.title and soup.title.string:
        return normalize_space(soup.title.string)
    h1 = soup.find("h1")
    return normalize_space(h1.get_text(" ")) if h1 else UNKNOWN


def is_public_business_email(email: str) -> bool:
    local, _, domain = email.lower().partition("@")
    if domain in FREE_EMAIL_DOMAINS:
        return True
    if not domain or "." not in domain:
        return False
    prefix = local.split("+", 1)[0]
    if prefix in PRIVATE_OR_NOISY_PREFIXES:
        return False
    if prefix in ROLE_PREFIXES:
        return True
    if "." in prefix:
        return False
    return len(prefix) <= 12 and not any(char.isdigit() for char in prefix)


def classify_email(email: str, source_url: str) -> str | None:
    if not is_valid_email_candidate(email):
        return None
    local, _, domain = email.lower().partition("@")
    prefix = local.split("+", 1)[0]
    source_domain = normalize_domain(source_url)
    same_company_domain = source_domain and domain == source_domain

    if domain in FREE_EMAIL_DOMAINS:
        if prefix in ROLE_PREFIXES:
            return GENERIC_COMPANY_EMAIL
        return PRIVATE_EMAIL
    if prefix in PRIVATE_OR_NOISY_PREFIXES:
        return None
    if prefix in ROLE_PREFIXES:
        return GENERIC_COMPANY_EMAIL
    if same_company_domain:
        return PUBLIC_WORK_EMAIL
    return PUBLIC_WORK_EMAIL


def extract_public_emails(html: str) -> list[str]:
    found: list[str] = []
    for match in EMAIL_RE.findall(html or ""):
        email = match.strip(".,;:()[]{}<>").lower()
        if is_valid_email_candidate(email) and is_public_business_email(email) and email not in found:
            found.append(email)
    return found


def extract_contact_candidates(html: str, source_url: str) -> list[ContactCandidate]:
    candidates: list[ContactCandidate] = []
    seen: set[str] = set()
    for match in EMAIL_RE.findall(html or ""):
        email = match.strip(".,;:()[]{}<>").lower()
        if not is_valid_email_candidate(email):
            continue
        email_type = classify_email(email, source_url)
        if not email_type or email in seen:
            continue
        seen.add(email)
        candidates.append(ContactCandidate(email=email, email_type=email_type, contact_method="Email", contact_form_url=""))

    priority = {GENERIC_COMPANY_EMAIL: 0, PUBLIC_WORK_EMAIL: 1, PRIVATE_EMAIL: 2}
    candidates.sort(key=lambda contact: (priority.get(contact.email_type, 9), contact.email))
    if candidates:
        return candidates

    form_url = extract_contact_form_url(html, source_url)
    if form_url:
        return [
            ContactCandidate(
                email=EMAIL_NOT_FOUND,
                email_type=CONTACT_FORM_ONLY,
                contact_method="Website Contact Form",
                contact_form_url=form_url,
            )
        ]
    return [ContactCandidate(email=EMAIL_NOT_FOUND, email_type=EMAIL_NOT_FOUND, contact_method="LinkedIn / Phone", contact_form_url="")]


def is_valid_email_candidate(email: str) -> bool:
    if email.lower() in PLACEHOLDER_EMAILS:
        return False
    local, _, domain = email.lower().partition("@")
    if not local or not domain or "." not in domain:
        return False
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in NON_CONTACT_EMAIL_DOMAINS):
        return False
    suffix = domain.rsplit(".", 1)[-1]
    if suffix in RESOURCE_TLDS:
        return False
    if any(char in email for char in ("/", "\\", "?", "#", "%")):
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    return True


def extract_phones(html: str, region: str) -> list[str]:
    text = visible_text(html)
    phones: list[str] = []
    for match in phonenumbers.PhoneNumberMatcher(text, region):
        number = normalize_space(match.raw_string)
        if number not in phones:
            phones.append(number)
    return phones


def extract_city(html: str) -> str:
    text = visible_text(html)
    patterns = [
        r"\b(?:Berlin|Munich|München|Hamburg|Cologne|Köln|Stuttgart|Düsseldorf|Dresden|Leipzig|Bremen|Aachen|Karlsruhe|Nuremberg|Nürnberg)\b",
        r"\b(?:Amsterdam|Eindhoven|Rotterdam|Delft|Utrecht|Groningen)\b",
        r"\b(?:Stockholm|Gothenburg|Göteborg|Malmö|Uppsala|Lund)\b",
        r"\b(?:London|Cambridge|Oxford|Manchester|Bristol|Birmingham|Edinburgh)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0)
    return UNKNOWN


def extract_links(html: str, base_url: str) -> dict[str, str]:
    soup = make_soup(html)
    links: dict[str, str] = {}
    for tag in soup.find_all("a", href=True):
        label = normalize_space(tag.get_text(" ")).lower()
        href = urljoin(base_url, tag["href"])
        lowered = href.lower()
        if "linkedin.com/company" in lowered and "linkedin" not in links:
            links["linkedin"] = href
        if any(word in label for word in ("contact", "kontakt")) and "contact" not in links:
            links["contact"] = href
        if any(word in label for word in ("impressum", "imprint")) and "impressum" not in links:
            links["impressum"] = href
        if any(word in label for word in ("legal notice", "legal")) and "legal" not in links:
            links["legal"] = href
        if any(word in label for word in ("datenschutz", "privacy")) and "privacy" not in links:
            links["privacy"] = href
        if any(word in label for word in ("ansprechpartner", "team")) and "team" not in links:
            links["team"] = href
        if any(word in label for word in ("career", "jobs", "vacancies")) and "careers" not in links:
            links["careers"] = href
    return links


def extract_contact_form_url(html: str, base_url: str) -> str:
    soup = make_soup(html)
    for tag in soup.find_all("a", href=True):
        label = normalize_space(tag.get_text(" ")).lower()
        href = urljoin(base_url, tag["href"])
        lowered = href.lower()
        if any(term in label for term in ("contact", "kontakt", "inquiry", "enquiry", "request")):
            return href
        if any(term in lowered for term in ("/contact", "/kontakt", "contact-form", "inquiry", "enquiry")):
            return href
    return ""


def likely_company_name_from_url_or_title(url: str, title: str) -> str:
    if title and title != UNKNOWN:
        for sep in (" - ", " | ", " – ", " — "):
            if sep in title:
                return normalize_space(title.split(sep)[0])
        return title
    domain = normalize_domain(url)
    return normalize_space(urlparse("https://" + domain).hostname.split(".")[0].replace("-", " ").title()) if domain else UNKNOWN
