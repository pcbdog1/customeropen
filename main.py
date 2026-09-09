from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from contact_cache import CONTACT_CACHE_PATH, ContactCache
from config import CYCLE_SEND_TARGET, HIGH_YIELD_INDUSTRIES, LoopConfig, LoopState
from daily_report import DailyReportData, save_daily_report
from domain_pool import DomainPool, DomainPoolRecord, import_seed_file
from email_seed_import import import_email_seed_files, import_email_seeds
from email_enrichment import HunterEmailEnricher
from email_recovery import RecoveryStats, run_email_recovery
from firecrawl_fetcher import FirecrawlFallbackFetcher
from email_sender import send_auto_eligible_emails
from email_sender_common import OPT_OUT_SENTENCE
from excel_manager import ExcelManager, OUTREACH_SHEET
from lead_finder import (
    DiscoveryStats,
    find_daily_leads,
    has_available_search_sources,
    public_contact_fetcher,
    public_discovery_search_provider,
)
from keyword_performance import KEYWORD_PERFORMANCE_PATH, KeywordPerformanceStore
from pcb_leads.keyword_library import domain_enrichment_queries
from pcb_leads.search import SearchResult
from pcb_leads.fetcher import PublicPageFetcher
from pcb_leads.seed_sources import seed_results_for
from pcb_leads.utils import normalize_domain, normalize_host
from pool_seed_sources import LOCAL_CURATED_HARDWARE_SEEDS
from search_provider import StableSearchProvider
from run_guard import (
    CHECKPOINT_PATH,
    RUN_SUMMARY_PATH,
    RunLock,
    estimate_next_launchd_run,
    lock_status,
    read_checkpoint,
    read_run_summary,
    write_checkpoint,
    write_run_summary,
)


LAUNCHD_LABEL = "com.customeropen.lead-automation"


class RunSearchBudget:
    HARD_MAX_ATTEMPTS = 300
    _DOMAIN_ENRICHMENT_QUERY = re.compile(r'^"([^"\s]+)"\s+email\s+contact$', re.IGNORECASE)
    _SITE_QUERY = re.compile(r"\bsite:([^\s]+)", re.IGNORECASE)

    def __init__(
        self,
        provider,
        max_queries: int,
        max_domains_per_source: int = 50,
        send_target: int = CYCLE_SEND_TARGET,
        max_provider_attempts: int | None = None,
    ) -> None:
        self.provider = provider
        self.max_queries = min(self.HARD_MAX_ATTEMPTS, max(0, int(max_queries)))
        requested_attempts = self.max_queries if max_provider_attempts is None else max_provider_attempts
        self.max_provider_attempts = min(
            self.HARD_MAX_ATTEMPTS,
            max(0, int(requested_attempts)),
        )
        self.max_domains_per_source = max(0, int(max_domains_per_source))
        self.send_target = min(CYCLE_SEND_TARGET, max(0, int(send_target)))
        self.query_count = 0
        self._seen_company_domains: set[str] = set()
        self._accepted_domains_by_source: dict[str, set[str]] = {}
        self._provider_failures = 0
        configure_attempt_budget = getattr(self.provider, "configure_attempt_budget", None)
        if callable(configure_attempt_budget):
            configure_attempt_budget(self.max_provider_attempts)

    @property
    def provider_attempts(self) -> int:
        reported = getattr(self.provider, "provider_attempts", None)
        return int(reported) if reported is not None else self.query_count

    @property
    def provider_failures(self) -> int:
        reported = getattr(self.provider, "provider_failures", None)
        return int(reported) if reported is not None else self._provider_failures

    @property
    def provider_diagnostics(self) -> list[dict[str, object]]:
        reported = getattr(self.provider, "provider_diagnostics", None)
        if reported is not None:
            return list(reported)
        return [
            {
                "name": type(self.provider).__name__,
                "attempts": self.provider_attempts,
                "failures": self.provider_failures,
                "circuit_open": False,
                "last_error": "",
            }
        ]

    def can_search(self, sent_success: int = 0) -> bool:
        return (
            sent_success < self.send_target
            and self.query_count < self.max_queries
            and self.provider_attempts < self.max_provider_attempts
        )

    def _search_once(self, query: str, limit: int) -> list:
        if not self.can_search():
            return []
        self.query_count += 1
        try:
            return list(self.provider.search(query, max(0, int(limit))))
        except Exception:
            self._provider_failures += 1
            return []

    def _source_key(self, query: str, source_key: str = "") -> str:
        match = self._SITE_QUERY.search(query)
        if match:
            return match.group(1).casefold().removeprefix("www.")
        normalized = re.sub(r"\s+", " ", source_key.strip().casefold())
        return normalized or "generic web"

    def search_source(self, query: str, limit: int, source_key: str) -> list:
        return self._search_once(query, limit)

    def accept_company_domain(self, url_or_domain: str, *, query: str = "", source_key: str = "") -> bool:
        domain = normalize_domain(url_or_domain)
        if not domain or domain in self._seen_company_domains:
            return False
        self._seen_company_domains.add(domain)
        quota_key = self._source_key(query, source_key)
        accepted = self._accepted_domains_by_source.setdefault(quota_key, set())
        if len(accepted) >= self.max_domains_per_source:
            return False
        accepted.add(domain)
        return True

    def search(self, query: str, limit: int) -> list:
        enrichment = self._DOMAIN_ENRICHMENT_QUERY.fullmatch(query.strip())
        if enrichment:
            results = []
            seen_urls: set[str] = set()
            for intent_query in domain_enrichment_queries(enrichment.group(1)):
                for result in self._search_once(intent_query, limit):
                    identity = result.url.rstrip("/").casefold()
                    if identity in seen_urls:
                        continue
                    seen_urls.add(identity)
                    results.append(result)
                    if len(results) >= max(0, int(limit)):
                        return results
                if not self.can_search():
                    break
            return results
        return self.search_source(query, limit, "")


