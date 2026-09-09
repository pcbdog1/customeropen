from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from openpyxl import Workbook

from contact_cache import ContactCache
from excel_manager import ExcelManager
from excel_manager import SENT_LOG_SHEET
from keyword_performance import KeywordPerformanceStore
import lead_finder
from lead_finder import (
    DiscoveryStats,
    build_seed_lead_with_public_email,
    discover_directory_candidates,
    find_daily_leads,
    has_available_candidate_seeds,
    has_available_search_sources,
    primary_discovery_query,
    resolve_directory_result,
    seed_result_to_lead,
    select_attributable_company_contact,
    secondary_contact_urls,
    seed_contact_urls,
)
from pcb_leads.directory_sources import prospect_queries
from pcb_leads.parser import extract_contact_candidates
from pcb_leads.search import SearchResult
from pcb_leads.utils import normalize_host


class FakeFetcher:
    def fetch(self, url: str):
        if url.endswith("/contact"):
            return type(
                "Page",
                (),
                {
                    "url": url,
                    "html": """
                    <html>
                      <title>ACME Sensors</title>
                      <body>
                        ACME Sensors builds industrial sensor hardware, embedded controllers,
                        firmware and electronics for automation devices.
                        Contact: info@acme-sensors.example
                      </body>
                    </html>
                    """,
                },
            )()
        return None


class FakeSearchProvider:
    def search(self, query: str, limit: int):
        return [
            SearchResult(
                title="ACME Sensors contact PDF",
                url="https://example-directory.test/acme-sensors-profile",
                snippet="ACME Sensors public contact: founder@yahoo.com and support@acme-sensors.example",
                query=query,
            )
        ][:limit]


class PublicDiscoverySearchProvider:
    def search(self, query: str, limit: int):
        return [acme_result()][:limit]


class PublicEmailFallbackSearchProvider:
    def search(self, query: str, limit: int):
        if "acme-sensors.example" in query:
            return [
                SearchResult(
                    title="ACME Sensors public contact",
                    url="https://public-directory.example/acme",
                    snippet="Industrial sensor manufacturer contact sales@acme-sensors.example",
                    query=query,
                )
            ]
        return [acme_result()][:limit]


class NoEmailFetcher:
    def fetch(self, url: str):
        return None


class EmptySearchProvider:
    def search(self, query: str, limit: int):
        return []


class FetchMustNotRun:
    def fetch(self, url: str):
        raise AssertionError(f"page fetch should be skipped for snippet email: {url}")


class FakeDirectoryProvider:
    def search(self, query: str, limit: int):
        if "industrystock.com" not in query:
            return []
        return [
            SearchResult(
                title="Alpha Controls",
                url="https://www.industrystock.com/en/company/alpha-controls",
                snippet="Industrial controllers, sensors, and electronic equipment manufacturer.",
                query=query,
            ),
            SearchResult(
                title="Alpha Controls duplicate",
                url="https://www.industrystock.com/en/company/alpha-controls",
                snippet="Industrial electronics and automation.",
                query=query,
            ),
            SearchResult(
                title="Cloud Workflow",
                url="https://www.industrystock.com/en/company/cloud-workflow",
                snippet="SaaS consulting and software services.",
                query=query,
            ),
            SearchResult(
                title="Beta Instruments",
                url="https://www.industrystock.com/en/company/beta-instruments",
                snippet="Laboratory analyzers and measurement equipment.",
                query=query,
            ),
        ][:limit]


class DirectoryContactFetcher:
    def fetch(self, url: str):
        pages = {
            "https://www.industrystock.com/en/company/alpha-controls": """
                <a href="https://alpha.example">Company website</a>
            """,
            "https://alpha.example": "Industrial electronic controllers and sensors.",
            "https://alpha.example/contact": "Industrial electronic controllers and sensors. Contact sales@alpha.example",
        }
        html = pages.get(url)
        if html is None:
            return None
        return type("Page", (), {"url": url, "html": html})()


def make_manager(tmp_path):
    workbook = tmp_path / "leads.xlsx"
    wb = Workbook()
    wb.active.title = "Leads"
    wb.save(workbook)
    return ExcelManager(workbook)


def acme_result():
    return SearchResult(
        title="ACME Sensors",
        url="https://acme-sensors.example",
        snippet="Industrial sensor hardware and embedded electronics.",
        query="seed fallback: Sensor hardware Australia",
    )


def test_seed_contact_page_public_company_email_is_preferred():
    result = SearchResult(
        title="ACME Sensors",
        url="https://acme-sensors.example",
        snippet="Industrial sensor hardware and embedded electronics.",
        query="seed fallback: Sensor hardware Germany",
    )

    lead = build_seed_lead_with_public_email(result, FakeFetcher(), "Germany", "Sensor")

    assert lead is not None
    assert lead.email == "info@acme-sensors.example"
    assert lead.email_type == "Generic Company Email"
    assert lead.score in {4, 5}


