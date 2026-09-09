from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .utils import normalize_domain
from .keyword_library import all_products, load_growth_state, primary_product


@dataclass(frozen=True)
class DirectorySource:
    name: str
    domain: str
    regions: frozenset[str] = frozenset()
    kind: str = "directory"


@dataclass(frozen=True)
class DirectoryQuery:
    source_name: str
    query: str


EUROPE = frozenset(
    {
        "Germany",
        "Netherlands",
        "Sweden",
        "Switzerland",
        "UK",
        "France",
        "Italy",
        "Spain",
        "Austria",
        "Belgium",
        "Denmark",
        "Finland",
        "Norway",
        "Ireland",
    }
)
NORTH_AMERICA = frozenset({"USA", "Canada", "Mexico"})

DIRECTORY_SOURCES = (
    DirectorySource("IndustryStock", "industrystock.com", EUROPE, "industrial_directory"),
    DirectorySource("Europages", "europages.com", EUROPE, "supplier"),
    DirectorySource("Kompass", "kompass.com", frozenset(), "manufacturer"),
    DirectorySource("DirectIndustry", "directindustry.com", frozenset(), "product"),
    DirectorySource("MedicalExpo", "medicalexpo.com", frozenset(), "product"),
    DirectorySource("Thomasnet", "thomasnet.com", NORTH_AMERICA, "manufacturer"),
    DirectorySource("Trade Show Exhibitors", "eventseye.com", frozenset(), "exhibitor"),
    DirectorySource("Industry Directory", "industrynet.com", NORTH_AMERICA, "industrial_directory"),
    DirectorySource("GlobalSpec", "globalspec.com", frozenset(), "product"),
    DirectorySource("IQS Directory", "iqsdirectory.com", NORTH_AMERICA, "supplier"),
    DirectorySource("ExportHub", "exporthub.com", frozenset(), "supplier"),
    DirectorySource("ODVA Members", "odva.org", frozenset(), "member"),
    DirectorySource("Distributor Partners", "digikey.com", frozenset(), "distributor"),
    DirectorySource("PDF Catalogs", "datasheetarchive.com", frozenset(), "pdf_catalog"),
    DirectorySource("Hannover Messe Exhibitors", "hannovermesse.de", EUROPE, "exhibitor"),
    DirectorySource("SPS Exhibitors", "sps.mesago.com", EUROPE, "exhibitor"),
    DirectorySource("MEDICA Exhibitors", "medica-tradefair.com", EUROPE, "exhibitor"),
    DirectorySource("Automate Exhibitors", "automateshow.com", NORTH_AMERICA, "exhibitor"),
    DirectorySource("CES Exhibitors", "ces.tech", NORTH_AMERICA, "exhibitor"),
    DirectorySource("Embedded World Exhibitors", "embedded-world.de", EUROPE, "exhibitor"),
    DirectorySource("Electronica Exhibitors", "electronica.de", EUROPE, "exhibitor"),
    DirectorySource("Productronica Exhibitors", "productronica.com", EUROPE, "exhibitor"),
    DirectorySource("Automatica Exhibitors", "automatica-munich.com", EUROPE, "exhibitor"),
    DirectorySource("COMPAMED Exhibitors", "compamed-tradefair.com", EUROPE, "exhibitor"),
    DirectorySource("Battery Show Europe Exhibitors", "thebatteryshow.eu", EUROPE, "exhibitor"),
    DirectorySource("Battery Show North America Exhibitors", "thebatteryshow.com", NORTH_AMERICA, "exhibitor"),
    DirectorySource("Sensors Converge Exhibitors", "sensorsconverge.com", NORTH_AMERICA, "exhibitor"),
    DirectorySource("MD&M West Exhibitors", "mdmwest.com", NORTH_AMERICA, "exhibitor"),
    DirectorySource("Design-2-Part Exhibitors", "d2p.com", NORTH_AMERICA, "exhibitor"),
    DirectorySource("European Microwave Week Exhibitors", "eumweek.com", EUROPE, "exhibitor"),
    DirectorySource("ECOC Exhibitors", "ecocexhibition.com", EUROPE, "exhibitor"),
    DirectorySource("Intersolar Exhibitors", "intersolar.de", EUROPE, "exhibitor"),
    DirectorySource("ees Europe Exhibitors", "ees-europe.com", EUROPE, "exhibitor"),
    DirectorySource("Intertraffic Exhibitors", "intertraffic.com", EUROPE, "exhibitor"),
    DirectorySource("Railtex Exhibitors", "railtex.co.uk", EUROPE, "exhibitor"),
    DirectorySource("DSEI Exhibitors", "dsei.co.uk", EUROPE, "exhibitor"),
    DirectorySource("Eurosatory Exhibitors", "eurosatory.com", EUROPE, "exhibitor"),
)