class RunPageFetchBudget:
    def __init__(self, fetcher, max_pages_per_domain: int = 5) -> None:
        self.fetcher = fetcher
        self.max_pages_per_domain = max(0, int(max_pages_per_domain))
        self._pages_by_domain: dict[str, int] = {}
        self._cache: dict[str, object | None] = {}

    def fetch(self, url: str):
        cache_key = url.rstrip("/").casefold()
        if cache_key in self._cache:
            return self._cache[cache_key]
        domain = normalize_domain(url)
        fetched = self._pages_by_domain.get(domain, 0)
        if not domain or fetched >= self.max_pages_per_domain:
            self._cache[cache_key] = None
            return None
        self._pages_by_domain[domain] = fetched + 1
        page = self.fetcher.fetch(url)
        self._cache[cache_key] = page
        return page


def cycle_send_capacity(pending_count: int, sent_success: int) -> int:
    return max(0, min(pending_count, CYCLE_SEND_TARGET - sent_success))


def effective_cycle_send_target(send_limit_override: int | None) -> int:
    if send_limit_override is None:
        return CYCLE_SEND_TARGET
    return min(CYCLE_SEND_TARGET, max(0, int(send_limit_override)))


def pending_auto_eligible_draft_identities(manager: ExcelManager) -> set[tuple[str, str]]:
    """Return pending eligible outreach drafts by normalized email and full company host."""
    ws = manager.sheet(OUTREACH_SHEET)
    headers = manager.headers(ws)
    email_col = headers.index("Contact Email") + 1
    domain_col = headers.index("Company Domain") + 1
    eligible_col = headers.index("Auto Send Eligible") + 1
    status_col = headers.index("Send Status") + 1
    return {
        (email, domain)
        for row in range(2, ws.max_row + 1)
        if str(ws.cell(row, eligible_col).value or "").strip().upper() == "YES"
        and not str(ws.cell(row, status_col).value or "").strip()
        and (email := str(ws.cell(row, email_col).value or "").strip().casefold())
        and email != "not found"
        and (domain := normalize_host(ws.cell(row, domain_col).value))
    }


def outreach_draft_identities(manager: ExcelManager) -> set[tuple[str, str]]:
    """Return identities for every pre-existing outreach row, regardless of eligibility."""
    ws = manager.sheet(OUTREACH_SHEET)
    headers = manager.headers(ws)
    email_col = headers.index("Contact Email") + 1
    domain_col = headers.index("Company Domain") + 1
    return {
        (email, domain)
        for row in range(2, ws.max_row + 1)
        if (email := str(ws.cell(row, email_col).value or "").strip().casefold())
        and email != "not found"
        and (domain := normalize_host(ws.cell(row, domain_col).value))
    }


def fallback_search_states(config: LoopConfig, persisted: LoopState):
    high_yield = {industry.casefold() for industry in HIGH_YIELD_INDUSTRIES}
    high_yield_indexes = [
        index for index, industry in enumerate(config.industries) if industry.casefold() in high_yield
    ]
    broad_indexes = [
        index for index, industry in enumerate(config.industries) if industry.casefold() not in high_yield
    ]
    current = (
        persisted.country_index % len(config.countries),
        persisted.industry_index % len(config.industries),
    )
    for industry_indexes in (high_yield_indexes, broad_indexes):
        max_rank = len(config.countries) + len(industry_indexes) - 1
        for combined_rank in range(max_rank):
            for country_index in range(len(config.countries)):
                industry_rank = combined_rank - country_index
                if not 0 <= industry_rank < len(industry_indexes):
                    continue
                industry_index = industry_indexes[industry_rank]
                if (country_index, industry_index) == current:
                    continue
                yield LoopState(
                    automation_start_date=persisted.automation_start_date,
                    running_day=persisted.running_day,
                    dynamic_daily_send_limit=persisted.dynamic_daily_send_limit,
                    send_throttle_reason=persisted.send_throttle_reason,
                    auto_send_paused=persisted.auto_send_paused,
                    country_index=country_index,
                    industry_index=industry_index,
                    last_run_date=persisted.last_run_date,
                    consecutive_high_failure_days=persisted.consecutive_high_failure_days,
                )


def draft_for_lead(lead) -> dict[str, object]:
    emphasis = industry_emphasis(lead.industry)
    subject = lead.email_subject.lower()[:60]
    service_angle = english_service_angle(lead.industry)
    sender_name = os.getenv("OUTREACH_SENDER_NAME", "Your Name")
    company_name = os.getenv("OUTREACH_COMPANY_NAME", "Your Company")
    company_website = os.getenv("OUTREACH_COMPANY_WEBSITE", "https://example.com")
    body = f"""Hello,

{lead.company_name}'s work in {lead.industry.lower()} suggests ongoing needs around control electronics, embedded boards, sensors, or functional test hardware. Those projects often need reliable PCB fabrication and PCBA support from prototype through small batch builds.

{company_name} supports PCB fabrication, PCBA assembly, component sourcing, prototype support, small batch production, firmware programming, and functional testing.

If your team needs support for {service_angle}, we can review BOMs, assemble boards, and follow agreed test requirements before shipment.

Would this be useful for an upcoming hardware project?

Kind regards,
{sender_name}
{company_name}
{company_website}

{OPT_OUT_SENTENCE}"""
    return {
        "Company Name": lead.company_name,
        "Contact Email": lead.email,
        "Email Type": lead.email_type,
        "Subject": subject,
        "Email Body": body,
        "Why This Angle Fits": lead.pcb_need_reason,
        "Auto Send Eligible": "",
        "Auto Send Reason": "",
        "Send Status": "",
        "Send Date": "",
        "Error Message": "",
        "Source URL": lead.source_links,
        "Compliance Note": lead.compliance_note,
        "Do Not Contact": "",
        "Lead Score": lead.score,
        "Company Domain": lead.domain,
        "Country": lead.country,
        "Industry": lead.industry,
        "Word Count": len(body.replace("/", " ").split()),
        "Industry Emphasis": emphasis,
    }