def test_seed_public_search_email_is_used_when_contact_page_has_no_email():
    result = SearchResult(
        title="ACME Sensors",
        url="https://acme-sensors.example",
        snippet="Industrial sensor hardware and embedded electronics.",
        query="seed fallback: Sensor hardware Germany",
    )

    lead = build_seed_lead_with_public_email(result, NoEmailFetcher(), "Germany", "Sensor", FakeSearchProvider())

    assert lead is not None
    assert lead.email == "support@acme-sensors.example"
    assert lead.email_type == "Generic Company Email"
    assert "example-directory.test" in lead.source_links


def test_company_contact_rejects_free_and_third_party_domains():
    candidates = extract_contact_candidates(
        "sales@acme.example info@glassdoor.com founder@yahoo.com",
        "https://acme.example/contact",
    )

    selected = select_attributable_company_contact(candidates, "https://acme.example")

    assert selected is not None
    assert selected.email == "sales@acme.example"


def test_company_contact_returns_none_without_official_domain_email():
    candidates = extract_contact_candidates(
        "info@godaddy.com founder@yahoo.com",
        "https://acme.example/contact",
    )

    assert select_attributable_company_contact(candidates, "https://acme.example") is None


def test_normalize_host_preserves_multilevel_company_hostname():
    assert normalize_host("https://sensor.uk.com/contact") == "sensor.uk.com"


def test_company_contact_accepts_exact_multilevel_company_hostname():
    candidates = extract_contact_candidates(
        "office@sensor.uk.com",
        "https://sensor.uk.com/contact",
    )

    selected = select_attributable_company_contact(candidates, "https://sensor.uk.com")

    assert selected is not None
    assert selected.email == "office@sensor.uk.com"


def test_company_contact_rejects_subdomain_email_for_parent_host():
    candidates = extract_contact_candidates(
        "office@sensor.uk.com",
        "https://uk.com/contact",
    )

    assert select_attributable_company_contact(candidates, "https://uk.com") is None


def test_company_contact_rejects_public_suffix_mailbox_for_registrable_company():
    candidates = extract_contact_candidates("sales@co.uk", "https://acme.co.uk/contact")

    assert select_attributable_company_contact(candidates, "https://acme.co.uk") is None


def test_company_contact_accepts_parent_mailbox_domain_for_official_subdomain():
    candidates = extract_contact_candidates("sales@acme.co.uk", "https://shop.acme.co.uk/contact")

    selected = select_attributable_company_contact(candidates, "https://shop.acme.co.uk")

    assert selected is not None
    assert selected.email == "sales@acme.co.uk"


def test_company_contact_rejects_child_mailbox_domain_for_parent_official_host():
    candidates = extract_contact_candidates("sales@mail.acme.co.uk", "https://acme.co.uk/contact")

    assert select_attributable_company_contact(candidates, "https://acme.co.uk") is None


def test_company_contact_rejects_sibling_host_even_with_same_registrable_domain():
    candidates = extract_contact_candidates("sales@mail.acme.co.uk", "https://shop.acme.co.uk/contact")

    assert select_attributable_company_contact(candidates, "https://shop.acme.co.uk") is None


def test_company_contact_rejects_www_mailbox_sibling_for_official_subdomain():
    candidates = extract_contact_candidates("sales@www.acme.co.uk", "https://shop.acme.co.uk/contact")

    assert select_attributable_company_contact(candidates, "https://shop.acme.co.uk") is None


def test_seed_result_preserves_multilevel_company_hostname():
    result = SearchResult(
        title="Sensor UK",
        url="https://sensor.uk.com/contact",
        snippet="Industrial sensors and embedded electronics.",
        query="UK Sensor contact",
    )

    lead = seed_result_to_lead(result, "UK", "Sensor")

    assert lead.website == "https://sensor.uk.com"


def test_search_platform_result_is_not_a_company_lead():
    result = SearchResult(
        "936 Hardware engineering manager jobs in United States",
        "https://glassdoor.com/Job/hardware-engineering-manager-jobs-SRCH_KO0,28.htm",
        "Recruitment listings info@glassdoor.com",
        "USA Hardware Engineer electronics",
    )

    assert build_seed_lead_with_public_email(result, NoEmailFetcher(), "USA", "Industrial Electronics") is None


def test_seed_search_snippet_email_skips_page_fetch():
    result = SearchResult(
        title="ACME Sensors contact",
        url="https://acme-sensors.example/contact",
        snippet="Industrial sensor manufacturer sales@acme-sensors.example",
        query="Australia sensor company contact sales",
    )

    lead = build_seed_lead_with_public_email(
        result,
        FetchMustNotRun(),
        "Australia",
        "Sensor",
    )

    assert lead is not None
    assert lead.email == "sales@acme-sensors.example"
    assert lead.contact_method == "Public Search Result"


