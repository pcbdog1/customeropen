from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pcb_leads.keyword_library import industry_names

DAILY_LEAD_TARGET = 20
DAILY_RUN_TIME = "10:00"
AUTO_SEND_ENABLED = True
REQUIRE_MANUAL_APPROVAL = False
MAX_DAILY_SEND_LIMIT = 100
SEND_DELAY_MIN_SECONDS = 60
SEND_DELAY_MAX_SECONDS = 180
MIN_SEND_TARGET_PER_RUN = 10
CYCLE_SEND_TARGET = 10
# A cycle must keep looking beyond the first country/industry combination when
# it has not reached the ten-email target.  Twelve combinations were too small
# for contact-poor regions and routinely ended runs below target.
MAX_FALLBACK_COMBINATIONS = 60
MAX_DIRECTORY_CANDIDATES = 120
MAX_SEARCH_QUERIES = 300
MAX_DOMAINS_PER_SOURCE = 50
MAX_FETCHED_PAGES_PER_DOMAIN = 5
MAX_POOL_DOMAINS_PER_RUN = 20
MAX_POOL_FETCHED_PAGES_PER_DOMAIN = 3
API_DOMAIN_DISCOVERY_LIMIT = 50
MAX_EMAIL_RECOVERY_PER_RUN = 50

PRIMARY_COUNTRIES = (
    "Germany",
    "UK",
    "USA",
    "Netherlands",
    "Switzerland",
    "Sweden",
    "France",
    "Italy",
    "Spain",
    "Canada",
    "Austria",
    "Belgium",
    "Denmark",
    "Finland",
    "Norway",
    "Ireland",
)
HIGH_YIELD_INDUSTRIES = (
    "Industrial Electronics",
    "Test and Measurement Equipment",
    "Sensor Manufacturer",
    "Medical Device Electronics",
    "Automation Equipment",
    "Motor Controller",
    "Power Electronics",
    "EV Charger",
    "Battery Management System",
    "IoT Gateway",
    "Machine Vision",
    "Industrial Camera",
    "RF Module",
    "Laboratory Instrument",
    "Embedded Controller",
    "Environmental Monitoring Device",
    "Industrial PC",
    "Data Acquisition System",
    "RFID Reader",
    "Access Control System",
)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True)
class LoopConfig:
    workbook_path: Path = Path("outputs/customeropen_leads.xlsx")
    domain_pool_path: Path = Path("outputs/company_domain_pool.xlsx")
    loop_state_path: Path = Path("state/loop_state.json")
    report_dir: Path = Path("outputs/daily_reports")
    log_dir: Path = Path("logs")
    daily_run_time: str = DAILY_RUN_TIME
    auto_send_enabled: bool = AUTO_SEND_ENABLED
    require_manual_approval: bool = REQUIRE_MANUAL_APPROVAL
    daily_lead_target: int = DAILY_LEAD_TARGET
    max_daily_send_limit: int = MAX_DAILY_SEND_LIMIT
    send_delay_min_seconds: int = SEND_DELAY_MIN_SECONDS
    send_delay_max_seconds: int = SEND_DELAY_MAX_SECONDS
    min_send_target_per_run: int = MIN_SEND_TARGET_PER_RUN
    cycle_send_target: int = CYCLE_SEND_TARGET
    max_fallback_combinations: int = MAX_FALLBACK_COMBINATIONS
    max_directory_candidates: int = MAX_DIRECTORY_CANDIDATES
    max_search_queries: int = MAX_SEARCH_QUERIES
    max_domains_per_source: int = MAX_DOMAINS_PER_SOURCE
    max_fetched_pages_per_domain: int = MAX_FETCHED_PAGES_PER_DOMAIN
    max_pool_domains_per_run: int = MAX_POOL_DOMAINS_PER_RUN
    max_pool_fetched_pages_per_domain: int = MAX_POOL_FETCHED_PAGES_PER_DOMAIN
    api_domain_discovery_limit: int = API_DOMAIN_DISCOVERY_LIMIT
    max_email_recovery_per_run: int = MAX_EMAIL_RECOVERY_PER_RUN
    countries: list[str] = field(default_factory=lambda: list(PRIMARY_COUNTRIES))
    industries: list[str] = field(
        default_factory=lambda: _ordered_unique(list(HIGH_YIELD_INDUSTRIES) + [
            "Robotics",
            "Automation",
            "Medical Device",
            "Industrial Electronics",
            "AI Hardware",
            "IoT Device",
            "Sensor",
            "Instrumentation",
            "Laboratory Equipment",
            "Machine Vision",
            "RF / Telecom",
            "Energy Storage",
            "Solar Inverter",
            "EV Charger",
            "BMS",
            "Motor Drives",
            "Building Automation",
            "Smart Appliances",
            "Consumer Electronics",
            "Lighting Control",
        ]
        + list(industry_names()))
    )


