from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_COUNTRY = "Germany"
DEFAULT_INDUSTRIES = ["Robotics", "Automation"]

SEARCH_QUERIES = {
    "Germany": {
        "Robotics": [
            "hardware engineer robotics company Germany electronics",
            "robotics company Germany embedded engineer",
            "robotics controller PCB Germany",
            "firmware engineer robotics startup Germany",
        ],
        "Automation": [
            "embedded engineer automation company Germany",
            "industrial automation electronics company Germany",
            "PCB design engineer industrial automation Germany",
            "automation equipment manufacturer Germany electronics",
        ],
    }
}


@dataclass
class AppConfig:
    country: str = DEFAULT_COUNTRY
    industries: list[str] | None = None
    limit: int = 20
    output_dir: Path = Path("outputs")
    state_path: Path = Path("state/run_state.json")
    request_timeout: int = 15
    user_agent: str = "PCBDOGLeadResearchBot/0.1 (+public business contact research; no login; respects robots.txt)"
    dry_run: bool = False

    @property
    def selected_industries(self) -> list[str]:
        return self.industries or DEFAULT_INDUSTRIES

    @property
    def output_path(self) -> Path:
        from datetime import date

        return self.output_dir / f"PCB_PCBA_Leads_Europe_USA_{date.today():%Y%m%d}.xlsx"

    def queries(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        by_country = SEARCH_QUERIES.get(self.country, {})
        for industry in self.selected_industries:
            for query in by_country.get(industry, []):
                pairs.append((industry, query))
        return pairs
