from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from excel_manager import EMAIL_RECOVERY_HEADERS, EMAIL_RECOVERY_SHEET, OUTREACH_SHEET, ExcelManager
from pcb_leads.parser import FREE_EMAIL_DOMAINS, extract_contact_candidates, is_valid_email_candidate
from pcb_leads.qualification import hardware_qualification
from pcb_leads.utils import hosts_are_attributable, mailbox_domain, normalize_domain, normalize_host


# Recovery is intentionally narrow: the root plus the pages where public B2B
# contacts most often appear. Hunter handles the remaining official-domain case.
RECOVERY_PAGE_PATHS = ("", "/contact")
BLOCKED_LOCAL_PARTS = frozenset({"abuse", "privacy", "legal", "cancellation", "noreply", "no-reply", "donotreply", "do-not-reply"})
RECOVERY_COMPLIANCE_NOTE = (
    "Public business contact found from company website, official contact/impressum page, public catalog, "
    "or Hunter domain search. B2B relevance: PCB/PCBA service matches the company hardware/electronics business."
)


@dataclass
class RecoveryStats:
    processed: int = 0
    recovered: int = 0
    safety_passed: int = 0
    not_found: int = 0


class _RecoveryFetchBudget:
    """Permit one Firecrawl fallback per company; later pages stay public-web only."""

    def __init__(self, fetcher) -> None:
        self.fetcher = fetcher
        self.firecrawl_attempted = False

    def fetch(self, url: str):
        before = self.fetcher.requests
        if self.firecrawl_attempted:
            return self.fetcher.fallback.fetch(url)
        page = self.fetcher.fetch(url)
        if self.fetcher.requests > before:
            self.firecrawl_attempted = True
        return page


def _recovery_email(email: str, company_domain: str) -> bool:
    email = str(email or "").strip().casefold()
    if not is_valid_email_candidate(email):
        return False
    local = email.partition("@")[0]
    domain = mailbox_domain(email)
    return bool(
        domain
        and domain not in FREE_EMAIL_DOMAINS
        and local not in BLOCKED_LOCAL_PARTS
        and hosts_are_attributable(domain, company_domain)
    )


def _find_on_official_pages(company_domain: str, website: str, fetcher) -> tuple[str, str, str]:
    root = website.rstrip("/") or f"https://{company_domain}"
    seen: set[str] = set()
    budget = _RecoveryFetchBudget(fetcher)
    for suffix in RECOVERY_PAGE_PATHS:
        url = root if not suffix else urljoin(root + "/", suffix.lstrip("/"))
        if url in seen:
            continue
        seen.add(url)
        page = budget.fetch(url)
        if not page or not hosts_are_attributable(normalize_host(page.url), company_domain):
            continue
        for candidate in extract_contact_candidates(page.html, page.url):
            if _recovery_email(candidate.email, company_domain):
                return candidate.email, candidate.email_type, page.url
    return "", "", ""