@dataclass
class LoopState:
    automation_start_date: str = field(default_factory=lambda: date.today().isoformat())
    running_day: int = 1
    dynamic_daily_send_limit: int = 20
    send_throttle_reason: str = ""
    auto_send_paused: bool = False
    country_index: int = 0
    industry_index: int = 0
    last_run_date: str = ""
    consecutive_high_failure_days: int = 0

    @classmethod
    def load(cls, path: Path) -> "LoopState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            automation_start_date=str(data.get("Automation Start Date") or data.get("automation_start_date") or date.today().isoformat()),
            running_day=int(data.get("Running Day") or data.get("running_day") or 1),
            dynamic_daily_send_limit=int(data.get("Dynamic Daily Send Limit") or data.get("dynamic_daily_send_limit") or 20),
            send_throttle_reason=str(data.get("Send Throttle Reason") or data.get("send_throttle_reason") or ""),
            auto_send_paused=bool(data.get("Auto Send Paused") if "Auto Send Paused" in data else data.get("auto_send_paused", False)),
            country_index=int(data.get("Current Country Index") if "Current Country Index" in data else data.get("country_index", 0)),
            industry_index=int(data.get("Current Industry Index") if "Current Industry Index" in data else data.get("industry_index", 0)),
            last_run_date=str(data.get("Last Run Date") or data.get("last_run_date") or ""),
            consecutive_high_failure_days=int(data.get("Consecutive High Failure Days") or data.get("consecutive_high_failure_days") or 0),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "Automation Start Date": self.automation_start_date,
                    "Running Day": self.running_day,
                    "Dynamic Daily Send Limit": self.dynamic_daily_send_limit,
                    "Send Throttle Reason": self.send_throttle_reason,
                    "Auto Send Paused": self.auto_send_paused,
                    "Current Country Index": self.country_index,
                    "Current Industry Index": self.industry_index,
                    "Last Run Date": self.last_run_date,
                    "Consecutive High Failure Days": self.consecutive_high_failure_days,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def current_country(self, config: LoopConfig) -> str:
        return config.countries[self.country_index % len(config.countries)]

    def current_industry(self, config: LoopConfig) -> str:
        return config.industries[self.industry_index % len(config.industries)]

    def next(self, config: LoopConfig) -> "LoopState":
        industry_index = self.industry_index + 1
        country_index = self.country_index
        if industry_index >= len(config.industries):
            industry_index = 0
            country_index = (country_index + 1) % len(config.countries)
        return LoopState(
            automation_start_date=self.automation_start_date,
            running_day=self.running_day,
            dynamic_daily_send_limit=self.dynamic_daily_send_limit,
            send_throttle_reason=self.send_throttle_reason,
            auto_send_paused=self.auto_send_paused,
            country_index=country_index,
            industry_index=industry_index,
            last_run_date=self.last_run_date,
            consecutive_high_failure_days=self.consecutive_high_failure_days,
        )


def dynamic_limit_for_day(running_day: int) -> int:
    if running_day <= 7:
        return 20
    if running_day <= 14:
        return 50
    return 100
