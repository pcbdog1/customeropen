from datetime import datetime
from hashlib import sha256

from openpyxl import Workbook, load_workbook

from config import HIGH_YIELD_INDUSTRIES, PRIMARY_COUNTRIES, LoopConfig, LoopState, dynamic_limit_for_day
import main as main_module
from main import RunPageFetchBudget, RunSearchBudget, cycle_send_capacity
import email_sender
from email_sender import select_auto_send_rows, send_auto_eligible_emails
from email_sender_common import OPT_OUT_SENTENCE
from excel_manager import OUTREACH_HEADERS, OUTREACH_SHEET, SENT_LOG_HEADERS, SENT_LOG_SHEET, ExcelManager
from daily_report import DailyReportData, build_daily_report_text
from pcb_leads.excel_store import HEADERS as LEAD_HEADERS
from pcb_leads.models import Lead
from pcb_leads.search import SearchResult


def test_loop_state_advances_industry_before_country():
    cfg = LoopConfig()
    state = LoopState(country_index=0, industry_index=0)
    next_state = state.next(cfg)
    assert cfg.countries[next_state.country_index] == "Germany"
    assert cfg.industries[next_state.industry_index] == "Test and Measurement Equipment"


def test_loop_state_wraps_country_after_last_industry():
    cfg = LoopConfig()
    state = LoopState(country_index=0, industry_index=len(cfg.industries) - 1)
    next_state = state.next(cfg)
    assert cfg.countries[next_state.country_index] == "UK"
    assert cfg.industries[next_state.industry_index] == "Industrial Electronics"


def test_dynamic_limit_steps_up_by_running_day():
    assert dynamic_limit_for_day(1) == 20
    assert dynamic_limit_for_day(8) == 50
    assert dynamic_limit_for_day(15) == 100


def test_primary_cycle_contains_only_approved_europe_and_north_america():
    cfg = LoopConfig()

    assert cfg.countries == list(PRIMARY_COUNTRIES)
    assert cfg.countries == [
        "Germany", "UK", "USA", "Netherlands", "Switzerland", "Sweden", "France", "Italy",
        "Spain", "Canada", "Austria", "Belgium", "Denmark", "Finland", "Norway", "Ireland",
    ]
    assert {"Argentina", "Brazil", "India", "Turkey", "Australia", "Singapore", "Vietnam"}.isdisjoint(cfg.countries)


def test_high_yield_industries_are_first_without_duplicates():
    industries = LoopConfig().industries

    assert industries[: len(HIGH_YIELD_INDUSTRIES)] == list(HIGH_YIELD_INDUSTRIES)
    assert len({industry.casefold() for industry in industries}) == len(industries)


class RecordingSearchProvider:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        return self.results(query)[:limit]


def test_search_budget_is_global_and_stops_at_send_target():
    provider = RecordingSearchProvider(lambda query: [])
    budget = RunSearchBudget(provider, max_queries=3, send_target=10)
    target_budget = RunSearchBudget(provider, max_queries=3, send_target=10)

    assert target_budget.can_search(sent_success=9)
    assert not target_budget.can_search(sent_success=10)

    for _ in range(4):
        budget.search("Germany sensor manufacturer", 10)

    assert budget.query_count == 3
    assert len(provider.calls) == 3
    assert not budget.can_search(sent_success=9)


def test_search_budget_caps_override_target_at_ten_confirmed_sends():
    provider = RecordingSearchProvider(lambda query: [])
    budget = RunSearchBudget(provider, max_queries=300, send_target=25)

    assert budget.send_target == 10
    assert budget.can_search(sent_success=9)
    assert not budget.can_search(sent_success=10)