def run_email_recovery(manager: ExcelManager, fetcher, enricher, *, max_companies: int = 50, dry_run: bool = True) -> tuple[RecoveryStats, dict[str, object]]:
    """Recover only public same-domain business mailboxes for existing qualified no-email leads."""
    outreach = manager.sheet(OUTREACH_SHEET)
    headers = manager.headers(outreach)
    recovery = manager.sheet(EMAIL_RECOVERY_SHEET)
    lead_rows = manager.sheet("Leads")
    lead_headers = manager.headers(lead_rows)
    outreach_index = {name: pos + 1 for pos, name in enumerate(headers)}
    lead_index = {name: pos + 1 for pos, name in enumerate(lead_headers)}
    lead_by_domain = {
        normalize_domain(lead_rows.cell(row, lead_index["公司官网"]).value): row
        for row in range(2, lead_rows.max_row + 1)
        if normalize_domain(lead_rows.cell(row, lead_index["公司官网"]).value)
    }
    stats = RecoveryStats()
    report_rows: list[list[str]] = []

    for row in range(2, outreach.max_row + 1):
        if stats.processed >= max_companies:
            break
        old_email = str(outreach.cell(row, outreach_index["Contact Email"]).value or "").strip()
        reason = str(outreach.cell(row, outreach_index["Final Send Block Reason"]).value or "")
        status = str(outreach.cell(row, outreach_index["Send Status"]).value or "").strip()
        if status or old_email.casefold() not in {"", "not found"} or "Company email domain missing" not in reason:
            continue
        company = str(outreach.cell(row, outreach_index["Company Name"]).value or "").strip()
        domain = normalize_domain(outreach.cell(row, outreach_index["Company Domain"]).value)
        source = str(outreach.cell(row, outreach_index["Source URL"]).value or "").strip()
        score = int(float(str(outreach.cell(row, outreach_index["Lead Score"]).value or 0)))
        lead_row = lead_by_domain.get(domain)
        lead_evidence = []
        if lead_row:
            lead_evidence = [
                str(lead_rows.cell(lead_row, lead_index[header]).value or "")
                for header in ("主营业务", "为什么判断它可能需要 PCB/PCBA", "所属行业")
            ]
        qualification = hardware_qualification(lead_evidence, company_name=company)
        if not domain or score < 4 or not qualification.eligible:
            continue
        stats.processed += 1
        website = f"https://{domain}"
        email, email_type, email_source = _find_on_official_pages(domain, website, fetcher)
        method = "Official website pages"
        if not email:
            enriched = enricher.find_public_business_email(domain)
            if _recovery_email(enriched, domain):
                email, email_type, email_source = enriched, "Public Work Email", f"Hunter domain search: {domain}"
                method = "Hunter Domain Search"
        if email:
            stats.recovered += 1
            outreach.cell(row, outreach_index["Contact Email"], email)
            outreach.cell(row, outreach_index["Email Type"], email_type)
            outreach.cell(row, outreach_index["Source URL"], "; ".join(dict.fromkeys([source, email_source])))
            outreach.cell(row, outreach_index["Email Source URL"], email_source)
            outreach.cell(row, outreach_index["Compliance Note"], RECOVERY_COMPLIANCE_NOTE)
            if lead_row:
                lead_reason = str(lead_rows.cell(lead_row, lead_index["为什么判断它可能需要 PCB/PCBA"]).value or "").strip()
                signal_text = ", ".join(qualification.hardware_signals)
                outreach.cell(row, outreach_index["Why This Angle Fits"], f"{lead_reason} hardware signals: {signal_text}.")
                lead_rows.cell(lead_row, lead_index["公开邮箱"], email)
                lead_rows.cell(lead_row, lead_index["Email Type"], email_type)
                lead_rows.cell(lead_row, lead_index["Email Source URL"], email_source)
                lead_rows.cell(lead_row, lead_index["Contact Method"], method)
                lead_rows.cell(lead_row, lead_index["数据来源链接"], "; ".join(dict.fromkeys([source, email_source])))
                lead_rows.cell(lead_row, lead_index["Compliance Note"], RECOVERY_COMPLIANCE_NOTE)
            safety = "Pending final workbook recheck"
            block_reason = ""
        else:
            safety = "BLOCKED"
            block_reason = "Contact Form Only / Manual Email Needed"
            outreach.cell(row, outreach_index["Email Type"], "Contact Form Only")
            if lead_row:
                lead_rows.cell(lead_row, lead_index["Email Type"], "Contact Form Only")
                lead_rows.cell(lead_row, lead_index["Contact Method"], "Contact Form Only / Manual Email Needed")
            stats.not_found += 1
        report_rows.append([company, website, old_email or "Not Found", email or "Not Found", email_type or "Not Found", email_source, method, safety, block_reason])

    manager.evaluate_auto_send_all()
    for report in report_rows:
        email = report[3]
        if email != "Not Found":
            matching = next((row for row in range(2, outreach.max_row + 1) if str(outreach.cell(row, outreach_index["Contact Email"]).value or "").casefold() == email.casefold()), None)
            if matching:
                report[7] = str(outreach.cell(matching, outreach_index["Final Send Safety Check"]).value or "")
                report[8] = str(outreach.cell(matching, outreach_index["Final Send Block Reason"]).value or "")
                stats.safety_passed += int(report[7] == "PASS")
        recovery.append(report)
    return stats, {"hunter": enricher.status(), "firecrawl": fetcher.usage(), "reports": report_rows}
