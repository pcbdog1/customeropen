from __future__ import annotations

import argparse
from pathlib import Path

from .config import AppConfig
from .excel_store import ExcelLeadStore
from .fetcher import PublicPageFetcher
from .models import Lead
from .parser import (
    extract_city,
    extract_contact_candidates,
    extract_links,
    extract_phones,
    extract_title,
    likely_company_name_from_url_or_title,
    visible_text,
)
from .scorer import demand_signal_from_text, infer_industry, is_likely_peer_manufacturer, score_candidate
from .search import SearchResult, default_search_provider
from .state import RunState
from .utils import UNKNOWN, country_to_phone_region, dedupe_key, normalize_domain


COMPLIANCE_NOTE = (
    "Public business contact collected from source URL. "
    "B2B relevance: PCB/PCBA manufacturing service matches company hardware/electronics business. "
    "Outreach email must include opt-out sentence."
)


def build_lead(
    result: SearchResult,
    html: str,
    final_url: str,
    country: str,
    fallback_industry: str,
    extra_source_links: list[str] | None = None,
) -> Lead | None:
    text = visible_text(html)
    combined = " ".join([result.title, result.snippet, text[:12000]])
    if is_likely_peer_manufacturer(combined):
        return None

    title = extract_title(html)
    company_name = result.title if result.query.startswith("seed fallback:") else likely_company_name_from_url_or_title(final_url, title)
    industry = infer_industry(combined, fallback_industry)
    contacts = extract_contact_candidates(html, final_url)
    contact = contacts[0]
    phones = extract_phones(html, country_to_phone_region(country))
    links = extract_links(html, final_url)
    score, reason = score_candidate(combined, industry, bool(contact.email != "Not Found" or phones))
    if score < 3:
        return None

    signal = demand_signal_from_text(combined)
    angle = development_angle(industry)
    subject = email_subject(industry)
    source_values = {
        result.url,
        final_url,
        links.get("contact", ""),
        links.get("impressum", ""),
        links.get("legal", ""),
        links.get("privacy", ""),
        links.get("team", ""),
        links.get("careers", ""),
        *(extra_source_links or []),
    }
    source_links = "; ".join(sorted(source_values - {""}))
    contact_method = contact.contact_method
    if contact.email == "Not Found" and contact.contact_form_url:
        contact_method = "Website Contact Form"
    elif contact.email == "Not Found" and links.get("linkedin"):
        contact_method = "LinkedIn"
    elif contact.email == "Not Found" and phones:
        contact_method = "Phone"

    notes = reason
    if contact.email == "Not Found":
        notes = f"{reason}; 邮箱未公开，建议用官网表单或LinkedIn开发; High Potential / Need manual contact check"

    return Lead(
        country=country,
        city=extract_city(html),
        company_name=company_name,
        website=f"https://{normalize_domain(final_url)}",
        business=summarize_business(combined, industry),
        industry=industry,
        pcb_need_reason=pcb_need_reason(industry, signal),
        demand_signal=signal,
        demand_signal_source=result.query,
        employees="未找到",
        founded="未找到",
        email=contact.email,
        email_type=contact.email_type,
        contact_method=contact_method,
        contact_form_url=contact.contact_form_url,
        contact_name=UNKNOWN,
        contact_title=UNKNOWN,
        phone=phones[0] if phones else UNKNOWN,
        linkedin=links.get("linkedin", UNKNOWN),
        source_links=source_links,
        score=score,
        development_angle=angle,
        email_subject=subject,
        notes=notes,
        compliance_note=COMPLIANCE_NOTE,
    )


def summarize_business(text: str, industry: str) -> str:
    if industry == "Robotics":
        return "Robotics / automation hardware products and related engineering"
    if "Automation" in industry:
        return "Industrial automation equipment, controls, sensors, or electronics"
    if "Medical" in industry:
        return "Medical device or healthcare electronics"
    if "Sensor" in industry:
        return "Sensors, instrumentation, or measurement electronics"
    return f"{industry} hardware or electronics products"


def pcb_need_reason(industry: str, signal: str) -> str:
    return f"{industry} products usually include controllers, embedded electronics, sensors, firmware, or assembled PCBAs; public signal: {signal}."


def development_angle(industry: str) -> str:
    if industry == "Robotics":
        return "机器人控制板PCBA打样和中小批量组装"
    if "Automation" in industry:
        return "工业控制板PCBA替代供应商和小批量生产"
    if "Medical" in industry:
        return "医疗设备电子控制板高可靠PCBA"
    return "嵌入式电子产品PCB/PCBA打样和中小批量生产"


def email_subject(industry: str) -> str:
    if industry == "Robotics":
        return "PCB & PCBA Support for Robotics Control Electronics"
    if "Automation" in industry:
        return "PCB & PCBA Manufacturing Support for Industrial Automation Electronics"
    return f"PCB & PCBA Manufacturing Support for {industry} Products"


def collect_leads(config: AppConfig) -> list[Lead]:
    state = RunState.load(config.state_path)
    provider = default_search_provider(config.user_agent, config.request_timeout)
    fetcher = PublicPageFetcher(config.user_agent, config.request_timeout)
    store = ExcelLeadStore(config.output_path)
    existing_keys = store.load_existing_keys()
    leads: list[Lead] = []
    seen_keys = set(existing_keys)

    for industry, query in config.queries():
        if len(leads) >= config.limit:
            break
        if state.has_completed_query(query):
            continue
        if config.dry_run:
            state.mark_query_completed(query)
            continue

        results = provider.search(query, limit=max(config.limit * 2, 20))
        for result in results:
            if len(leads) >= config.limit:
                break
            if state.has_seen_url(result.url):
                continue
            state.mark_url_seen(result.url)

            page = fetcher.fetch(result.url)
            if not page:
                continue
            detail_html, detail_urls = fetch_detail_pages(fetcher, page.html, page.url, config.country)
            combined_html = "\n".join([page.html, detail_html])
            lead = build_lead(result, combined_html, page.url, config.country, industry, detail_urls)
            if not lead:
                continue
            key = dedupe_key(lead.company_name, lead.website)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            leads.append(lead)
        state.mark_query_completed(query)
        state.save()

    if leads:
        store.append_leads(leads)
    state.save()
    return leads


def fetch_detail_pages(fetcher: PublicPageFetcher, html: str, base_url: str, country: str) -> tuple[str, list[str]]:
    links = extract_links(html, base_url)
    priority = ["contact"]
    if country.lower() == "germany":
        priority.extend(["impressum", "legal", "privacy", "team", "careers"])
    else:
        priority.extend(["legal", "team", "careers"])

    collected_html: list[str] = []
    collected_urls: list[str] = []
    for key in priority:
        url = links.get(key)
        if not url or url in collected_urls:
            continue
        page = fetcher.fetch(url)
        if not page:
            continue
        collected_html.append(page.html)
        collected_urls.append(page.url)
    return "\n".join(collected_html), collected_urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find public PCB/PCBA prospect leads and save them to Excel.")
    parser.add_argument("--country", default="Germany")
    parser.add_argument("--industries", nargs="+", default=["Robotics", "Automation"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--state", default="state/run_state.json")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and state without network fetches.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = AppConfig(
        country=args.country,
        industries=args.industries,
        limit=args.limit,
        output_dir=Path(args.output_dir),
        state_path=Path(args.state),
        dry_run=args.dry_run,
    )
    leads = collect_leads(config)
    print(f"Added {len(leads)} leads to {config.output_path}")


if __name__ == "__main__":
    main()