def test_search_budget_shares_hard_300_attempt_cap_with_multi_provider_chain():
    from pcb_leads.search import FallbackSearchProvider, MultiSearchProvider, SearchProvider

    class CountingEmptyProvider(SearchProvider):
        def __init__(self):
            self.calls = 0

        def search(self, query, limit):
            self.calls += 1
            return []

    providers = [CountingEmptyProvider(), CountingEmptyProvider()]
    multi = MultiSearchProvider(providers)
    budget = RunSearchBudget(FallbackSearchProvider(multi), max_queries=500)

    for _ in range(500):
        budget.search("industrial sensor manufacturer", 5)

    assert budget.max_queries == 300
    assert budget.query_count <= 300
    assert budget.provider_attempts == 300
    assert budget.provider_failures == 0
    assert sum(provider.calls for provider in providers) == 300


def test_effective_cycle_send_target_caps_live_and_dry_run_overrides():
    assert main_module.effective_cycle_send_target(None) == 10
    assert main_module.effective_cycle_send_target(4) == 4
    assert main_module.effective_cycle_send_target(25) == 10


def test_keyword_report_recommends_next_distinct_non_paused_ranked_query(tmp_path):
    performance = main_module.KeywordPerformanceStore(tmp_path / "performance.json")
    performance.record("top active", "Directory", 5, 4, 2, sent=2, qualified_hardware=3)
    performance.record("paused historical", "Email-First", 5, 4, 2, sent=1, qualified_hardware=2)
    for _ in range(3):
        performance.record("paused historical", "Email-First", 1, 0, 0)
    performance.record("next active", "Manufacturer", 5, 2, 1, qualified_hardware=2)

    source, top_keyword, next_keyword = main_module.keyword_report_values(performance)

    assert source == "Directory"
    assert top_keyword == "top active"
    assert next_keyword == "next active"


def test_keyword_report_skips_casing_and_whitespace_variant_of_top_query(tmp_path):
    performance = main_module.KeywordPerformanceStore(tmp_path / "performance.json")
    performance.record("Top Hardware", "Directory", 5, 4, 2, sent=3, qualified_hardware=3)
    performance.record("  top hardware  ", "Manufacturer", 5, 4, 2, sent=2, qualified_hardware=3)
    performance.record("next active", "Email-First", 5, 2, 1, sent=1, qualified_hardware=2)

    source, top_keyword, next_keyword = main_module.keyword_report_values(performance)

    assert source == "Directory"
    assert top_keyword == "Top Hardware"
    assert next_keyword == "next active"


def test_keyword_report_uses_deterministic_fallback_without_distinct_active_query(tmp_path):
    performance = main_module.KeywordPerformanceStore(tmp_path / "performance.json")
    performance.record("only active", "Directory", 5, 2, 1, qualified_hardware=2)
    for _ in range(3):
        performance.record("paused", "Email-First", 1, 0, 0)

    assert main_module.keyword_report_values(performance) == ("Directory", "only active", "无")


def test_search_budget_uses_one_quota_for_generic_source_across_query_variants():
    def results(query):
        country = query.split()[0].casefold()
        return [SearchResult(country, f"https://{country}.example", "hardware", query)]

    budget = RunSearchBudget(RecordingSearchProvider(results), max_queries=10, max_domains_per_source=2)

    accepted = []
    for query in (
        "Germany sensor exhibitor manufacturer",
        "Austria automation exhibitor manufacturer",
        "Belgium electronics exhibitor manufacturer",
    ):
        result = budget.search_source(query, 1, "Exhibitor Lists")[0]
        accepted.append(
            budget.accept_company_domain(
                result.url,
                query=query,
                source_key="Exhibitor Lists",
            )
        )

    assert accepted == [True, True, False]


def test_search_budget_uses_site_domain_quota_across_query_variants():
    budget = RunSearchBudget(RecordingSearchProvider(lambda query: []), max_queries=10, max_domains_per_source=2)

    accepted = [
        budget.accept_company_domain(
            f"https://company-{index}.example",
            query=query,
            source_key=source_key,
        )
        for index, (query, source_key) in enumerate(
            [
                ("site:industrystock.com Germany sensor manufacturer", "IndustryStock"),
                ("site:industrystock.com Austria automation supplier", "IndustryStock Europe"),
                ("site:industrystock.com Belgium electronics company", "Industrial Directory"),
            ]
        )
    ]

    assert accepted == [True, True, False]