def test_dach_contact_paths_prioritize_legal_contact_pages():
    assert seed_contact_urls("https://acme.example", "Germany")[:5] == [
        "https://acme.example/",
        "https://acme.example/impressum",
        "https://acme.example/kontakt",
        "https://acme.example/legal-notice",
        "https://acme.example/contact",
    ]


def test_non_dach_contact_paths_prioritize_contact_and_support():
    assert seed_contact_urls("https://acme.example", "USA")[:5] == [
        "https://acme.example/",
        "https://acme.example/contact",
        "https://acme.example/contact-us",
        "https://acme.example/about",
        "https://acme.example/support",
    ]


def test_secondary_contact_paths_cover_company_team_sales_and_partners():
    assert secondary_contact_urls("https://acme.example") == [
        "https://acme.example/about-us",
        "https://acme.example/legal",
        "https://acme.example/company",
        "https://acme.example/team",
        "https://acme.example/sales",
        "https://acme.example/distributors",
        "https://acme.example/partners",
    ]


def test_contact_paths_use_complete_origin_instead_of_result_path():
    result_url = "https://store.eu.acme.example/catalog/index.html?source=search#result"

    assert seed_contact_urls(result_url, "USA")[:3] == [
        "https://store.eu.acme.example/",
        "https://store.eu.acme.example/contact",
        "https://store.eu.acme.example/contact-us",
    ]
    assert secondary_contact_urls(result_url)[:3] == [
        "https://store.eu.acme.example/about-us",
        "https://store.eu.acme.example/legal",
        "https://store.eu.acme.example/company",
    ]
    assert all("/catalog/" not in url and "index.html" not in url for url in [
        *seed_contact_urls(result_url, "USA"),
        *secondary_contact_urls(result_url),
    ])


def test_find_daily_leads_uses_public_contact_fallback_when_direct_map_is_empty(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [acme_result()])
    monkeypatch.setattr(lead_finder, "DIRECT_PUBLIC_EMAILS", {})

    leads = find_daily_leads(
        manager,
        "Australia",
        "Instrumentation",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
    )

    assert [(lead.company_name, lead.email) for lead in leads] == [("Acme Sensors", "info@acme-sensors.example")]
    cached = cache.lookup("acme-sensors.example")
    assert cached is not None
    assert cached.outcome == "found"


def test_find_daily_leads_uses_one_public_discovery_query_after_seed_shortfall(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    leads = find_daily_leads(
        manager,
        "Australia",
        "Instrumentation",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
        search_provider=PublicDiscoverySearchProvider(),
    )

    assert [(lead.company_name, lead.email) for lead in leads] == [("Acme Sensors", "info@acme-sensors.example")]


def test_find_daily_leads_uses_public_search_contact_when_site_pages_have_no_email(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [acme_result()])

    class OfficialEvidenceNoEmailFetcher:
        def fetch(self, url):
            if url == "https://acme-sensors.example":
                return type(
                    "Page",
                    (),
                    {"url": url, "html": "<html><body>Industrial sensors use embedded controllers and electronics.</body></html>"},
                )()
            return None

    leads = find_daily_leads(
        manager,
        "Australia",
        "Instrumentation",
        1,
        fetcher=OfficialEvidenceNoEmailFetcher(),
        contact_cache=cache,
        search_provider=PublicEmailFallbackSearchProvider(),
    )

    assert [(lead.company_name, lead.email) for lead in leads] == [
        ("Acme Sensors", "sales@acme-sensors.example")
    ]
    assert "public-directory.example/acme" in leads[0].source_links


def test_directory_discovery_filters_software_dedupes_urls_and_caps_candidates():
    results = discover_directory_candidates(FakeDirectoryProvider(), "Germany", "Sensor", 2)

    assert [result.title for result in results] == ["Alpha Controls", "Beta Instruments"]


def test_directory_result_resolves_to_public_company_website():
    listing = FakeDirectoryProvider().search("site:industrystock.com Sensor Germany", 1)[0]

    resolved = resolve_directory_result(listing, DirectoryContactFetcher())

    assert resolved is not None
    assert resolved.url == "https://alpha.example"
    assert resolved.query == listing.query


def test_directory_domains_are_all_resolved_before_contact_enrichment(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])
    alpha_listing = "https://www.industrystock.com/en/company/alpha-controls"
    beta_listing = "https://www.industrystock.com/en/company/beta-instruments"

    class TwoCompanyProvider:
        def search(self, query, limit):
            if "industrystock.com" not in query:
                return []
            return [
                SearchResult("Alpha Controls", alpha_listing, "Industrial sensor electronics", query),
                SearchResult("Beta Instruments", beta_listing, "Laboratory electronic instruments", query),
            ][:limit]

    class PhaseOrderFetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            if url == alpha_listing:
                return type("Page", (), {"url": url, "html": '<a href="https://alpha.example">Company website</a>'})()
            if url == beta_listing:
                return type("Page", (), {"url": url, "html": '<a href="https://beta.example">Company website</a>'})()
            if url.startswith("https://alpha.example"):
                assert beta_listing in self.calls
                return type(
                    "Page",
                    (),
                    {"url": url, "html": "Alpha industrial sensor electronics sales@alpha.example"},
                )()
            if url == "https://beta.example":
                return type("Page", (), {"url": url, "html": "Beta laboratory electronic instruments"})()
            return None

    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=PhaseOrderFetcher(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=TwoCompanyProvider(),
    )

    assert [lead.domain for lead in leads] == ["alpha.example"]