def keyword_report_values(performance_store: KeywordPerformanceStore) -> tuple[str, str, str]:
    top_queries = performance_store.top_queries(1)
    if not top_queries:
        return "无", "无", "无"
    top = top_queries[0]
    source = str(top.get("source_type") or "无")
    keyword = str(top.get("query") or "无")
    normalized_keyword = keyword.strip().casefold()
    next_keyword = next(
        (
            candidate
            for record in performance_store.ranked_queries(include_paused=False)
            if (candidate := str(record.get("query") or "").strip())
            and candidate.casefold() != normalized_keyword
        ),
        "无",
    )
    return source, keyword, next_keyword


def industry_emphasis(industry: str) -> str:
    lowered = industry.lower()
    if "robot" in lowered:
        return "Robotics PCBA; Industrial Control PCB; High-reliability PCB"
    if "medical" in lowered:
        return "Medical PCBA; High-reliability PCB"
    if "sensor" in lowered or "embedded" in lowered or "iot" in lowered:
        return "Sensor / Embedded System PCBA; Industrial Control PCB"
    if "ai" in lowered:
        return "AI Hardware PCB; Sensor / Embedded System PCBA"
    return "Industrial Control PCB; High-reliability PCB"


def english_service_angle(industry: str) -> str:
    lowered = industry.lower()
    if "robot" in lowered:
        return "robotics control boards, sensor interface PCBAs, or small batch robot electronics"
    if "medical" in lowered:
        return "medical device control boards, prototype PCBAs, or high-reliability small batch electronics"
    if "sensor" in lowered:
        return "sensor interface PCBAs, embedded control boards, or functional-tested electronics"
    if "ai" in lowered:
        return "AI hardware boards, edge device PCBAs, or embedded prototype builds"
    if "iot" in lowered or "embedded" in lowered:
        return "embedded system PCBAs, IoT device boards, or firmware-programmed prototypes"
    if "energy" in lowered or "charger" in lowered or "bms" in lowered:
        return "power electronics control boards, BMS PCBAs, or small batch energy hardware"
    return "industrial control boards, embedded PCBAs, or prototype-to-small-batch electronics"


def summarize_unsent_reasons(manager: ExcelManager) -> list[str]:
    ws = manager.sheet(OUTREACH_SHEET)
    headers = manager.headers(ws)
    reason_col = headers.index("Auto Send Reason") + 1
    eligible_col = headers.index("Auto Send Eligible") + 1
    status_col = headers.index("Send Status") + 1
    counts: dict[str, int] = {}
    for row in range(2, ws.max_row + 1):
        eligible = str(ws.cell(row, eligible_col).value or "")
        status = str(ws.cell(row, status_col).value or "")
        if eligible == "YES" or status:
            continue
        reason = str(ws.cell(row, reason_col).value or "Unknown")
        first_reason = reason.split(";")[0]
        counts[first_reason] = counts.get(first_reason, 0) + 1
    return [f"{reason}: {count}" for reason, count in sorted(counts.items())]


def compute_running_day(state: LoopState, forced_day: int | None = None) -> int:
    if forced_day:
        return forced_day
    try:
        start = date.fromisoformat(state.automation_start_date)
    except ValueError:
        start = date.today()
    return max(1, (date.today() - start).days + 1)


def collect_local_seed_domains(pool: DomainPool, config: LoopConfig, limit: int = 100) -> int:
    """Populate the append-only pool from curated public-company seeds only."""
    records: list[DomainPoolRecord] = []
    for country in config.countries:
        for result in seed_results_for(country, "Industrial Electronics", limit):
            records.append(DomainPoolRecord(
                company_name=result.title,
                domain=normalize_domain(result.url),
                website=f"https://{normalize_host(result.url)}",
                country=country,
                industry="Industrial Electronics",
                source_type="Local Curated Public Seed",
                source_url=result.url,
                hardware_signal=result.snippet,
            ))
            if len(records) >= limit * 4:
                break
        if len(records) >= limit * 4:
            break
    for country, industry, company, website, signal in LOCAL_CURATED_HARDWARE_SEEDS:
        records.append(DomainPoolRecord(
            company_name=company,
            domain=normalize_domain(website),
            website=website,
            country=country,
            industry=industry,
            source_type="Local Curated Public Seed",
            source_url=website,
            hardware_signal=signal,
        ))
    added = pool.add(records)
    pool.save()
    return added


def import_domains() -> dict[str, object]:
    config = LoopConfig()
    pool = DomainPool(config.domain_pool_path)
    imported = 0
    for source in (Path("inputs/domain_seed.csv"), Path("inputs/domain_seed.xlsx")):
        imported += import_seed_file(pool, source)
    pool.save()
    return {"imported_domains": imported, "domain_pool": str(config.domain_pool_path), **pool.metrics()}


def collect_domains(limit: int = 100, dry_run: bool = False) -> dict[str, object]:
    config = LoopConfig()
    pool = DomainPool(config.domain_pool_path)
    manager = ExcelManager(config.workbook_path)
    known_domains = manager.existing_domains() | {normalize_domain(value) for value in manager.sent_domains()}
    existing_pool_domains = pool._existing_domains()
    already_known = 0
    imported = 0
    added = 0
    if not dry_run:
        already_known = pool.mark_known(known_domains)
        for source in (Path("inputs/domain_seed.csv"), Path("inputs/domain_seed.xlsx")):
            imported += import_seed_file(pool, source)
        added = collect_local_seed_domains(pool, config, limit=limit)

    provider = StableSearchProvider()
    api_added = 0
    dry_run_domains: set[str] = set()
    query_countries = config.countries[:1] if dry_run else config.countries[:4]
    query_industries = HIGH_YIELD_INDUSTRIES[:1] if dry_run else HIGH_YIELD_INDUSTRIES[:2]
    for country in query_countries:
        for industry in query_industries:
            results = provider.search_all(f"{industry} manufacturer company {country}", limit=min(10, limit))
            if dry_run:
                for result in results:
                    domain = normalize_domain(result.url)
                    if domain and domain not in known_domains and domain not in existing_pool_domains:
                        dry_run_domains.add(domain)
                api_added = len(dry_run_domains)
            else:
                api_added += pool.add(DomainPoolRecord(
                    company_name=result.title, domain=normalize_domain(result.url), website=result.url,
                    country=country, industry=industry, source_type="Official Search API",
                    source_url=result.url, hardware_signal=result.snippet,
                ) for result in results)
            if api_added >= limit:
                break
        if api_added >= limit:
            break
    if not dry_run:
        pool.save()
    return {"dry_run": dry_run, "imported_domains": imported, "new_domains": added + api_added, "local_seed_domains": added, "api_domains": api_added, "already_known_domains": already_known, "domain_pool": str(config.domain_pool_path), "api_usage": provider.usage_summary(), **pool.metrics()}