HARDWARE_TERMS = frozenset(
    {
        "hardware",
        "electronics",
        "electronic",
        "device",
        "equipment",
        "instrument",
        "analyzer",
        "sensor",
        "controller",
        "control",
        "robot",
        "automation",
        "machine",
        "motor",
        "drive",
        "battery",
        "charger",
        "inverter",
        "medical",
        "embedded",
        "iot",
        "camera",
        "vision",
        "telecom",
        "wireless",
        "meter",
        "monitor",
        "laboratory",
        "lighting",
        "appliance",
        "mechatronic",
    }
    | {product.casefold() for product in all_products()}
)
SOFTWARE_ONLY_TERMS = frozenset({"saas", "consulting", "cloud platform", "software services", "web agency"})
NON_COMPANY_DISCOVERY_DOMAINS = frozenset(
    {
        "goodfirms.co",
        "enfsolar.com",
        "clutch.co",
        "designrush.com",
        "capterra.com",
        "g2.com",
        "iot.org.ar",
        "glassdoor.com",
        "indeed.com",
        "ziprecruiter.com",
        "monster.com",
        "godaddy.com",
        "prnewswire.com",
        "businesswire.com",
        "globenewswire.com",
    }
)
GENERIC_LISTING_PHRASES = (
    "top ",
    "find a supplier",
    "encontra a tu proveedor",
    "manufacturers from",
    "manufacturers in",
    "supplier directory",
    "list of ",
)


def directory_queries(country: str, industry: str) -> list[DirectoryQuery]:
    if country == "Russia":
        return []
    eligible: list[DirectorySource] = []
    for source in DIRECTORY_SOURCES:
        if source.regions and country not in source.regions:
            continue
        eligible.append(source)
    if country in EUROPE:
        preferred = [source for source in eligible if source.name in {"IndustryStock", "Europages"}]
    elif country in NORTH_AMERICA:
        preferred = [source for source in eligible if source.name in {"Thomasnet", "Industry Directory"}]
    else:
        preferred = [source for source in eligible if source.name in {"Kompass", "DirectIndustry"}]
    rotation = int(load_growth_state().get("expansion_level", 0))
    product = primary_product(industry, rotation)
    anchor = preferred[:1]
    rotating = [source for source in eligible if source not in anchor]
    extra = [rotating[rotation % len(rotating)]] if rotating else []
    sources = anchor + extra
    queries: list[DirectoryQuery] = []
    for source in sources:
        if source.kind == "customs":
            search_terms = f'{country} (PCB OR PCBA OR "printed circuit board") importer buyer'
        elif source.kind == "exhibitor":
            search_terms = f'{country} exhibitor electronics hardware'
        elif source.kind == "ranking":
            search_terms = f'{country} hardware electronics companies'
        else:
            search_terms = f'{country} "{product}" manufacturer'
        queries.append(
            DirectoryQuery(
                source_name=source.name,
                query=f'site:{source.domain} {search_terms}',
            )
        )
    return queries


def prospect_queries(country: str, industry: str) -> list[DirectoryQuery]:
    if country == "Russia":
        return []
    rotation = int(load_growth_state().get("expansion_level", 0))
    product = primary_product(industry, rotation)
    direct_targets = dict.fromkeys((industry, product))
    queries = [
        DirectoryQuery(
            "Direct Manufacturers",
            f'{country} "{target}" manufacturer contact',
        )
        for target in direct_targets
    ]
    queries.extend(directory_queries(country, industry))
    domain_first = (
        ("Exhibitor Lists", f'{country} "{industry}" exhibitor manufacturer'),
        ("Manufacturer Directories", f'{country} "{product}" manufacturer directory'),
        ("Supplier Directories", f'{country} "{product}" supplier directory'),
        ("Association Members", f'{country} "{industry}" association members manufacturers'),
        ("Product Directories", f'{country} "{product}" product directory manufacturers'),
        ("Distributor Partners", f'{country} "{product}" distributor partner manufacturer'),
        ("Industrial Directories", f'{country} "{industry}" industrial directory manufacturers'),
        ("PDF Catalogs", f'{country} "{product}" manufacturer catalog filetype:pdf'),
    )
    queries.extend(DirectoryQuery(source_name, query) for source_name, query in domain_first)
    unique = {(item.source_name.casefold(), item.query.casefold()): item for item in queries}
    return list(unique.values())[:12]


def is_hardware_candidate(title: str, snippet: str) -> bool:
    text = f"{title} {snippet}".lower()
    has_hardware = any(term in text for term in HARDWARE_TERMS)
    if not has_hardware:
        return False
    software_signals = sum(term in text for term in SOFTWARE_ONLY_TERMS)
    hardware_signals = sum(term in text for term in HARDWARE_TERMS)
    return software_signals == 0 or hardware_signals >= 2


def is_likely_company_result(title: str, url: str) -> bool:
    domain = normalize_domain(url)
    if domain in NON_COMPANY_DISCOVERY_DOMAINS:
        return False
    normalized_title = "".join(
        char for char in unicodedata.normalize("NFKD", title.lower()) if not unicodedata.combining(char)
    ).strip()
    if normalized_title in {"distributors", "manufacturers", "suppliers", "company directory"}:
        return False
    return not any(phrase in normalized_title for phrase in GENERIC_LISTING_PHRASES)


def directory_source_name(url: str) -> str:
    domain = normalize_domain(url)
    for source in DIRECTORY_SOURCES:
        source_domain = normalize_domain(source.domain)
        if domain == source_domain or domain.endswith("." + source_domain):
            return source.name
    return ""


def is_directory_url(url: str) -> bool:
    return bool(directory_source_name(url))