def test_workbook_and_sent_log_domains_are_excluded_before_enrichment(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    manager.sheet("Leads").append([1, "2026-08-10", "Germany", "", "Existing", "https://existing.example"])
    manager.sheet(SENT_LOG_SHEET).append(
        ["2026-08-09T10:00:00", "Sent", "sales@sent.example", "subject", "SENT", "", "Germany", "Sensor", "https://sent.example", "sent.example"]
    )
    monkeypatch.setattr(
        lead_finder,
        "seed_results_for",
        lambda *_: [
            SearchResult("Existing", "https://existing.example", "sensor electronics", "seed"),
            SearchResult("Sent", "https://sent.example", "sensor electronics", "seed"),
        ],
    )

    class FetchMustStayExcluded:
        def fetch(self, url):
            raise AssertionError(f"excluded domain reached contact enrichment: {url}")

    stats = DiscoveryStats()
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        2,
        fetcher=FetchMustStayExcluded(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.sent_history_skips == 1
    assert stats.skip_reasons["sent history domain"] == 1
    assert stats.skip_reasons["missing or duplicate domain"] == 1


def test_sent_history_email_skip_is_counted_separately_from_duplicates(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    manager.sheet(SENT_LOG_SHEET).append(
        ["2026-08-09T10:00:00", "Repeat", "sales@repeat.example", "subject", "SENT", "", "Germany", "Sensor", "", ""]
    )
    monkeypatch.setattr(
        lead_finder,
        "seed_results_for",
        lambda *_: [
            SearchResult(
                "Repeat Sensors",
                "https://repeat.example",
                "Industrial sensor controller manufacturer. Contact sales@repeat.example.",
                "seed",
            )
        ],
    )

    class RepeatFetcher:
        def fetch(self, url):
            if url.startswith("https://repeat.example"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": (
                            "<html><body>Repeat Sensors builds industrial sensor controllers. "
                            "Contact sales@repeat.example.</body></html>"
                        ),
                    },
                )()
            return None

    stats = DiscoveryStats()
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=RepeatFetcher(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.sent_history_skips == 1
    assert stats.skip_reasons["sent history email"] == 1
    assert "duplicate company or email" not in stats.skip_reasons


def test_failed_domain_remains_seen_across_fallback_searches_in_one_run(tmp_path, monkeypatch):
    from main import RunSearchBudget

    manager = make_manager(tmp_path)
    failed = SearchResult("Failed Sensors", "https://failed.example", "sensor electronics", "seed")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [failed])
    budget = RunSearchBudget(EmptySearchProvider(), max_queries=300)

    class CountingFetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            return None

    fetcher = CountingFetcher()
    for index in range(2):
        find_daily_leads(
            manager,
            "Germany",
            "Sensor",
            1,
            fetcher=fetcher,
            contact_cache=ContactCache(tmp_path / f"contacts-{index}.json"),
            search_provider=budget,
        )

    assert fetcher.calls.count("https://failed.example/impressum") == 1


def test_directory_discovery_runs_after_resolved_seed_domain_has_no_email(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(
        lead_finder,
        "seed_results_for",
        lambda *_: [SearchResult("No Contact", "https://no-contact.example", "sensor electronics", "seed")],
    )

    class FreshDirectoryProvider:
        def search(self, query, limit):
            if "email" in query and "contact" in query:
                return []
            return [
                SearchResult(
                    "Fresh Instruments",
                    "https://fresh.example",
                    "Industrial sensor electronics and embedded controllers",
                    query,
                )
            ][:limit]

    class SeedShortfallFetcher:
        def fetch(self, url):
            if url == "https://fresh.example/contact":
                return type(
                    "Page",
                    (),
                    {"url": url, "html": "Fresh industrial sensor electronics sales@fresh.example"},
                )()
            return None

    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=SeedShortfallFetcher(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=FreshDirectoryProvider(),
    )

    assert [lead.domain for lead in leads] == ["fresh.example"]


def test_find_daily_leads_keeps_directory_and_contact_sources(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    stats = DiscoveryStats()
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=DirectoryContactFetcher(),
        contact_cache=cache,
        search_provider=FakeDirectoryProvider(),
        stats=stats,
    )

    assert len(leads) == 1
    assert leads[0].website == "https://alpha.example"
    assert leads[0].email == "sales@alpha.example"
    assert "industrystock.com/en/company/alpha-controls" in leads[0].source_links
    assert "alpha.example/contact" in leads[0].source_links
    assert stats.directory_candidates == 2
    assert stats.emails_found == 1
    assert "public emails=1" in stats.summary()


def test_find_daily_leads_resolves_generic_contact_title_from_official_hostname(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Contact",
        "https://acme-controls.de",
        "Industrial sensor control systems.",
        "Germany automation contact",
    )

    class AcmeControlsFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>Acme Controls builds embedded controllers and industrial sensors. sales@acme-controls.de</body></html>",
                    },
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Automation Equipment",
        1,
        fetcher=AcmeControlsFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert [lead.company_name for lead in leads] == ["Acme Controls"]
    assert "hardware signals:" in leads[0].pcb_need_reason
    assert "sensor" in leads[0].pcb_need_reason
    assert stats.hardware_qualified == 1


def test_find_daily_leads_rejects_software_company_name_even_with_hardware_products(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Acme Software Company Ltd",
        "https://acme-software.example",
        "We manufacture industrial sensor controllers.",
        "Germany automation contact",
    )

    class SoftwareCompanyFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": (
                            "<html><body>Acme Software Company Ltd. "
                            "We manufacture industrial sensor controllers. "
                            "sales@acme-software.example</body></html>"
                        ),
                    },
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Automation Equipment",
        1,
        fetcher=SoftwareCompanyFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.non_hardware_filtered == 1


