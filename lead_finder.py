from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from contact_cache import ContactCache
from config import MAX_DIRECTORY_CANDIDATES
from keyword_performance import KeywordPerformanceStore
from pcb_leads.cli import build_lead
from pcb_leads.directory_sources import (
    NON_COMPANY_DISCOVERY_DOMAINS,
    directory_queries,
    is_directory_url,
    is_hardware_candidate,
    is_likely_company_result,
    prospect_queries,
)
from pcb_leads.fetcher import PublicPageFetcher
from pcb_leads.models import Lead
from pcb_leads.qualification import hardware_qualification, is_generic_company_name, resolve_company_name
from pcb_leads.keyword_library import (
    build_query_batch,
    email_first_queries,
    learn_public_terms,
    load_growth_state,
    primary_product,
    record_search_outcome,
)
from pcb_leads.parser import ContactCandidate, FREE_EMAIL_DOMAINS, extract_contact_candidates, is_valid_email_candidate
from pcb_leads.scorer import is_likely_peer_manufacturer
from pcb_leads.search import SearchResult, default_search_provider
from pcb_leads.seed_sources import seed_results_for
from pcb_leads.utils import (
    UNKNOWN,
    dedupe_key,
    hosts_are_attributable,
    mailbox_domain,
    normalize_domain,
    normalize_host,
)

from excel_manager import ExcelManager


OUTBOUND_LINK_BLOCKLIST = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "google.com",
    "bing.com",
}

DISCOVERY_CACHE_EXCLUSION_DOMAINS = NON_COMPANY_DISCOVERY_DOMAINS | {
    "bing.com",
    "duckduckgo.com",
    "google.com",
}


@dataclass
class DiscoveryStats:
    candidates_checked: int = 0
    directory_candidates: int = 0
    emails_found: int = 0
    cache_skips: int = 0
    search_queries: int = 0
    email_candidates: int = 0
    hardware_qualified: int = 0
    non_hardware_filtered: int = 0
    sent_history_skips: int = 0
    company_name_failures: int = 0
    provider_attempts: int = 0
    provider_failures: int = 0
    provider_diagnostics: list[dict[str, object]] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    top_queries: list[dict[str, object]] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def summary(self) -> str:
        return (
            f"candidates checked={self.candidates_checked}; "
            f"directory candidates={self.directory_candidates}; "
            f"public emails={self.emails_found}; cache skips={self.cache_skips}; "
            f"search queries={self.search_queries}; email candidates={self.email_candidates}; "
            f"hardware qualified={self.hardware_qualified}; non-hardware filtered={self.non_hardware_filtered}; "
            f"sent-history skips={self.sent_history_skips}; company name failures={self.company_name_failures}; "
            f"provider attempts={self.provider_attempts}; provider failures={self.provider_failures}; "
            f"skip reasons={self.skip_reasons or {}}"
        )


@dataclass
class _DomainCandidate:
    result: SearchResult
    source_links: list[str]
    directory_name: str
    source_type: str


DIRECT_PUBLIC_EMAILS = {}


def public_contact_fetcher() -> PublicPageFetcher:
    return PublicPageFetcher(
        user_agent="PCBDOG-Lead-Research/1.0 (+https://www.pcbdog.com)",
        timeout=6,
        pause_seconds=0.1,
    )


def public_discovery_search_provider():
    return default_search_provider("PCBDOG-Lead-Research/1.0 (+https://www.pcbdog.com)", timeout=12)


def primary_discovery_query(country: str, industry: str) -> str:
    rotation = int(load_growth_state().get("expansion_level", 0))
    queries = email_first_queries(country, industry, rotation=rotation, max_queries=1)
    return queries[0] if queries else f"{industry} hardware manufacturer company {country} contact"