def email_recovery(dry_run: bool = True, limit: int = 50) -> dict[str, object]:
    config = LoopConfig()
    manager = ExcelManager(config.workbook_path)
    if dry_run:
        recovery_workbook = config.workbook_path.with_name(config.workbook_path.stem + "_EMAIL_RECOVERY_DRY_RUN.xlsx")
        manager.path = recovery_workbook
    recovery_fetcher = PublicPageFetcher(
        user_agent="PCBDOG-Email-Recovery/1.0 (+https://www.pcbdog.com)",
        timeout=2,
        pause_seconds=0,
    )
    fetcher = FirecrawlFallbackFetcher(recovery_fetcher, api_timeout=4)
    enricher = HunterEmailEnricher(api_timeout=4)
    stats, usage = run_email_recovery(
        manager,
        fetcher,
        enricher,
        max_companies=min(50, max(1, limit)),
        dry_run=False,
    )
    manager.save()
    return {
        "dry_run": dry_run,
        "workbook": str(manager.path),
        "processed": stats.processed,
        "recovered": stats.recovered,
        "safety_passed": stats.safety_passed,
        "not_found": stats.not_found,
        "api_usage": usage,
        "samples": usage["reports"][:10],
    }


def run_daily_loop(
    dry_run: bool = False,
    send_limit_override: int | None = None,
    lead_limit_override: int | None = None,
    force_country: str | None = None,
    force_industry: str | None = None,
    simulate_day: int | None = None,
) -> dict[str, object]:
    config = LoopConfig()
    checkpoint_path = CHECKPOINT_PATH.with_name("daily_loop_checkpoint.dry_run.json") if dry_run else CHECKPOINT_PATH
    summary_path = RUN_SUMMARY_PATH.with_name("run_summary.dry_run.json") if dry_run else RUN_SUMMARY_PATH
    lock_path = CHECKPOINT_PATH.with_name("daily_loop.dry_run.lock") if dry_run else None
    lock = RunLock.acquire(path=lock_path, checkpoint_path=checkpoint_path) if lock_path else RunLock.acquire()
    recovered_from_interruption = lock.recovered_from_stale_lock
    sent_success = 0
    append_added = 0
    new_drafts = 0
    contact_cache_path = CONTACT_CACHE_PATH.with_name("contact_cache.dry_run.json") if dry_run else CONTACT_CACHE_PATH
    performance_path = KEYWORD_PERFORMANCE_PATH.with_name("keyword_performance.dry_run.json") if dry_run else KEYWORD_PERFORMANCE_PATH
    discovery_cache = ContactCache(contact_cache_path)
    performance_store = KeywordPerformanceStore(performance_path)
    lead_query_by_email: dict[str, str] = {}

    def checkpoint(**values) -> None:
        write_checkpoint(path=checkpoint_path, **values)

    def run_summary(**values) -> None:
        write_run_summary(path=summary_path, **values)

    def remember_queries(leads) -> None:
        for lead in leads:
            if lead.email and lead.email != "Not Found":
                lead_query_by_email[lead.email.strip().casefold()] = lead.demand_signal_source

    def record_sent_keywords(send_stats) -> None:
        if dry_run:
            return
        for item in send_stats.sent_items:
            query = lead_query_by_email.get(item.email.strip().casefold())
            if query:
                performance_store.record_sent(query, "Email-First Web")
    try:
        checkpoint(stage="start", interruption_reason="")
        state = LoopState.load(config.loop_state_path)
        previous_summary = read_run_summary(summary_path)
        previous_checkpoint = read_checkpoint(checkpoint_path)
        if previous_summary and not previous_summary.get("completed", True):
            recovered_from_interruption = True
        if previous_checkpoint.get("interruption_reason"):
            recovered_from_interruption = True
        running_day = compute_running_day(state, simulate_day)
        dynamic_limit = config.cycle_send_target
        effective_send_limit = effective_cycle_send_target(send_limit_override) if send_limit_override is not None else 0

        country = force_country or state.current_country(config)
        industry = force_industry or state.current_industry(config)
        if force_country in config.countries or force_industry in config.industries:
            state = LoopState(
                automation_start_date=state.automation_start_date,
                running_day=state.running_day,
                dynamic_daily_send_limit=state.dynamic_daily_send_limit,
                send_throttle_reason=state.send_throttle_reason,
                auto_send_paused=state.auto_send_paused,
                country_index=config.countries.index(country) if country in config.countries else state.country_index,
                industry_index=config.industries.index(industry) if industry in config.industries else state.industry_index,
                last_run_date=state.last_run_date,
                consecutive_high_failure_days=state.consecutive_high_failure_days,
            )
        lock.heartbeat("load_workbook")
        manager = ExcelManager(config.workbook_path)
        baseline_outreach_draft_ids = outreach_draft_identities(manager)
        manager.evaluate_auto_send_all()
        pending_before_search = manager.pending_auto_eligible_count()

        def target_progress() -> int:
            if not dry_run:
                return sent_success
            return len(pending_auto_eligible_draft_identities(manager) - baseline_outreach_draft_ids)

        def discovery_limit(target_remaining: int) -> int:
            configured_limit = lead_limit_override or config.daily_lead_target
            if dry_run:
                return min(configured_limit, target_remaining)
            return lead_limit_override or min(config.daily_lead_target, target_remaining)

        if send_limit_override is None:
            effective_send_limit = cycle_send_capacity(pending_before_search, 0)

        lock.heartbeat("send_existing")
        first_send = send_auto_eligible_emails(
            manager,
            max_send=0 if state.auto_send_paused or not config.auto_send_enabled else effective_send_limit,
            wait_min=0 if dry_run or send_limit_override is not None else config.send_delay_min_seconds,
            wait_max=0 if dry_run or send_limit_override is not None else config.send_delay_max_seconds,
            dry_run=dry_run,
        )
        sent_success = 0 if dry_run else first_send.sent
        last_customer = first_send.last_customer
        last_email = first_send.last_email
        checkpoint(
            stage="send_existing_complete",
            sent_emails=sent_success,
            last_customer=last_customer,
            last_email=last_email,
        )

        # Use recovery only to fill the remaining cycle capacity. Existing safe
        # drafts are always attempted first, so API credits are not consumed when
        # the historical outbound queue already satisfies the ten-email target.
        recovery_stats = RecoveryStats()
        if target_progress() < config.cycle_send_target:
            lock.heartbeat("email_recovery")
            recovery_fetcher = PublicPageFetcher(
                user_agent="PCBDOG-Email-Recovery/1.0 (+https://www.pcbdog.com)",
                timeout=2,
                pause_seconds=0,
            )
            recovery_stats, _ = run_email_recovery(
                manager,
                FirecrawlFallbackFetcher(recovery_fetcher, api_timeout=4),
                HunterEmailEnricher(api_timeout=4),
                max_companies=config.max_email_recovery_per_run,
                dry_run=dry_run,
            )
            if not dry_run and recovery_stats.processed:
                manager.save()
            manager.evaluate_auto_send_all()

        remaining_send_capacity = (
            max(0, effective_send_limit - first_send.sent)
            if send_limit_override is not None
            else cycle_send_capacity(manager.pending_auto_eligible_count(), first_send.sent)
        )

        lock.heartbeat("find_leads")
        discovery_stats = DiscoveryStats()
        run_target = effective_cycle_send_target(send_limit_override)
        domain_pool = DomainPool(config.domain_pool_path)
        stable_provider = StableSearchProvider()
        email_enricher = HunterEmailEnricher()
        firecrawl_fetcher = FirecrawlFallbackFetcher(public_contact_fetcher())
        # Email recovery updates existing drafts but does not send them until the
        # second send stage. Only discover more domains when the already-sent
        # count plus the now-qualified queue cannot fill this cycle.
        needs_discovery = (
            target_progress() < run_target
            if dry_run
            else sent_success + manager.pending_auto_eligible_count() < run_target
        )
        pool_records = (
            domain_pool.unchecked(config.max_pool_domains_per_run * (2 if dry_run else 1))
            if needs_discovery
            else []
        )
        pool_domains = [record.domain for record in pool_records]
        pool_groups: dict[tuple[str, str], list[SearchResult]] = {}
        for record in pool_records:
            key = (record.country or country, record.industry or industry)
            pool_groups.setdefault(key, []).append(SearchResult(
                title=record.company_name,
                url=record.website,
                snippet=record.hardware_signal,
                query=f"{record.source_type}: {record.source_url}",
            ))
        pool_results = [result for group in pool_groups.values() for result in group]
        search_budget = RunSearchBudget(
            stable_provider,
            max_queries=config.max_search_queries,
            max_domains_per_source=config.max_domains_per_source,
            send_target=run_target,
        )
        page_budget = RunPageFetchBudget(
            firecrawl_fetcher,
            max_pages_per_domain=(
                config.max_pool_fetched_pages_per_domain if pool_results else config.max_fetched_pages_per_domain
            ),
        )
        if pool_results:
            initial_limit = discovery_limit(max(0, run_target - target_progress()))
            leads = []
            for (pool_country, pool_industry), group in pool_groups.items():
                if len(leads) >= initial_limit:
                    break
                group_leads = find_daily_leads(
                    manager,
                    pool_country,
                    pool_industry,
                    initial_limit - len(leads),
                    fetcher=page_budget,
                    search_provider=stable_provider,
                    stats=discovery_stats,
                    keyword_state_updates=not dry_run,
                    contact_cache=discovery_cache,
                    keyword_performance=performance_store,
                    candidate_results=group,
                    allow_directory_discovery=False,
                    email_enricher=email_enricher,
                )
                leads.extend(group_leads)
            domain_pool.mark_checked(
                pool_domains,
                {lead.domain: lead.email for lead in leads if lead.email and lead.email != "Not Found"},
            )
            if not dry_run:
                domain_pool.save()
        elif stable_provider.configured and search_budget.can_search(target_progress()):
            initial_limit = discovery_limit(max(0, run_target - target_progress()))
            leads = find_daily_leads(
                manager,
                country,
                industry,
                initial_limit,
                fetcher=page_budget,
                search_provider=search_budget,
                stats=discovery_stats,
                keyword_state_updates=not dry_run,
                contact_cache=discovery_cache,
                keyword_performance=performance_store,
                candidate_results=stable_provider.search(f"{industry} manufacturer company {country}", config.api_domain_discovery_limit),
                allow_directory_discovery=False,
                email_enricher=email_enricher,
            )
        else:
            leads = []
        discovery_stats.search_queries = search_budget.query_count
        remember_queries(leads)
        checkpoint(stage="find_leads_complete", completed_leads=len(leads), sent_emails=sent_success)

        lock.heartbeat("append_leads")
        append_stats = manager.append_leads(leads)
        append_added = append_stats.added
        checkpoint(stage="append_leads_complete", completed_leads=append_stats.added, sent_emails=sent_success)

        lock.heartbeat("generate_drafts")
        new_drafts = manager.append_outreach_drafts([draft_for_lead(lead) for lead in leads])
        checkpoint(
            stage="generate_drafts_complete",
            completed_leads=append_stats.added,
            generated_emails=new_drafts,
            sent_emails=sent_success,
        )
        manager.evaluate_auto_send_all()
        if send_limit_override is None:
            remaining_send_capacity = cycle_send_capacity(manager.pending_auto_eligible_count(), first_send.sent)

        lock.heartbeat("send_new")
        second_send = send_auto_eligible_emails(
            manager,
            max_send=0 if state.auto_send_paused or not config.auto_send_enabled else remaining_send_capacity,
            wait_min=0 if dry_run or send_limit_override is not None else config.send_delay_min_seconds,
            wait_max=0 if dry_run or send_limit_override is not None else config.send_delay_max_seconds,
            dry_run=dry_run,
        )
        record_sent_keywords(second_send)

        sent_success = first_send.sent + second_send.sent
        sent_failed = first_send.failed + second_send.failed
        attempted = first_send.attempted + second_send.attempted
        last_customer = second_send.last_customer or first_send.last_customer
        last_email = second_send.last_email or first_send.last_email
        total_added = append_stats.added
        total_score4 = append_stats.score4
        total_score5 = append_stats.score5
        total_new_drafts = new_drafts
        search_cursor = state
        extra_searches = 0
        cursor_steps = 0
        max_extra_searches = config.max_fallback_combinations
        max_cursor_steps = len(config.countries) * len(config.industries)
        fallback_states = iter(fallback_search_states(config, state))
        while (
            (dry_run or (not state.auto_send_paused and config.auto_send_enabled))
            and stable_provider.configured
            and search_budget.can_search(target_progress())
            and extra_searches < max_extra_searches
            and cursor_steps < max_cursor_steps
        ):
            try:
                search_cursor = next(fallback_states)
            except StopIteration:
                break
            cursor_steps += 1
            extra_country = search_cursor.current_country(config)
            extra_industry = search_cursor.current_industry(config)
            if not has_available_search_sources(
                manager,
                extra_country,
                extra_industry,
                lead_limit_override or config.daily_lead_target,
            ):
                continue
            print(
                f"target_send_loop: search={extra_searches + 1}/{max_extra_searches} "
                f"country={extra_country} industry={extra_industry} sent_success={sent_success}",
                flush=True,
            )
            lock.heartbeat(f"target_send_search_{extra_country}_{extra_industry}")
            extra_searches += 1
            target_remaining = max(
                0,
                run_target - target_progress(),
            )
            extra_leads = find_daily_leads(
                manager,
                extra_country,
                extra_industry,
                discovery_limit(target_remaining),
                fetcher=page_budget,
                search_provider=search_budget,
                stats=discovery_stats,
                keyword_state_updates=not dry_run,
                contact_cache=discovery_cache,
                keyword_performance=performance_store,
                relax_not_found_cache=True,
            )
            discovery_stats.search_queries = search_budget.query_count
            remember_queries(extra_leads)
            extra_stats = manager.append_leads(extra_leads)
            extra_drafts = manager.append_outreach_drafts([draft_for_lead(lead) for lead in extra_leads])
            manager.evaluate_auto_send_all()
            total_added += extra_stats.added
            total_score4 += extra_stats.score4
            total_score5 += extra_stats.score5
            total_new_drafts += extra_drafts
            pending_now = manager.pending_auto_eligible_count()
            extra_send = send_auto_eligible_emails(
                manager,
                max_send=min(pending_now, target_remaining),
                wait_min=config.send_delay_min_seconds,
                wait_max=config.send_delay_max_seconds,
                dry_run=dry_run,
            )
            record_sent_keywords(extra_send)
            print(
                f"target_send_loop_result: country={extra_country} industry={extra_industry} "
                f"new_leads={extra_stats.added} new_drafts={extra_drafts} "
                f"pending_before_send={pending_now} sent={extra_send.sent} failed={extra_send.failed}",
                flush=True,
            )
            if not dry_run:
                sent_success += extra_send.sent
                sent_failed += extra_send.failed
                attempted += extra_send.attempted
                last_customer = extra_send.last_customer or last_customer
                last_email = extra_send.last_email or last_email
            checkpoint(
                stage="target_send_loop",
                completed_leads=total_added,
                generated_emails=total_new_drafts,
                sent_emails=sent_success,
                last_customer=extra_send.last_customer or last_customer,
                last_email=extra_send.last_email or last_email,
            )
        if cursor_steps > extra_searches:
            print(
                f"target_send_loop_skipped_exhausted_combinations={cursor_steps - extra_searches} "
                f"sent_success={sent_success}",
                flush=True,
            )
        discovery_stats.search_queries = search_budget.query_count
        discovery_stats.provider_attempts = search_budget.provider_attempts
        discovery_stats.provider_failures = search_budget.provider_failures
        discovery_stats.provider_diagnostics = search_budget.provider_diagnostics
        api_usage = stable_provider.usage_summary()
        api_usage["firecrawl"] = firecrawl_fetcher.usage()
        api_usage["hunter"] = email_enricher.status()
        checkpoint(
            stage="send_new_complete",
            completed_leads=total_added,
            generated_emails=total_new_drafts,
            sent_emails=sent_success,
            last_customer=last_customer,
            last_email=last_email,
        )
        failure_rate = sent_failed / attempted if attempted else 0
        throttled = sent_failed > 5
        paused = state.auto_send_paused
        consecutive_high_failure_days = state.consecutive_high_failure_days + 1 if failure_rate > 0.10 and attempted else 0
        if consecutive_high_failure_days >= 3:
            paused = True
        send_throttle_reason = "Daily failed sends exceeded 5; next run limited to 20." if throttled else ""

        next_state = search_cursor.next(config) if extra_searches else state.next(config)
        next_state.running_day = running_day
        next_state.dynamic_daily_send_limit = dynamic_limit
        next_state.send_throttle_reason = send_throttle_reason
        next_state.auto_send_paused = paused
        next_state.last_run_date = datetime.now().isoformat(timespec="seconds")
        next_state.consecutive_high_failure_days = consecutive_high_failure_days
        if not dry_run:
            next_state.save(config.loop_state_path)
        else:
            # Dry-run should still prove the shape of loop_state.json without advancing real production state.
            dry_state = config.loop_state_path.with_name("loop_state.dry_run.json")
            next_state.save(dry_state)

        manager.evaluate_auto_send_all()
        auto_eligible = manager.pending_auto_eligible_count()
        top_source, top_keyword, next_keyword = keyword_report_values(performance_store)
        report_data = DailyReportData(
            country=country,
            industry=industry,
            running_day=running_day,
            dynamic_send_limit=dynamic_limit,
            send_target=config.cycle_send_target,
            send_shortfall=max(0, config.cycle_send_target - sent_success),
            new_leads=total_added,
            score4=total_score4,
            score5=total_score5,
            new_drafts=total_new_drafts,
            auto_eligible=auto_eligible,
            sent_success=sent_success,
            unsent_remaining=auto_eligible,
            sent_failed=sent_failed,
            throttled=throttled,
            paused=paused,
            unsent_reasons=summarize_unsent_reasons(manager),
            next_country=next_state.current_country(config),
            next_industry=next_state.current_industry(config),
            discovery_summary=discovery_stats.summary(),
            new_domains=discovery_stats.candidates_checked,
            email_domains=discovery_stats.email_candidates,
            hardware_passed=discovery_stats.hardware_qualified,
            nonhardware_filtered=discovery_stats.non_hardware_filtered,
            cache_skipped=discovery_stats.cache_skips,
            history_sent_skipped=discovery_stats.sent_history_skips,
            company_name_failures=discovery_stats.company_name_failures,
            top_source=top_source,
            top_keyword=top_keyword,
            next_keyword=next_keyword,
            api_usage=api_usage,
        )
        lock.heartbeat("daily_report")
        report_path = save_daily_report(manager, report_data, config.report_dir, "_DRY_RUN" if dry_run else "")
        if not dry_run:
            manager.save()
        else:
            dry_workbook = config.workbook_path.with_name(config.workbook_path.stem + "_DRY_RUN.xlsx")
            manager.path = dry_workbook
            manager.save()

        checkpoint(
            stage="complete",
            completed_leads=total_added,
            generated_emails=total_new_drafts,
            sent_emails=sent_success,
            last_customer=last_customer,
            last_email=last_email,
            interruption_reason="",
        )
        run_summary(
            completed=True,
            recovered_from_interruption=recovered_from_interruption,
            sent_count=sent_success,
            new_lead_count=total_added,
            failure_reason="",
        )
        return {
            "dry_run": dry_run,
            "workbook": str(config.workbook_path if not dry_run else dry_workbook),
            "report": str(report_path),
            "country": country,
            "industry": industry,
            "running_day": running_day,
            "dynamic_send_limit": dynamic_limit,
            "send_target": config.cycle_send_target,
            "send_shortfall": max(0, config.cycle_send_target - sent_success),
            "effective_send_limit": effective_send_limit,
            "sent_success": sent_success,
            "sent_failed": sent_failed,
            "new_leads": total_added,
            "new_drafts": total_new_drafts,
            "auto_eligible_pending": auto_eligible,
            "dry_run_new_auto_eligible": target_progress() if dry_run else 0,
            "next_country": next_state.current_country(config),
            "next_industry": next_state.current_industry(config),
            "recovered_from_interruption": recovered_from_interruption,
            "discovery_summary": discovery_stats.summary(),
            "search_queries": discovery_stats.search_queries,
            "provider_attempts": discovery_stats.provider_attempts,
            "provider_failures": discovery_stats.provider_failures,
            "provider_diagnostics": discovery_stats.provider_diagnostics,
            "api_usage": api_usage,
            "candidates_checked": discovery_stats.candidates_checked,
            "email_candidates": discovery_stats.email_candidates,
            "skip_reasons": discovery_stats.skip_reasons,
            "top_queries": discovery_stats.top_queries,
        }
    except Exception as exc:
        reason = str(exc)
        checkpoint(
            stage="failed",
            completed_leads=append_added,
            generated_emails=new_drafts,
            sent_emails=sent_success,
            interruption_reason=reason,
        )
        run_summary(
            completed=False,
            recovered_from_interruption=recovered_from_interruption,
            sent_count=sent_success,
            new_lead_count=append_added,
            failure_reason=reason,
        )
        raise
    finally:
        lock.release()