def test_find_daily_leads_rejects_direct_email_without_fetched_official_page(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    result = SearchResult(
        "Search Result Title",
        "https://acme-controls.de",
        "Industrial sensor controller manufacturer.",
        "Germany automation contact",
    )
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    monkeypatch.setattr(
        lead_finder,
        "DIRECT_PUBLIC_EMAILS",
        {"acme-controls.de": ("sales@acme-controls.de", "Generic Company Email", "https://acme-controls.de/contact")},
    )

    leads = find_daily_leads(
        manager,
        "Germany",
        "Automation Equipment",
        1,
        fetcher=NoEmailFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
    )

    assert leads == []


def test_find_daily_leads_rejects_cached_email_without_fetched_official_page(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record(
        "acme-controls.de",
        "found",
        "https://acme-controls.de/contact",
        "sales@acme-controls.de",
        "Generic Company Email",
    )
    result = SearchResult(
        "Search Result Title",
        "https://acme-controls.de",
        "Industrial sensor controller manufacturer.",
        "Germany automation contact",
    )
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])

    leads = find_daily_leads(
        manager,
        "Germany",
        "Automation Equipment",
        1,
        fetcher=NoEmailFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
    )

    assert leads == []


def test_find_daily_leads_uses_official_hostname_not_search_result_title(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    result = SearchResult(
        "Search Results: ACME Consulting",
        "https://acme-controls.de",
        "Search listing for automation providers.",
        "Germany automation contact",
    )

    class OfficialAcmeFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>ACME Controls builds industrial sensors and embedded controllers. sales@acme-controls.de</body></html>",
                    },
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Automation Equipment",
        1,
        fetcher=OfficialAcmeFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
    )

    assert [lead.company_name for lead in leads] == ["Acme Controls"]


def test_find_daily_leads_rejects_globalgrasshopper_travel_content(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Global Grasshopper",
        "https://globalgrasshopper.com",
        "Travel blog tourism affiliate guide.",
        "Germany sensor controller manufacturer",
    )

    class TravelFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>Travel tourism affiliate advice. contact@globalgrasshopper.com</body></html>",
                    },
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor Manufacturer",
        1,
        fetcher=TravelFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.company_name_failures == 1


def test_find_daily_leads_counts_unresolved_generic_company_name(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Contact",
        "https://globalgrasshopper.com",
        "Travel blog tourism affiliate guide.",
        "Germany sensor controller manufacturer",
    )

    class GenericTravelFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>Travel tourism affiliate advice. contact@globalgrasshopper.com</body></html>",
                    },
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor Manufacturer",
        1,
        fetcher=GenericTravelFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.company_name_failures == 1
    assert stats.skip_reasons["company name unresolved"] == 1