def test_search_budget_inspects_each_company_domain_once_per_run():
    def results(query):
        suffix = "contact" if "contact" in query else "products"
        return [
            SearchResult("ACME", f"https://acme.example/{suffix}", "sensor hardware", query),
            SearchResult("Beta", "https://beta.example", "controller hardware", query),
        ]

    budget = RunSearchBudget(RecordingSearchProvider(results), max_queries=10)

    first = budget.search_source("Germany sensor manufacturer", 10, "Manufacturer Directories")
    second = budget.search_source("Germany sensor contact", 10, "Manufacturer Directories")

    accepted = [
        budget.accept_company_domain(result.url, query=result.query, source_key="Manufacturer Directories")
        for result in [*first, *second]
    ]

    assert accepted == [True, True, False, False]


def test_search_budget_expands_domain_email_lookup_intents_within_global_ceiling():
    provider = RecordingSearchProvider(lambda query: [])
    budget = RunSearchBudget(provider, max_queries=5)

    budget.search('"acme.example" email contact', 5)

    assert budget.query_count == 5
    assert len(provider.calls) == 5
    assert all("acme.example" in query for query, _ in provider.calls)


def test_page_fetch_budget_caches_urls_and_fetches_at_most_five_pages_per_domain():
    class Fetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            return type("Page", (), {"url": url, "html": url})()

    fetcher = Fetcher()
    budget = RunPageFetchBudget(fetcher, max_pages_per_domain=5)

    pages = [budget.fetch(f"https://acme.example/page-{index}") for index in range(6)]
    cached = budget.fetch("https://acme.example/page-0")

    assert sum(page is not None for page in pages) == 5
    assert len(fetcher.calls) == 5
    assert cached is pages[0]


def test_page_fetch_budget_limits_directory_listing_hosts_to_five_pages():
    class Fetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            return type("Page", (), {"url": url, "html": url})()

    fetcher = Fetcher()
    budget = RunPageFetchBudget(fetcher, max_pages_per_domain=5)

    pages = [
        budget.fetch(f"https://www.industrystock.com/en/company/company-{index}")
        for index in range(6)
    ]

    assert sum(page is not None for page in pages) == 5
    assert len(fetcher.calls) == 5


def test_global_cycle_includes_extended_hardware_industries():
    industries = set(LoopConfig().industries)

    assert {"Instrumentation", "Laboratory Equipment", "RF / Telecom", "Motor Drives"}.issubset(industries)


def test_global_cycle_loads_external_keyword_library_industries():
    industries = set(LoopConfig().industries)

    assert "Neurotechnology and Brain Computer Interface" in industries
    assert "Defense and High Reliability Electronics" in industries
    assert "Consumer XR Gaming and Specialty Electronics" in industries


def test_cycle_send_capacity_is_ten_even_when_more_drafts_are_pending():
    assert cycle_send_capacity(pending_count=25, sent_success=0) == 10


def test_cycle_send_capacity_uses_only_remaining_target():
    assert cycle_send_capacity(pending_count=25, sent_success=7) == 3


def test_fallback_search_order_starts_with_high_yield_tier_despite_persisted_cursor():
    config = LoopConfig()
    persisted = LoopState(
        country_index=config.countries.index("Ireland"),
        industry_index=len(HIGH_YIELD_INDUSTRIES) + 5,
    )

    states = list(main_module.fallback_search_states(config, persisted))
    industries = [state.current_industry(config) for state in states]
    first_broad = next(index for index, industry in enumerate(industries) if industry not in HIGH_YIELD_INDUSTRIES)

    assert industries[0] == HIGH_YIELD_INDUSTRIES[0]
    assert set(industries[:first_broad]) == set(HIGH_YIELD_INDUSTRIES)