def print_status() -> None:
    config = LoopConfig()
    state = LoopState.load(config.loop_state_path)
    checkpoint = read_checkpoint()
    summary = read_run_summary()
    lock = lock_status()
    try:
        manager = ExcelManager(config.workbook_path)
        pending = manager.pending_auto_eligible_count()
    except Exception:
        pending = "Unknown"
    try:
        pool_metrics = DomainPool(config.domain_pool_path).metrics()
    except Exception:
        pool_metrics = {"total": "Unknown", "unchecked": "Unknown", "email_found": "Unknown"}
    provider_status = StableSearchProvider().status()
    hunter = HunterEmailEnricher()
    firecrawl = FirecrawlFallbackFetcher(public_contact_fetcher())
    hunter_configured = hunter.configured
    firecrawl_configured = firecrawl.configured
    launchd = launchd_status()
    log_path = config.log_dir / f"daily_loop_{date.today():%Y%m%d}.log"
    last_log_update = file_mtime(log_path)
    abnormal = bool(
        checkpoint.get("interruption_reason")
        or (summary and not summary.get("completed", True))
        or lock.get("stale")
    )
    values = {
        "lock_present": lock["exists"],
        "lock_stale": lock["stale"],
        "last_abnormal_interruption": abnormal,
        "checkpoint_stage": checkpoint.get("stage", "Not Found"),
        "checkpoint_interruption_reason": checkpoint.get("interruption_reason", ""),
        "pending_auto_send_eligible": pending,
        "company_domain_pool_total": pool_metrics["total"],
        "company_domain_pool_unchecked": pool_metrics["unchecked"],
        "company_domain_pool_email_found": pool_metrics["email_found"],
        "search_provider": provider_status["provider"],
        "tavily_configured": provider_status["tavily_configured"],
        "brave_configured": provider_status["brave_configured"],
        "exa_configured": provider_status["exa_configured"],
        "firecrawl_configured": firecrawl_configured,
        "hunter_configured": hunter_configured,
        "email_enrichment_enabled": hunter.enabled,
        "firecrawl_enabled": firecrawl.enabled,
        "recent_sent_count": summary.get("sent_count", "Not Found"),
        "recent_failure_reason": summary.get("failure_reason", ""),
        "launchd_loaded": launchd["loaded"],
        "launchd_state": launchd["state"],
        "launchd_runs": launchd["runs"],
        "launchd_last_exit_code": launchd["last_exit_code"],
        "launchd_interval_seconds": launchd["interval_seconds"],
        "today_log_last_update": last_log_update,
        "next_launchd_run_estimate": estimate_next_launchd_run_from_log(last_log_update, launchd["interval_seconds"]),
        "last_run_date": state.last_run_date or "Not Found",
        "next_country": state.current_country(config),
        "next_industry": state.current_industry(config),
    }
    for key, value in values.items():
        print(f"{key}: {value}")


