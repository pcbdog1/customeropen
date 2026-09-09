from __future__ import annotations

import os

import requests

from env_config import load_project_env
from pcb_leads.parser import FREE_EMAIL_DOMAINS, is_valid_email_candidate
from pcb_leads.utils import hosts_are_attributable, mailbox_domain, normalize_domain


class HunterEmailEnricher:
    """Optional Hunter lookup for attributable public work or generic company email."""

    def __init__(self, api_timeout: int = 20) -> None:
        load_project_env()
        self.enabled = os.getenv("EMAIL_ENRICHMENT_ENABLED", "True").casefold() == "true"
        self.api_key = os.getenv("HUNTER_API_KEY", "").strip()
        self.api_timeout = api_timeout
        self.requests = 0
        self.emails_found = 0
        self.failures: list[str] = []

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def status(self) -> dict[str, object]:
        return {"configured": self.configured, "requests": self.requests, "emails": self.emails_found, "failure_reason": self.failures[-1] if self.failures else ""}

    def find_public_business_email(self, domain: str) -> str:
        company_domain = normalize_domain(domain)
        if not self.configured or not company_domain:
            return ""
        self.requests += 1
        try:
            response = requests.get("https://api.hunter.io/v2/domain-search", params={"domain": company_domain, "api_key": self.api_key}, timeout=self.api_timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError) as exc:
            self.failures.append(f"{type(exc).__name__}: {str(exc)[:180]}")
            return ""
        for item in payload.get("data", {}).get("emails", []):
            email = str(item.get("value", "")).strip().casefold()
            if is_valid_email_candidate(email) and mailbox_domain(email) not in FREE_EMAIL_DOMAINS and hosts_are_attributable(mailbox_domain(email), company_domain):
                self.emails_found += 1
                return email
        return ""