def test_fallback_search_first_28_diagonally_cover_priority_countries_and_industries():
    config = LoopConfig()
    persisted = LoopState(country_index=0, industry_index=0)

    first_run = list(main_module.fallback_search_states(config, persisted))[:28]
    second_run = list(main_module.fallback_search_states(config, persisted))[:28]
    combinations = [
        (state.current_country(config), state.current_industry(config))
        for state in first_run
    ]

    assert combinations == [
        (state.current_country(config), state.current_industry(config))
        for state in second_run
    ]
    assert (persisted.current_country(config), persisted.current_industry(config)) not in combinations
    assert set(config.countries[:6]).issubset(country for country, _ in combinations)
    assert set(HIGH_YIELD_INDUSTRIES[:6]).issubset(industry for _, industry in combinations)


def test_dry_run_fallback_stops_at_ten_eligible_without_formal_writes(tmp_path, monkeypatch):
    formal_workbook = tmp_path / "formal.xlsx"
    workbook = Workbook()
    leads = workbook.active
    leads.title = "Leads"
    leads.append(LEAD_HEADERS)
    outreach = workbook.create_sheet(OUTREACH_SHEET)
    outreach.append(OUTREACH_HEADERS)
    preexisting_body = " ".join(["message"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    for index in range(2):
        preexisting = {
            "Company Name": f"Existing Devices {index}",
            "Contact Email": f"sales@existing-{index}.example",
            "Email Type": "Generic Company Email",
            "Subject": "Existing hardware project",
            "Email Body": preexisting_body,
            "Why This Angle Fits": "Official hardware evidence. hardware signals: sensor, controller.",
            "Source URL": f"https://existing-{index}.example/contact",
            "Compliance Note": "Public business contact from the official company website.",
            "Lead Score": 4,
            "Company Domain": f"existing-{index}.example",
            "Country": "Germany",
            "Industry": "Industrial Electronics",
        }
        outreach.append([preexisting.get(header, "") for header in OUTREACH_HEADERS])
    sent_log = workbook.create_sheet(SENT_LOG_SHEET)
    sent_log.append(SENT_LOG_HEADERS)
    workbook.save(formal_workbook)

    formal_state = {
        "cache": tmp_path / "contact_cache.json",
        "performance": tmp_path / "keyword_performance.json",
        "loop": tmp_path / "loop_state.json",
        "checkpoint": tmp_path / "daily_loop_checkpoint.json",
        "summary": tmp_path / "run_summary.json",
        "report": tmp_path / "reports" / f"daily_report_{datetime.now():%Y%m%d}.txt",
    }
    formal_state["cache"].write_text('{"formal": "cache"}\n', encoding="utf-8")
    formal_state["performance"].write_text('{"formal": "performance"}\n', encoding="utf-8")
    formal_state["loop"].write_text(
        '{"country_index": 0, "industry_index": 0, "formal": "loop"}\n',
        encoding="utf-8",
    )
    formal_state["checkpoint"].write_text('{"formal": "checkpoint"}\n', encoding="utf-8")
    formal_state["summary"].write_text('{"formal": "summary"}\n', encoding="utf-8")
    formal_state["report"].parent.mkdir()
    formal_state["report"].write_text("formal report\n", encoding="utf-8")
    formal_before = {
        "workbook": sha256(formal_workbook.read_bytes()).hexdigest(),
        **{name: sha256(path.read_bytes()).hexdigest() for name, path in formal_state.items()},
    }

    countries = ["Germany", "UK", "USA", "Netherlands", "Switzerland"]
    config = LoopConfig(
        workbook_path=formal_workbook,
        domain_pool_path=tmp_path / "company_domain_pool.xlsx",
        loop_state_path=formal_state["loop"],
        report_dir=formal_state["report"].parent,
        countries=countries,
        industries=["Industrial Electronics"],
        max_search_queries=300,
        max_fallback_combinations=60,
    )

    class FakeLock:
        recovered_from_stale_lock = False

        def heartbeat(self, stage=""):
            return None

        def release(self):
            return None

    class FakeSearchProvider:
        configured = False

        def search(self, query, limit):
            return []

        def usage_summary(self):
            return {}

    class FakeFetcher:
        def fetch(self, url):
            return None

    calls = []

    def eligible_lead(index, country, industry):
        domain = f"qualified-{index}.example"
        return Lead(
            country=country,
            city="Berlin",
            company_name=f"Qualified Devices {index}",
            website=f"https://{domain}",
            business="Industrial sensor and embedded controller manufacturer",
            industry=industry,
            pcb_need_reason="hardware signals: sensor, controller.",
            demand_signal="Public company website",
            demand_signal_source=f"{country} {industry} manufacturer email",
            employees="50",
            founded="2010",
            email=f"sales@{domain}",
            email_type="Generic Company Email",
            contact_method="Official Company Page",
            contact_form_url="",
            contact_name="Not Found",
            contact_title="Not Found",
            phone="Not Found",
            linkedin="Not Found",
            source_links=f"https://{domain}/contact",
            score=4,
            development_angle="Industrial sensor controller electronics",
            email_subject="Industrial sensor PCBA support",
            notes="",
            compliance_note="Public business contact from the official company website.",
        )

    def fake_find_daily_leads(manager, country, industry, limit, **kwargs):
        calls.append(
            {
                "country": country,
                "industry": industry,
                "search_provider": kwargs["search_provider"],
                "fetcher": kwargs["fetcher"],
                "keyword_state_updates": kwargs["keyword_state_updates"],
            }
        )
        kwargs["search_provider"].query_count += 10
        if len(calls) == 1:
            return []
        start = sum(len(call.get("leads", [])) for call in calls)
        batch = [eligible_lead(start + offset, country, industry) for offset in range(min(3, limit))]
        calls[-1]["leads"] = batch
        return batch

    monkeypatch.setattr(main_module, "LoopConfig", lambda: config)
    monkeypatch.setattr(main_module.RunLock, "acquire", lambda **_kwargs: FakeLock())
    monkeypatch.setattr(main_module, "CHECKPOINT_PATH", formal_state["checkpoint"])
    monkeypatch.setattr(main_module, "RUN_SUMMARY_PATH", formal_state["summary"])
    monkeypatch.setattr(main_module, "CONTACT_CACHE_PATH", formal_state["cache"])
    monkeypatch.setattr(main_module, "KEYWORD_PERFORMANCE_PATH", formal_state["performance"])
    monkeypatch.setattr(main_module, "public_discovery_search_provider", FakeSearchProvider)
    monkeypatch.setattr(main_module, "StableSearchProvider", FakeSearchProvider)
    monkeypatch.setattr(main_module, "public_contact_fetcher", FakeFetcher)
    monkeypatch.setattr(main_module, "has_available_search_sources", lambda *args: True)
    monkeypatch.setattr(main_module, "find_daily_leads", fake_find_daily_leads)
    monkeypatch.setattr(
        email_sender.RealEmailSender,
        "send",
        lambda self, item: (_ for _ in ()).throw(AssertionError("dry-run called live sender")),
    )

    result = main_module.run_daily_loop(
        dry_run=True,
        force_country="Germany",
        force_industry="Industrial Electronics",
        lead_limit_override=20,
    )

    # With no local pool records and no configured official API, the revised
    # architecture must finish without falling back to public HTML search.
    assert calls == []
    assert result["auto_eligible_pending"] == 2
    assert result["dry_run_new_auto_eligible"] == 0
    assert result["sent_success"] == 0
    assert result["search_queries"] == 0
    assert result["provider_attempts"] == 0
    assert result["provider_failures"] == 0

    sandbox_workbook = load_workbook(result["workbook"])
    sandbox_outreach = sandbox_workbook[OUTREACH_SHEET]
    sandbox_headers = [cell.value for cell in sandbox_outreach[1]]
    status_col = sandbox_headers.index("Send Status") + 1
    eligible_col = sandbox_headers.index("Auto Send Eligible") + 1
    email_col = sandbox_headers.index("Contact Email") + 1
    domain_col = sandbox_headers.index("Company Domain") + 1
    assert sum(
        1
        for row in range(2, sandbox_outreach.max_row + 1)
        if sandbox_outreach.cell(row, eligible_col).value == "YES"
    ) == 2
    new_identities = {
        (
            str(sandbox_outreach.cell(row, email_col).value or "").strip().casefold(),
            str(sandbox_outreach.cell(row, domain_col).value or "").strip().casefold(),
        )
        for row in range(4, sandbox_outreach.max_row + 1)
        if sandbox_outreach.cell(row, eligible_col).value == "YES"
    }
    assert len(new_identities) == 0
    assert all(
        not sandbox_outreach.cell(row, status_col).value
        for row in range(2, sandbox_outreach.max_row + 1)
    )
    assert sandbox_workbook[SENT_LOG_SHEET].max_row == 1
    assert sha256(formal_workbook.read_bytes()).hexdigest() == formal_before["workbook"]
    for name, path in formal_state.items():
        assert sha256(path.read_bytes()).hexdigest() == formal_before[name]


def test_dry_run_sender_reports_selection_without_confirmed_send(tmp_path, monkeypatch):
    path = tmp_path / "book.xlsx"
    workbook = Workbook()
    workbook.active.title = "Leads"
    outreach = workbook.create_sheet(OUTREACH_SHEET)
    outreach.append(OUTREACH_HEADERS)
    body = " ".join(["message"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    draft = {
        "Company Name": "Acme Sensors",
        "Contact Email": "sales@acme.example",
        "Email Type": "Generic Company Email",
        "Subject": "Acme sensor controller projects",
        "Email Body": body,
        "Why This Angle Fits": "Official hardware evidence. hardware signals: sensor, controller.",
        "Source URL": "https://acme.example/contact",
        "Compliance Note": "Public business contact from the official company website.",
        "Lead Score": 4,
        "Company Domain": "acme.example",
        "Country": "Germany",
        "Industry": "Sensor",
    }
    outreach.append([draft.get(header, "") for header in OUTREACH_HEADERS])
    sent_log = workbook.create_sheet(SENT_LOG_SHEET)
    sent_log.append(SENT_LOG_HEADERS)
    workbook.save(path)
    monkeypatch.setattr(
        email_sender.RealEmailSender,
        "send",
        lambda self, item: (_ for _ in ()).throw(AssertionError("dry-run called live sender")),
    )

    stats = send_auto_eligible_emails(ExcelManager(path), 1, 0, 0, dry_run=True)

    assert stats.selected == 1
    assert stats.attempted == stats.sent == stats.failed == 0
    assert stats.sent_items == []
    reloaded = load_workbook(path)
    assert not reloaded[OUTREACH_SHEET].cell(2, OUTREACH_HEADERS.index("Send Status") + 1).value
    assert reloaded[SENT_LOG_SHEET].max_row == 1


def test_select_auto_send_rows_rejects_private_and_skips_dnc_sent_company_and_domain(tmp_path):
    path = tmp_path / "book.xlsx"
    wb = Workbook()
    leads = wb.active
    leads.title = "Leads"
    leads.append(["公司名称", "公司官网"])
    ws = wb.create_sheet(OUTREACH_SHEET)
    ws.append(OUTREACH_HEADERS)
    body = " ".join(["word"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    angle = "Official product evidence. hardware signals: robotics, controller."
    ws.append(["A", "sales@a.example", "Generic Company Email", "s", body, angle, "", "", "", "", "", "https://a.example", "note", "", 4, "a.example", "Germany", "Robotics", 130, "Robotics PCBA"])
    ws.append(["B", "b@yahoo.com", "Private Email", "s", body, angle, "", "", "", "", "", "https://b.example", "note", "", 4, "b.example", "Germany", "Robotics", 130, "Robotics PCBA"])
    ws.append(["C", "sales@c.example", "Generic Company Email", "s", body, angle, "", "", "SENT", "", "", "https://c.example", "note", "", 4, "c.example", "Germany", "Robotics", 130, "Robotics PCBA"])
    ws.append(["D", "sales@d.example", "Generic Company Email", "s", body, angle, "", "", "", "", "", "https://d.example", "note", "YES", 4, "d.example", "Germany", "Robotics", 130, "Robotics PCBA"])
    ws.append(["A", "office@a.example", "Generic Company Email", "s", body, angle, "", "", "", "", "", "https://a.example", "note", "", 4, "a.example", "Germany", "Robotics", 130, "Robotics PCBA"])
    log = wb.create_sheet(SENT_LOG_SHEET)
    log.append(SENT_LOG_HEADERS)
    wb.save(path)

    manager = ExcelManager(path)
    selected = select_auto_send_rows(manager, max_send=50)

    assert [item.email for item in selected] == ["sales@a.example"]


def test_select_auto_send_rows_rejects_historically_sent_domain(tmp_path):
    path = tmp_path / "book.xlsx"
    wb = Workbook()
    leads = wb.active
    leads.title = "Leads"
    leads.append(["公司名称", "公司官网"])
    ws = wb.create_sheet(OUTREACH_SHEET)
    ws.append(OUTREACH_HEADERS)
    body = " ".join(["word"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    ws.append(["New Name", "new@sent.example", "Generic Company Email", "s", body, "hardware signals: sensor, controller.", "", "", "", "", "", "https://sent.example", "note", "", 4, "sent.example", "Germany", "Sensor", 130, "Sensor PCBA"])
    log = wb.create_sheet(SENT_LOG_SHEET)
    log.append(SENT_LOG_HEADERS)
    log.append(["2026-07-01T10:00:00", "Old Name", "old@sent.example", "old subject", "SENT", "", "Germany", "Sensor", "https://sent.example", "sent.example"])
    wb.save(path)

    selected = select_auto_send_rows(ExcelManager(path), max_send=50)

    assert selected == []


def test_send_rechecks_historically_sent_domain_before_smtp(tmp_path, monkeypatch):
    import email_sender as email_sender_module
    from email_sender import send_auto_eligible_emails as send_emails

    path = tmp_path / "book.xlsx"
    wb = Workbook()
    leads = wb.active
    leads.title = "Leads"
    leads.append(["公司名称", "公司官网"])
    ws = wb.create_sheet(OUTREACH_SHEET)
    ws.append(OUTREACH_HEADERS)
    body = " ".join(["word"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    ws.append(["New Name", "new@sent.example", "Generic Company Email", "s", body, "hardware signals: sensor, controller.", "YES", "eligible", "", "", "", "https://sent.example", "note", "", 4, "sent.example", "Germany", "Sensor", 130, "Sensor PCBA"])
    log = wb.create_sheet(SENT_LOG_SHEET)
    log.append(SENT_LOG_HEADERS)
    log.append(["2026-07-01T10:00:00", "Old Name", "old@sent.example", "old subject", "SENT", "", "Germany", "Sensor", "https://sent.example", "sent.example"])
    wb.save(path)
    item = email_sender_module.SendableEmail(2, "New Name", "new@sent.example", "Generic Company Email", "s", body, "Germany", "Sensor", "https://sent.example", "sent.example")
    monkeypatch.setattr(email_sender_module, "select_auto_send_rows", lambda manager, max_send: [item])
    sent = []
    monkeypatch.setattr(email_sender_module.RealEmailSender, "send", lambda self, selected: sent.append(selected))

    stats = send_emails(ExcelManager(path), 1, 0, 0, dry_run=False)

    assert sent == []
    assert stats.sent == 0
    assert stats.skipped == 1


def test_private_email_and_score3_cannot_be_auto_send_eligible(tmp_path):
    path = tmp_path / "book.xlsx"
    wb = Workbook()
    leads = wb.active
    leads.title = "Leads"
    leads.append(["公司名称", "公司官网"])
    ws = wb.create_sheet(OUTREACH_SHEET)
    ws.append(OUTREACH_HEADERS)
    body = " ".join(["word"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    ws.append(["PrivateCo", "founder@yahoo.com", "Private Email", "s", body, "hardware signals: battery, controller.", "", "", "", "", "", "https://private.example", "note", "", 3, "private.example", "Germany", "BMS", 130, "BMS PCBA"])
    log = wb.create_sheet(SENT_LOG_SHEET)
    log.append(SENT_LOG_HEADERS)
    wb.save(path)

    manager = ExcelManager(path)
    manager.evaluate_auto_send_all()
    selected = select_auto_send_rows(manager, max_send=50)

    assert selected == []
    assert manager.sheet(OUTREACH_SHEET).cell(2, OUTREACH_HEADERS.index("Auto Send Eligible") + 1).value == "NO"


def test_send_stats_return_successful_items_for_keyword_attribution(tmp_path, monkeypatch):
    path = tmp_path / "book.xlsx"
    wb = Workbook()
    leads = wb.active
    leads.title = "Leads"
    leads.append(["公司名称", "公司官网"])
    ws = wb.create_sheet(OUTREACH_SHEET)
    ws.append(OUTREACH_HEADERS)
    body = " ".join(["word"] * 125) + "\n\n" + OPT_OUT_SENTENCE
    ws.append(["A", "sales@a.example", "Generic Company Email", "subject", body, "hardware signals: sensor, controller.", "", "", "", "", "", "https://a.example", "note", "", 4, "a.example", "Germany", "Sensor", 130, "Sensor PCBA"])
    log = wb.create_sheet(SENT_LOG_SHEET)
    log.append(SENT_LOG_HEADERS)
    wb.save(path)
    monkeypatch.setattr(email_sender.RealEmailSender, "send", lambda self, item: None)

    stats = send_auto_eligible_emails(ExcelManager(path), 1, 0, 0, dry_run=False)

    assert stats.sent == 1
    assert [item.email for item in stats.sent_items] == ["sales@a.example"]


def test_daily_report_includes_discovery_summary():
    data = DailyReportData(
        country="Germany",
        industry="Sensor",
        running_day=1,
        dynamic_send_limit=10,
        send_target=10,
        send_shortfall=4,
        new_leads=6,
        score4=5,
        score5=1,
        new_drafts=6,
        auto_eligible=0,
        sent_success=6,
        unsent_remaining=0,
        sent_failed=0,
        throttled=False,
        paused=False,
        unsent_reasons=[],
        next_country="Germany",
        next_industry="Instrumentation",
        discovery_summary=(
            "candidates checked=80; directory candidates=24; public emails=6; cache skips=30; "
            "provider attempts=120; provider failures=2"
        ),
        new_domains=80,
        email_domains=6,
        hardware_passed=5,
        nonhardware_filtered=12,
        cache_skipped=30,
        history_sent_skipped=4,
        company_name_failures=2,
        top_source="Exhibitor Lists",
        top_keyword="Germany industrial sensor exhibitor manufacturer",
        next_keyword="Germany industrial sensor manufacturer contact",
    )

    text = build_daily_report_text(data)

    assert "公开联系人发现统计" in text
    assert "public emails=6" in text
    assert "provider attempts=120" in text
    assert "provider failures=2" in text
    assert "本轮发送目标：10" in text
    assert "今日实际发送数量：6" in text
    assert "本轮发送短缺：4" in text
    assert "新增官网域名数量：80" in text
    assert "发现公开邮箱域名数量：6" in text
    assert "硬件资格通过数量：5" in text
    assert "非硬件网站过滤数量：12" in text
    assert "联系缓存跳过数量：30" in text
    assert "历史已发送跳过数量：4" in text
    assert "公司名称解析失败数量：2" in text
    assert "最佳来源：Exhibitor Lists" in text
    assert "最佳关键词：Germany industrial sensor exhibitor manufacturer" in text
    assert "明日推荐国家/行业/关键词：Germany + Instrumentation + Germany industrial sensor manufacturer contact" in text