def test_find_daily_leads_does_not_make_directory_operator_a_lead(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Industry Directory",
        "https://www.industrystock.com/company",
        "Industrial sensors and embedded controllers. sales@industrystock.com",
        "Germany sensor company contact",
    )

    class DirectoryOperatorFetcher:
        def fetch(self, url):
            if url == result.url:
                return type("Page", (), {"url": url, "html": "<html><body>Directory operator contact page.</body></html>"})()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor Manufacturer",
        1,
        fetcher=DirectoryOperatorFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.skip_reasons["directory resolution failed"] == 1


def test_find_daily_leads_requires_two_official_hardware_signals(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Acme Controls",
        "https://acme-controls.example",
        "Sensor products.",
        "Germany industrial embedded controller manufacturer",
    )

    class SingleSignalFetcher:
        def fetch(self, url):
            if url.endswith("/contact"):
                return type(
                    "Page",
                    (),
                    {"url": url, "html": "<html><body>Sensor product enquiries: sales@acme-controls.example</body></html>"},
                )()
            return None

    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])
    leads = find_daily_leads(
        manager,
        "Germany",
        "Industrial Electronics",
        1,
        fetcher=SingleSignalFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert leads == []
    assert stats.non_hardware_filtered == 1
    assert stats.skip_reasons["non-hardware website"] == 1


def test_find_daily_leads_combines_result_root_and_contact_evidence_within_shared_cap(
    tmp_path,
    monkeypatch,
):
    from main import RunPageFetchBudget

    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    stats = DiscoveryStats()
    result = SearchResult(
        "Acme Sensor Systems Contact",
        "https://acme-evidence.example/contact",
        "Official support for Acme sensor products.",
        "Germany industrial electronics manufacturer",
    )

    class EvidenceFetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            if url == "https://acme-evidence.example":
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>Acme Evidence GmbH manufactures controller products.</body></html>",
                    },
                )()
            if url == "https://acme-evidence.example/contact":
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": "<html><body>Sales: sales@acme-evidence.example</body></html>",
                    },
                )()
            return None

    fetcher = EvidenceFetcher()
    bounded_fetcher = RunPageFetchBudget(fetcher, max_pages_per_domain=5)
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])

    leads = find_daily_leads(
        manager,
        "Germany",
        "Industrial Electronics",
        1,
        fetcher=bounded_fetcher,
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        stats=stats,
    )

    assert [lead.email for lead in leads] == ["sales@acme-evidence.example"]
    assert "sensor" in leads[0].pcb_need_reason
    assert "controller" in leads[0].pcb_need_reason
    assert fetcher.calls[0] == "https://acme-evidence.example"
    assert "https://acme-evidence.example/contact" in fetcher.calls
    assert len(fetcher.calls) <= 5
    assert all(
        url in {
            "https://acme-evidence.example",
            "https://acme-evidence.example/impressum",
            "https://acme-evidence.example/kontakt",
            "https://acme-evidence.example/legal-notice",
            "https://acme-evidence.example/contact",
        }
        for url in fetcher.calls
    )
    assert stats.hardware_qualified == 1


def test_discovery_stats_summary_surfaces_provider_attempts_and_failures():
    stats = DiscoveryStats(provider_attempts=12, provider_failures=2)

    assert "provider attempts=12" in stats.summary()
    assert "provider failures=2" in stats.summary()


def test_contact_cache_skips_recent_no_result_from_the_same_normalized_source(tmp_path):
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record(
        "acme.example",
        "not_found",
        "https://acme.example/contact/",
        "",
        "",
        source_type="Contact",
        now=datetime(2026, 7, 14, 12, 0, 0),
    )

    cached = cache.lookup(
        "acme.example",
        source_type=" contact ",
        source_url="https://ACME.example/contact#team",
        now=datetime(2026, 7, 15, 12, 0, 0),
    )

    assert cached is not None
    assert cached.outcome == "not_found"
    assert cache.lookup(
        "acme.example",
        source_type="Contact",
        source_url="https://acme.example/contact",
        now=datetime(2026, 7, 28, 12, 0, 0),
    ) is None


def test_contact_cache_retries_recent_no_result_from_new_contact_impressum_and_pdf_sources(tmp_path):
    cache = ContactCache(tmp_path / "contacts.json")
    now = datetime(2026, 7, 15, 12, 0, 0)
    cache.record(
        "acme.example",
        "not_found",
        "https://acme.example/contact",
        "",
        "",
        source_type="Contact",
        now=now,
    )

    for source_type, source_url in [
        ("Impressum", "https://acme.example/impressum"),
        ("PDF", "https://acme.example/catalog.pdf"),
        ("Directory", "https://directory.example/acme"),
    ]:
        assert cache.lookup(
            "acme.example",
            source_type=source_type,
            source_url=source_url,
            now=now,
        ) is None