def discover_directory_candidates(
    provider,
    country: str,
    industry: str,
    max_candidates: int = MAX_DIRECTORY_CANDIDATES,
    stats: DiscoveryStats | None = None,
    performance_store: KeywordPerformanceStore | None = None,
    query_metrics: dict[str, dict[str, object]] | None = None,
) -> list[SearchResult]:
    if max_candidates <= 0:
        return []
    queries = prospect_queries(country, industry)
    if not queries:
        return []
    if performance_store:
        query_lookup = {(item.query, item.source_name): item for item in queries}
        direct_queries = [item for item in queries if item.source_name == "Direct Manufacturers"]
        remaining_queries = [item for item in queries if item.source_name != "Direct Manufacturers"]
        queries = []
        for tier in (direct_queries, remaining_queries):
            ordered = performance_store.ordered([(item.query, item.source_name) for item in tier])
            queries.extend(query_lookup[item] for item in ordered)
    per_query = max(4, (max_candidates + len(queries) - 1) // len(queries))
    candidates: list[SearchResult] = []
    seen_urls: set[str] = set()
    for directory_query in queries:
        search_source = getattr(provider, "search_source", None)
        if callable(search_source):
            results = list(search_source(directory_query.query, per_query, directory_query.source_name))
        else:
            results = list(provider.search(directory_query.query, limit=per_query))
        if stats is not None:
            stats.search_queries += 1
        if query_metrics is not None:
            metric = query_metrics.setdefault(
                directory_query.query,
                {
                    "source_type": directory_query.source_name,
                    "candidates": 0,
                    "emails": 0,
                    "new_leads": 0,
                    "new_domains": 0,
                    "qualified_hardware": 0,
                },
            )
            metric["candidates"] = int(metric["candidates"]) + len(results)
        for result in results:
            result.query = directory_query.query
            normalized_url = result.url.rstrip("/").lower()
            if normalized_url in seen_urls:
                continue
            if directory_query.source_name != "Email-First Web" and not is_hardware_candidate(result.title, result.snippet):
                continue
            seen_urls.add(normalized_url)
            candidates.append(result)
            if stats is not None:
                stats.directory_candidates += 1
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def resolve_directory_result(result: SearchResult, fetcher) -> SearchResult | None:
    if not is_directory_url(result.url):
        return result
    listing_page = fetcher.fetch(result.url)
    if not listing_page:
        return None
    listing_domain = normalize_domain(listing_page.url)
    links: list[tuple[int, str]] = []
    for anchor in BeautifulSoup(listing_page.html or "", "html.parser").find_all("a", href=True):
        url = urljoin(listing_page.url, anchor["href"])
        domain = normalize_domain(url)
        if not domain or domain == listing_domain or domain in OUTBOUND_LINK_BLOCKLIST or is_directory_url(url):
            continue
        label = anchor.get_text(" ", strip=True).lower()
        score = 0 if any(term in label for term in ("website", "homepage", "company site")) else 1
        links.append((score, url))
    if not links:
        return None
    company_url = sorted(links, key=lambda item: item[0])[0][1]
    return SearchResult(
        title=result.title,
        url=f"https://{normalize_domain(company_url)}",
        snippet=result.snippet,
        query=result.query,
    )


def find_daily_leads(
    manager: ExcelManager,
    country: str,
    industry: str,
    max_new: int,
    *,
    fetcher=None,
    contact_cache: ContactCache | None = None,
    search_provider=None,
    stats: DiscoveryStats | None = None,
    keyword_state_updates: bool = False,
    keyword_performance: KeywordPerformanceStore | None = None,
    relax_not_found_cache: bool = False,
    candidate_results: list[SearchResult] | None = None,
    allow_directory_discovery: bool = True,
    email_enricher=None,
) -> list[Lead]:
    existing_keys = manager.existing_company_keys()
    sent_domains = {
        domain for value in manager.sent_domains() if (domain := normalize_domain(value))
    }
    existing_domains = manager.existing_domains() | sent_domains
    sent_emails = manager.sent_emails()
    existing_emails = manager.existing_emails()
    leads: list[Lead] = []
    cache = contact_cache or ContactCache()
    page_fetcher = fetcher or public_contact_fetcher()
    provider = search_provider or public_discovery_search_provider()
    discovery_stats = stats or DiscoveryStats()
    rotation = int(load_growth_state().get("expansion_level", 0))
    selected_product = primary_product(industry, rotation)
    performance_store = keyword_performance or (KeywordPerformanceStore() if keyword_state_updates else None)
    query_metrics: dict[str, dict[str, object]] = {}

    def metric_for(query: str, source_type: str = "Email-First Web") -> dict[str, object]:
        return query_metrics.setdefault(
            query,
            {
                "source_type": source_type,
                "candidates": 0,
                "emails": 0,
                "new_leads": 0,
                "new_domains": 0,
                "qualified_hardware": 0,
            },
        )

    def finish() -> list[Lead]:
        if performance_store:
            for query, metric in query_metrics.items():
                performance_store.record(
                    query,
                    str(metric["source_type"]),
                    int(metric["candidates"]),
                    int(metric["emails"]),
                    int(metric["new_leads"]),
                    new_domains=int(metric["new_domains"]),
                    qualified_hardware=int(metric["qualified_hardware"]),
                )
            discovery_stats.top_queries = performance_store.top_queries(5)
        if keyword_state_updates:
            record_search_outcome(len(leads), industry=industry, product=selected_product)
        return leads

    def cache_source(result: SearchResult, source_links: list[str]) -> tuple[str, str]:
        if source_links:
            return "directory", source_links[0]
        source_url = result.url
        lowered_url = source_url.casefold()
        if ".pdf" in lowered_url:
            return "pdf", source_url
        if "impressum" in lowered_url or "kontakt" in lowered_url:
            return "impressum", source_url
        if "contact" in lowered_url:
            return "contact", source_url
        if "legal" in lowered_url:
            return "legal", source_url
        return "official", source_url

    def is_cacheable_company_domain(domain: str, source_url: str) -> bool:
        source_domain = normalize_domain(source_url)
        return bool(
            domain
            and source_domain not in DISCOVERY_CACHE_EXCLUSION_DOMAINS
            and not is_directory_url(source_url)
        )

    run_seen_domains: set[str] = set()

    def resolve_domain_candidate(result: SearchResult) -> _DomainCandidate | None:
        source_type = str(metric_for(result.query)["source_type"])
        source_links: list[str] = []
        directory_name = result.title if is_directory_url(result.url) else ""
        if directory_name:
            source_links.append(result.url)
            result = resolve_directory_result(result, page_fetcher)
            if result is None:
                discovery_stats.skip("directory resolution failed")
                return None
        if not is_likely_company_result(result.title, result.url):
            discovery_stats.skip("non-company result")
            return None
        if is_likely_peer_manufacturer(f"{result.title} {result.snippet}"):
            discovery_stats.skip("PCB/EMS peer")
            return None
        domain = normalize_domain(result.url)
        if domain in sent_domains:
            discovery_stats.sent_history_skips += 1
            discovery_stats.skip("sent history domain")
            return None
        if not domain or domain in existing_domains:
            discovery_stats.skip("missing or duplicate domain")
            return None
        accept_domain = getattr(provider, "accept_company_domain", None)
        if callable(accept_domain):
            accepted = accept_domain(
                domain,
                query=result.query,
                source_key=source_type,
            )
        else:
            accepted = domain not in run_seen_domains
            run_seen_domains.add(domain)
        if not accepted:
            discovery_stats.skip("run duplicate or source quota")
            return None
        discovery_stats.candidates_checked += 1
        metric = metric_for(result.query, source_type)
        metric["new_domains"] = int(metric["new_domains"]) + 1
        return _DomainCandidate(result, source_links, directory_name, source_type)

    def consider(candidate: _DomainCandidate) -> None:
        if len(leads) >= max_new:
            return
        result = candidate.result
        source_links = candidate.source_links
        directory_name = candidate.directory_name
        query_metric = metric_for(result.query, candidate.source_type)
        domain = normalize_domain(result.url)
        source_type, source_url = cache_source(result, source_links)
        cacheable = is_cacheable_company_domain(domain, result.url)
        snippet_contact = select_attributable_company_contact(
            extract_contact_candidates(" ".join([result.title, result.snippet, result.url]), result.url),
            result.url,
        )
        cached = (
            cache.lookup(
                domain,
                source_type=source_type,
                source_url=source_url,
                allow_not_found_retry=relax_not_found_cache or bool(snippet_contact),
            )
            if cacheable
            else None
        )
        if cached and cached.is_in_cooldown and not snippet_contact:
            discovery_stats.cache_skips += 1
            discovery_stats.skip("contact cache cooldown")
            return
        official_page_texts: list[str] = []
        collect_root_page_evidence(page_fetcher, result.url, official_page_texts)
        if (
            not official_page_texts
            and result.url.rstrip("/").casefold() != _url_origin(result.url).rstrip("/").casefold()
        ):
            collect_official_page_evidence(
                page_fetcher,
                result.url,
                result.url,
                official_page_texts,
            )
        cached_contact = (
            select_attributable_company_contact(
                [ContactCandidate(cached.email, cached.email_type, "Cached Public Email Source", "")],
                result.url,
            )
            if cached and cached.outcome == "found" and is_valid_email_candidate(cached.email)
            else None
        )
        if cached and cached_contact:
            lead = seed_result_to_lead(result, country, industry)
            lead.email = cached_contact.email
            lead.email_type = cached_contact.email_type
            lead.contact_method = "Cached Public Email Source"
            lead.source_links = "; ".join(
                dict.fromkeys(value for value in [lead.source_links, cached.source_url, *source_links] if value)
            )
            collect_official_source_evidence(
                page_fetcher,
                cached.source_url,
                result.url,
                official_page_texts,
            )
        elif domain in DIRECT_PUBLIC_EMAILS and select_attributable_company_contact(
            extract_contact_candidates(DIRECT_PUBLIC_EMAILS[domain][0], result.url),
            result.url,
        ):
            lead = seed_result_to_lead(result, country, industry)
            lead.source_links = "; ".join(dict.fromkeys([lead.source_links, *source_links]))
            collect_official_source_evidence(
                page_fetcher,
                DIRECT_PUBLIC_EMAILS[domain][2],
                result.url,
                official_page_texts,
            )
        else:
            lead = build_seed_lead_with_public_email(
                result,
                page_fetcher,
                country,
                industry,
                provider=provider,
                extra_source_links=source_links,
                official_texts=official_page_texts,
            )
        collect_root_page_evidence(page_fetcher, result.url, official_page_texts)
        if not official_page_texts:
            discovery_stats.skip("official page evidence unavailable")
            return
        official_texts = [result.title, result.snippet, *official_page_texts]
        if lead is not None:
            discovery_stats.email_candidates += 1
            query_metric["emails"] = int(query_metric["emails"]) + 1
            # Curated local records already carry an auditable company identity.
            # Do not replace it with a footer credit or a truncated legal parser match.
            if result.query.startswith("Local Curated Public Seed:") and not is_generic_company_name(result.title):
                company_name = result.title.strip()
            else:
                company_name = resolve_company_name(result.title, lead.website, official_texts, directory_name=directory_name)
            if not company_name or is_generic_company_name(company_name):
                discovery_stats.company_name_failures += 1
                discovery_stats.skip("company name unresolved")
                return
            lead.company_name = company_name

        qualification = hardware_qualification(
            official_texts,
            company_name=lead.company_name if lead is not None else "",
        )
        if not qualification.eligible:
            discovery_stats.non_hardware_filtered += 1
            discovery_stats.skip("non-hardware website")
            return
        discovery_stats.hardware_qualified += 1
        query_metric["qualified_hardware"] = int(query_metric["qualified_hardware"]) + 1
        if lead is None and email_enricher is not None:
            enriched_email = email_enricher.find_public_business_email(domain)
            if enriched_email:
                lead = seed_result_to_lead(result, country, industry)
                lead.email = enriched_email
                lead.email_type = "Public Work Email"
                lead.contact_method = "Public business email enrichment API"
                lead.source_links = "; ".join(dict.fromkeys([lead.source_links, result.url]))
        if lead is None:
            if cacheable:
                cache.record(domain, "not_found", source_url, "", "", source_type=source_type)
            discovery_stats.skip("email not found")
            return
        lead.pcb_need_reason = (
            f"{lead.pcb_need_reason} hardware signals: {', '.join(qualification.hardware_signals)}."
        )

        key = dedupe_key(lead.company_name, lead.website)
        email = lead.email.strip().lower()
        if email in sent_emails:
            discovery_stats.sent_history_skips += 1
            discovery_stats.skip("sent history email")
            return
        if (
            not email
            or email == "not found"
            or key in existing_keys
            or lead.domain in existing_domains
            or email in existing_emails
        ):
            discovery_stats.skip("duplicate company or email")
            return
        existing_keys.add(key)
        existing_domains.add(lead.domain)
        existing_emails.add(email)
        leads.append(lead)
        if cacheable:
            cache.record(domain, "found", lead.source_links, lead.email, lead.email_type, source_type=source_type)
        discovery_stats.emails_found += 1
        query_metric["new_leads"] = int(query_metric["new_leads"]) + 1
        if keyword_state_updates:
            learn_public_terms(
                f"{result.title} {result.snippet}",
                industry=industry,
                source_url=lead.website or result.url,
            )

    seed_results = list(candidate_results) if candidate_results is not None else list(seed_results_for(country, industry, max_new * 6))
    for result in seed_results:
        seed_metric = metric_for(result.query, "Seed Results")
        seed_metric["candidates"] = int(seed_metric["candidates"]) + 1
    domain_candidates = [
        candidate
        for result in seed_results
        if (candidate := resolve_domain_candidate(result)) is not None
    ]
    for candidate in domain_candidates:
        consider(candidate)
        if len(leads) >= max_new:
            break
    if allow_directory_discovery and len(leads) < max_new:
        directory_limit = min(MAX_DIRECTORY_CANDIDATES, max(30, max_new * 6))
        searched_results = discover_directory_candidates(
            provider,
            country,
            industry,
            directory_limit,
            discovery_stats,
            performance_store,
            query_metrics,
        )
        searched_candidates = [
            candidate
            for result in searched_results
            if (candidate := resolve_domain_candidate(result)) is not None
        ]
        for candidate in searched_candidates:
            consider(candidate)
            if len(leads) >= max_new:
                break
    return finish()


def has_available_direct_email_seeds(manager: ExcelManager, country: str, industry: str, max_new: int) -> bool:
    existing_domains = manager.existing_domains()
    existing_emails = manager.existing_emails()
    for result in seed_results_for(country, industry, max_new * 6):
        domain = normalize_domain(result.url)
        if domain in existing_domains or domain not in DIRECT_PUBLIC_EMAILS:
            continue
        email = DIRECT_PUBLIC_EMAILS[domain][0].strip().lower()
        if email and email not in existing_emails:
            return True
    return False


def has_available_candidate_seeds(
    manager: ExcelManager,
    country: str,
    industry: str,
    max_new: int,
    *,
    contact_cache: ContactCache | None = None,
) -> bool:
    existing_domains = manager.existing_domains()
    existing_emails = manager.existing_emails()
    cache = contact_cache or ContactCache()
    for result in seed_results_for(country, industry, max_new * 6):
        domain = normalize_domain(result.url)
        if not domain or domain in existing_domains:
            continue
        cached = cache.lookup(domain)
        if cached and cached.is_in_cooldown:
            continue
        if cached and cached.outcome == "found" and is_valid_email_candidate(cached.email):
            if cached.email.strip().lower() in existing_emails:
                continue
            return True
        if domain in DIRECT_PUBLIC_EMAILS:
            email = DIRECT_PUBLIC_EMAILS[domain][0].strip().lower()
            if email in existing_emails:
                continue
        return True
    return country in {"Brazil", "Mexico", "Chile", "Argentina", "Australia", "New Zealand"}


def has_available_search_sources(
    manager: ExcelManager,
    country: str,
    industry: str,
    max_new: int,
    *,
    contact_cache: ContactCache | None = None,
) -> bool:
    if has_available_candidate_seeds(
        manager,
        country,
        industry,
        max_new,
        contact_cache=contact_cache,
    ):
        return True
    return bool(directory_queries(country, industry))


def add_results_as_leads(
    results,
    leads: list[Lead],
    max_new: int,
    existing_keys: set[str],
    existing_domains: set[str],
    existing_emails: set[str],
    country: str,
    industry: str,
    *,
    allow_seed_fallback: bool = False,
    direct_email_only: bool = False,
) -> None:
    for result in results:
        if len(leads) >= max_new:
            break
        result_domain = normalize_domain(result.url)
        if result_domain in existing_domains:
            continue
        if allow_seed_fallback:
            if direct_email_only:
                if normalize_domain(result.url) not in DIRECT_PUBLIC_EMAILS:
                    continue
                lead = seed_result_to_lead(result, country, industry)
            else:
                lead = seed_result_to_lead(result, country, industry)
            key = dedupe_key(lead.company_name, lead.website)
            email = lead.email.strip().lower()
            if key in existing_keys or lead.domain in existing_domains or (email and email != "not found" and email in existing_emails):
                continue
            existing_keys.add(key)
            existing_domains.add(lead.domain)
            leads.append(lead)
            continue


def build_seed_lead_with_public_email(
    result,
    fetcher,
    country: str,
    industry: str,
    provider=None,
    extra_source_links: list[str] | None = None,
    official_texts: list[str] | None = None,
) -> Lead | None:
    """Find one attributable public email without doing full profile enrichment."""
    if not is_likely_company_result(result.title, result.url):
        return None
    if normalize_domain(result.url) in DIRECT_PUBLIC_EMAILS:
        direct_lead = seed_result_to_lead(result, country, industry)
        direct_contacts = extract_contact_candidates(direct_lead.email, result.url)
        if select_attributable_company_contact(direct_contacts, result.url):
            return direct_lead
    snippet_contacts = extract_contact_candidates(
        " ".join([result.title, result.snippet, result.url]),
        result.url,
    )
    snippet_contact = select_attributable_company_contact(snippet_contacts, result.url)
    if snippet_contact:
        lead = seed_result_to_lead(result, country, industry)
        lead.email = snippet_contact.email
        lead.email_type = snippet_contact.email_type
        lead.contact_method = "Public Search Result"
        lead.source_links = "; ".join(dict.fromkeys([result.url, *(extra_source_links or [])]))
        return lead
    saw_contact_form = False
    contact_urls = seed_contact_urls(result.url, country)
    for url in contact_urls:
        page = fetcher.fetch(url)
        if not page:
            continue
        if official_texts is not None and is_official_company_page(page.url, result.url):
            append_official_evidence(official_texts, page.html)
        page_candidates = extract_contact_candidates(page.html, page.url)
        saw_contact_form = saw_contact_form or any(
            candidate.email_type == "Contact Form Only" for candidate in page_candidates
        )
        page_contact = select_attributable_company_contact(page_candidates, result.url)
        if not page_contact:
            continue
        lead = build_lead(
            result,
            "\n".join([result.snippet, page.html]),
            page.url,
            country,
            industry,
            [page.url, *(extra_source_links or [])],
        )
        if lead:
            lead.email = page_contact.email
            lead.email_type = page_contact.email_type
            lead.contact_method = "Official Company Page"
            return lead
    if saw_contact_form:
        for url in secondary_contact_urls(result.url):
            page = fetcher.fetch(url)
            if not page:
                continue
            if official_texts is not None and is_official_company_page(page.url, result.url):
                append_official_evidence(official_texts, page.html)
            page_contact = select_attributable_company_contact(
                extract_contact_candidates(page.html, page.url),
                result.url,
            )
            if not page_contact:
                continue
            lead = build_lead(
                result,
                "\n".join([result.snippet, page.html]),
                page.url,
                country,
                industry,
                [page.url, *(extra_source_links or [])],
            )
            if lead:
                lead.email = page_contact.email
                lead.email_type = page_contact.email_type
                lead.contact_method = "Official Company Page"
                return lead
    if provider:
        domain = normalize_domain(result.url)
        for query in [f'"{domain}" email contact']:
            for found in provider.search(query, limit=5):
                contacts = extract_contact_candidates(" ".join([found.title, found.snippet, found.url]), result.url)
                contact = select_attributable_company_contact(contacts, result.url)
                if not contact:
                    continue
                lead = seed_result_to_lead(result, country, industry)
                lead.email = contact.email
                lead.email_type = contact.email_type
                lead.contact_method = "Public Search Result"
                lead.source_links = "; ".join(sorted({result.url, found.url}))
                lead.demand_signal_source = query
                return lead
    return None


def is_official_company_page(page_url: str, company_url: str) -> bool:
    return bool(normalize_domain(page_url) and normalize_domain(page_url) == normalize_domain(company_url))


def append_official_evidence(official_texts: list[str], text: str) -> None:
    if text and text not in official_texts:
        official_texts.append(text)


def collect_official_page_evidence(
    fetcher,
    page_url: str,
    company_url: str,
    official_texts: list[str],
) -> None:
    page = fetcher.fetch(page_url)
    if page and is_official_company_page(page.url, company_url):
        append_official_evidence(official_texts, page.html)


def collect_official_source_evidence(
    fetcher,
    source_urls: str,
    company_url: str,
    official_texts: list[str],
) -> None:
    for source_url in (value.strip() for value in (source_urls or "").split(";")):
        if source_url.startswith(("http://", "https://")) and is_official_company_page(source_url, company_url):
            collect_official_page_evidence(fetcher, source_url, company_url, official_texts)


def collect_root_page_evidence(fetcher, company_url: str, official_texts: list[str]) -> None:
    collect_official_page_evidence(
        fetcher,
        _url_origin(company_url),
        company_url,
        official_texts,
    )


def select_attributable_company_contact(
    candidates: list[ContactCandidate],
    company_url: str,
) -> ContactCandidate | None:
    company_host = normalize_host(company_url)
    if not company_host:
        return None
    for candidate in candidates:
        if candidate.email == "Not Found":
            continue
        email_domain = mailbox_domain(candidate.email)
        if email_domain in FREE_EMAIL_DOMAINS:
            continue
        if hosts_are_attributable(email_domain, company_host):
            return candidate
    return None


def _url_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        parsed = urlsplit(f"https://{base_url.lstrip('/')}")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def seed_contact_urls(base_url: str, country: str) -> list[str]:
    if country.casefold() in {"germany", "austria", "switzerland"}:
        paths = ["impressum", "kontakt", "legal-notice", "contact"]
    else:
        paths = ["contact", "contact-us", "about", "support"]
    origin = _url_origin(base_url)
    return [origin + "/", *(urljoin(origin + "/", path) for path in paths)]


def secondary_contact_urls(base_url: str) -> list[str]:
    paths = ["about-us", "legal", "company", "team", "sales", "distributors", "partners"]
    origin = _url_origin(base_url)
    return [urljoin(origin + "/", path) for path in paths]


def seed_result_to_lead(result, country: str, industry: str) -> Lead:
    website = f"https://{normalize_host(result.url)}"
    source = result.url
    domain = normalize_domain(result.url)
    email, email_type, email_source = DIRECT_PUBLIC_EMAILS.get(domain, ("Not Found", "Not Found", ""))
    business = result.snippet or f"{industry} hardware or electronics products"
    reason = (
        f"{industry} products typically include controllers, sensors, embedded electronics, firmware, "
        "or assembled PCBAs; source is the company's public official website listing."
    )
    return Lead(
        country=country,
        city=UNKNOWN,
        company_name=result.title,
        website=website,
        business=business,
        industry=industry,
        pcb_need_reason=reason,
        demand_signal="官网产品包含电子硬件",
        demand_signal_source=result.query,
        employees="未找到",
        founded="未找到",
        email=email,
        email_type=email_type,
        contact_method="Public Email Source" if email != "Not Found" else "Website Contact Form / LinkedIn / Phone",
        contact_form_url=website,
        contact_name=UNKNOWN,
        contact_title=UNKNOWN,
        phone=UNKNOWN,
        linkedin=UNKNOWN,
        source_links="; ".join([value for value in [source, email_source] if value]),
        score=4,
        development_angle="嵌入式电子产品PCB/PCBA打样和中小批量生产",
        email_subject=f"PCB & PCBA Manufacturing Support for {industry} Products",
        notes="Validated direct public email seed; no deep profile enrichment performed.",
        compliance_note=(
            "Public business contact collected from source URL. "
            "B2B relevance: PCB/PCBA manufacturing service matches company hardware/electronics business. "
            "Outreach email must include opt-out sentence."
        ),
    )