def launchd_status() -> dict[str, object]:
    result = {
        "loaded": False,
        "state": "Not Loaded",
        "runs": "Unknown",
        "last_exit_code": "Unknown",
        "interval_seconds": 19800,
    }
    completed = subprocess.run(
        ["launchctl", "print", f"gui/{subprocess.getoutput('id -u')}/{LAUNCHD_LABEL}"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        return result
    output = completed.stdout
    result["loaded"] = True
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("state = "):
            result["state"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("runs = "):
            result["runs"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("last exit code = "):
            result["last_exit_code"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("run interval = "):
            value = stripped.split("=", 1)[1].replace("seconds", "").strip()
            try:
                result["interval_seconds"] = int(value)
            except ValueError:
                pass
    return result


def file_mtime(path: Path) -> str:
    if not path.exists():
        return "Not Found"
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def estimate_next_launchd_run_from_log(last_log_update: str, interval_seconds: object) -> str:
    if not last_log_update or last_log_update == "Not Found":
        return "Unknown"
    try:
        interval = int(interval_seconds)
        last = datetime.fromisoformat(last_log_update)
    except (TypeError, ValueError):
        return "Unknown"
    return (last + timedelta(seconds=interval)).isoformat(timespec="seconds")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily_loop", "dry_run", "status", "import_domains", "collect_domains", "email_recovery", "import_email_seed", "import_email_seeds"], required=True)
    parser.add_argument("--seed-file", default="", help="CSV/XLSX company email seed file for import_email_seeds")
    parser.add_argument("--seed-dir", default="", help="Directory containing CSV/XLS/XLSX email seed files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-send-limit", type=int, default=None)
    parser.add_argument("--test-lead-limit", type=int, default=None)
    parser.add_argument("--simulate-day", type=int, default=None)
    parser.add_argument("--force-country", default=None)
    parser.add_argument("--force-industry", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "status":
        print_status()
        return
    if args.mode == "import_domains":
        result = import_domains()
        for key, value in result.items():
            print(f"{key}: {value}")
        return
    if args.mode == "collect_domains":
        result = collect_domains(limit=100, dry_run=args.dry_run)
        for key, value in result.items():
            print(f"{key}: {value}")
        return
    if args.mode == "email_recovery":
        result = email_recovery(dry_run=args.dry_run, limit=args.test_lead_limit or 50)
        for key, value in result.items():
            print(f"{key}: {value}")
        return
    if args.mode in {"import_email_seed", "import_email_seeds"}:
        config = LoopConfig()
        if args.seed_dir:
            seed_dir = Path(args.seed_dir)
            sources = sorted(
                path for path in seed_dir.iterdir()
                if path.is_file() and path.suffix.casefold() in {".csv", ".xls", ".xlsx"}
            )
        else:
            source = Path(args.seed_file) if args.seed_file else next(
                (candidate for candidate in (Path("inputs/email_seed.xlsx"), Path("inputs/email_seed.csv")) if candidate.exists()),
                None,
            )
            sources = [source] if source is not None else []
        if not sources or any(not source.exists() for source in sources):
            raise FileNotFoundError("No email seed files found")
        lock = RunLock.acquire()
        try:
            manager = ExcelManager(config.workbook_path)
            sent_log_rows_before = manager.sheet("Sent_Log").max_row
            stats = import_email_seed_files(manager, sources, draft_for_lead)
            if manager.sheet("Sent_Log").max_row != sent_log_rows_before:
                raise RuntimeError("Email seed import must not modify Sent_Log")
            manager.save()
        finally:
            lock.release()
        print(f"files_read: {stats.files_read}")
        print(f"raw_emails: {stats.raw_emails}")
        print(f"skipped_duplicates: {stats.skipped_duplicates}")
        print(f"skipped_sent: {stats.skipped_sent}")
        print(f"new_customers: {stats.new_customers}")
        print(f"new_drafts: {stats.new_drafts}")
        print(f"pending_auto_eligible: {stats.pending_eligible}")
        print(f"workbook: {config.workbook_path}")
        return
    if args.mode in {"daily_loop", "dry_run"}:
        result = run_daily_loop(
            dry_run=args.dry_run or args.mode == "dry_run",
            send_limit_override=args.test_send_limit,
            lead_limit_override=args.test_lead_limit,
            force_country=args.force_country,
            force_industry=args.force_industry,
            simulate_day=args.simulate_day,
        )
        for key, value in result.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