def test_contact_cache_reads_legacy_found_contact_and_preserves_it_during_lazy_migration(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps(
            {
                "acme.example": {
                    "outcome": "found",
                    "source_url": "https://acme.example/contact",
                    "email": "sales@acme.example",
                    "email_type": "Generic Company Email",
                    "updated_at": "2026-07-14T12:00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    cache = ContactCache(path)

    cached = cache.lookup("acme.example", source_type="Contact", source_url="https://acme.example/contact")
    cache.record(
        "acme.example",
        "not_found",
        "https://acme.example/impressum",
        "",
        "",
        source_type="Impressum",
        now=datetime(2026, 7, 15, 12, 0, 0),
    )

    assert cached is not None
    assert cached.email == "sales@acme.example"
    assert cache.lookup("acme.example").email == "sales@acme.example"
    migrated = json.loads(path.read_text(encoding="utf-8"))["acme.example"]
    assert migrated["found"]["email"] == "sales@acme.example"
    assert len(migrated["not_found"]) == 1


def test_find_daily_leads_retries_legacy_no_result_from_new_contact_source(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record("acme-sensors.example", "not_found", "", "", "")
    result = SearchResult(
        "ACME Sensors",
        "https://acme-sensors.example/contact",
        "Industrial sensor hardware and embedded electronics.",
        "Germany sensor contact",
    )
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])

    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
    )

    assert [lead.email for lead in leads] == ["info@acme-sensors.example"]


def test_find_daily_leads_relaxes_no_result_cache_but_keeps_found_contacts_reusable(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record("acme-sensors.example", "not_found", "", "", "")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [acme_result()])

    leads = find_daily_leads(
        manager,
        "Australia",
        "Instrumentation",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
        relax_not_found_cache=True,
    )

    assert [lead.email for lead in leads] == ["info@acme-sensors.example"]
    cached = cache.lookup("acme-sensors.example")
    assert cached is not None
    assert cached.outcome == "found"


def test_find_daily_leads_never_caches_search_domain_as_target_company(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    result = SearchResult(
        "Industrial Sensors",
        "https://www.google.com/search?q=industrial+sensors",
        "Industrial sensor hardware and embedded electronics.",
        "Germany sensor contact",
    )
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [result])

    find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=NoEmailFetcher(),
        contact_cache=cache,
        search_provider=EmptySearchProvider(),
    )

    assert cache.lookup("google.com") is None


def test_email_first_snippet_bypasses_old_no_email_cache(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record("acme-sensors.example", "not_found", "", "", "")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    class SnippetEmailProvider:
        def search(self, query, limit):
            return [
                SearchResult(
                    "ACME Sensors",
                    "https://acme-sensors.example/contact",
                    "Industrial sensor electronics sales@acme-sensors.example",
                    query,
                )
            ][:limit]

    class SnippetEvidenceFetcher:
        def fetch(self, url):
            if url == "https://acme-sensors.example/contact":
                return type(
                    "Page",
                    (),
                    {"url": url, "html": "<html><body>Industrial sensors use embedded controllers and electronics.</body></html>"},
                )()
            return None

    leads = find_daily_leads(
        manager,
        "Germany",
        "Sensor",
        1,
        fetcher=SnippetEvidenceFetcher(),
        contact_cache=cache,
        search_provider=SnippetEmailProvider(),
    )

    assert [lead.email for lead in leads] == ["sales@acme-sensors.example"]


def test_available_candidate_seed_ignores_existing_domains(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    leads = manager.sheet("Leads")
    leads.append([1, "2026-07-14", "Australia", "", "Existing", "https://existing.example"])
    monkeypatch.setattr(
        lead_finder,
        "seed_results_for",
        lambda *_: [
            SearchResult("Existing", "https://existing.example", "hardware", "seed"),
            SearchResult("New", "https://new.example", "hardware", "seed"),
        ],
    )

    assert has_available_candidate_seeds(manager, "Australia", "Instrumentation", 1) is True


def test_available_candidate_seed_ignores_cached_email_already_in_workbook(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    outreach = manager.sheet("Outreach_Email_Drafts")
    headers = manager.headers(outreach)
    row = [""] * len(headers)
    row[headers.index("Company Name")] = "Previously Contacted"
    row[headers.index("Contact Email")] = "used@example.com"
    outreach.append(row)
    cache = ContactCache(tmp_path / "contacts.json")
    cache.record("new.example", "found", "https://new.example/contact", "used@example.com", "Generic Company Email")
    monkeypatch.setattr(
        lead_finder,
        "seed_results_for",
        lambda *_: [SearchResult("New", "https://new.example", "hardware", "seed")],
    )

    assert has_available_candidate_seeds(manager, "Germany", "Instrumentation", 1, contact_cache=cache) is False


def test_directory_sources_keep_fallback_combination_searchable_when_seeds_are_exhausted(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    assert has_available_search_sources(manager, "Germany", "Instrumentation", 10) is True
    assert has_available_search_sources(manager, "Russia", "Instrumentation", 10) is False


def test_primary_discovery_query_uses_email_first_contact_intent():
    query = primary_discovery_query("USA", "Vintage Camera Electronics")

    assert "USA" in query
    assert "Vintage Camera Electronics" in query
    assert query.endswith('"contact"')


def test_formal_keyword_state_records_search_outcome(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    outcomes = []
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [acme_result()])
    monkeypatch.setattr(
        lead_finder,
        "record_search_outcome",
        lambda found_count, **kwargs: outcomes.append((found_count, kwargs)) or {},
    )
    monkeypatch.setattr(lead_finder, "learn_public_terms", lambda *args, **kwargs: [])
    leads = find_daily_leads(
        manager,
        "Australia",
        "Instrumentation",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
        keyword_state_updates=True,
        keyword_performance=KeywordPerformanceStore(tmp_path / "keyword_performance.json"),
    )

    assert len(leads) == 1
    assert outcomes == [(1, {"industry": "Instrumentation", "product": "Instrumentation"})]


def test_find_daily_leads_records_domain_first_query_performance(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    cache = ContactCache(tmp_path / "contacts.json")
    performance = KeywordPerformanceStore(tmp_path / "keyword_performance.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    stats = DiscoveryStats()
    leads = find_daily_leads(
        manager,
        "Germany",
        "Industrial Electronics",
        1,
        fetcher=FakeFetcher(),
        contact_cache=cache,
        search_provider=PublicDiscoverySearchProvider(),
        stats=stats,
        keyword_performance=performance,
    )

    assert len(leads) == 1
    assert 0 < stats.search_queries <= 12
    assert stats.email_candidates == 1
    top = performance.top_queries(1)[0]
    assert top["email_count"] == 1
    assert top["new_lead_count"] == 1
    assert f"search queries={stats.search_queries}" in stats.summary()


def test_find_daily_leads_runs_direct_queries_before_productive_thomasnet_history(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    performance = KeywordPerformanceStore(tmp_path / "keyword_performance.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    thomasnet_query = next(
        item.query
        for item in prospect_queries("USA", "Sensor")
        if item.source_name == "Thomasnet"
    )
    performance.record(thomasnet_query, "Thomasnet", candidates=8, emails=4, new_leads=3)

    class OrderedSearchProvider:
        def __init__(self):
            self.directory_sources = []

        def search(self, query, limit):
            return []

        def search_source(self, query, limit, source_name):
            self.directory_sources.append(source_name)
            return []

    provider = OrderedSearchProvider()

    assert find_daily_leads(
        manager,
        "USA",
        "Sensor",
        1,
        fetcher=NoEmailFetcher(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=provider,
        keyword_performance=performance,
    ) == []

    assert provider.directory_sources[0] == "Direct Manufacturers"


def test_keyword_metrics_count_accepted_domains_and_hardware_without_proxy_leads(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    performance = KeywordPerformanceStore(tmp_path / "keyword_performance.json")
    monkeypatch.setattr(lead_finder, "seed_results_for", lambda *_: [])

    class RepeatedHardwareProvider:
        def search(self, query, limit):
            return [
                SearchResult(
                    "No Email Instruments",
                    "https://no-email.example/products",
                    "Industrial measurement electronics and embedded controllers",
                    query,
                )
            ][:limit]

    class HardwareNoEmailFetcher:
        def fetch(self, url):
            if url == "https://no-email.example/products":
                return type(
                    "Page",
                    (),
                    {
                        "url": url,
                        "html": (
                            "<html><title>No Email Instruments</title><body>We manufacture industrial "
                            "electronic measurement instruments, sensor controllers, embedded devices, "
                            "and hardware equipment.</body></html>"
                        ),
                    },
                )()
            return None

    leads = find_daily_leads(
        manager,
        "Germany",
        "Industrial Electronics",
        2,
        fetcher=HardwareNoEmailFetcher(),
        contact_cache=ContactCache(tmp_path / "contacts.json"),
        search_provider=RepeatedHardwareProvider(),
        keyword_performance=performance,
    )

    records = json.loads(performance.path.read_text())["queries"].values()
    assert leads == []
    assert sum(record["candidate_count"] for record in records) > 1
    assert sum(record["new_domain_count"] for record in records) == 1
    assert sum(record["qualified_hardware_count"] for record in records) == 1
