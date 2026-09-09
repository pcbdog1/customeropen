from pcb_leads.directory_sources import (
    DIRECTORY_SOURCES,
    directory_source_name,
    directory_queries,
    is_hardware_candidate,
    is_directory_url,
    is_likely_company_result,
    prospect_queries,
)


def test_directory_queries_include_regional_sources_without_russia():
    queries = directory_queries("Germany", "Industrial Electronics")

    assert any("site:industrystock.com" in item.query for item in queries)
    assert all("Russia" not in item.query for item in queries)
    assert len(queries) <= 2


def test_directory_queries_use_thomasnet_for_north_america():
    queries = directory_queries("USA", "Instrumentation")

    assert any("site:thomasnet.com" in item.query for item in queries)


def test_directory_source_name_normalizes_configured_sps_domain():
    url = "https://sps.mesago.com/en/exhibitor-directory"

    assert directory_source_name(url) == "SPS Exhibitors"
    assert is_directory_url(url)


def test_all_approved_european_countries_use_european_directory_sources():
    for country in ("Austria", "Belgium", "Denmark", "Finland", "Norway", "Ireland"):
        queries = directory_queries(country, "Industrial Electronics")

        assert any("site:industrystock.com" in item.query for item in queries), country


def test_hardware_filter_rejects_software_and_accepts_instruments():
    assert is_hardware_candidate(
        "Industrial sensor maker",
        "Measurement devices, embedded controllers, and instrumentation",
    )
    assert not is_hardware_candidate(
        "Cloud workflow platform",
        "SaaS consulting and enterprise software services",
    )


def test_hardware_filter_accepts_broad_pcb_buying_categories():
    assert is_hardware_candidate("Smart motor products", "Motor drives and electronic controls")
    assert is_hardware_candidate("Laboratory systems", "Analyzers and test equipment")


def test_hardware_filter_accepts_products_from_external_keyword_library():
    assert is_hardware_candidate(
        "Neural interface developer",
        "EEG headset and brain computer interface products",
    )
    assert is_hardware_candidate(
        "Analog photography company",
        "Film scanner and camera light meter products",
    )


def test_prospect_queries_are_domain_first_source_searches():
    queries = prospect_queries("Germany", "Sensor")

    source_types = {item.source_name for item in queries}
    assert {
        "Exhibitor Lists",
        "Manufacturer Directories",
        "Supplier Directories",
        "Association Members",
        "Product Directories",
        "Distributor Partners",
        "Industrial Directories",
        "PDF Catalogs",
    } <= source_types
    assert not any("@" in item.query for item in queries)
    assert len({item.query.casefold() for item in queries}) == len(queries)


def test_prospect_queries_prioritize_direct_company_manufacturer_contact_searches():
    queries = prospect_queries("USA", "Vintage Camera Electronics")
    direct_queries = [item for item in queries if item.source_name == "Direct Manufacturers"]

    assert direct_queries
    assert queries[: len(direct_queries)] == direct_queries
    assert all("site:" not in item.query.casefold() for item in direct_queries)
    assert all("manufacturer" in item.query.casefold() for item in direct_queries)
    assert all("contact" in item.query.casefold() for item in direct_queries)
    assert '"vintage camera electronics"' in direct_queries[0].query.casefold()
    assert len(queries) <= 12
    assert len({item.query.casefold() for item in queries}) == len(queries)


def test_prospect_queries_use_country_industry_and_product_manufacturer_terms():
    queries = prospect_queries("USA", "Vintage Camera Electronics")
    text = " ".join(item.query for item in queries)
    lowered = text.lower()

    assert "USA" in text
    assert "vintage camera electronics" in lowered
    assert "manufacturer" in lowered
    assert "catalog" in lowered
    assert len(queries) <= 12


def test_source_pool_includes_domain_first_source_kinds():
    kinds = {source.kind for source in DIRECTORY_SOURCES}
    domains = {source.domain for source in DIRECTORY_SOURCES}

    assert {"industrial_directory", "manufacturer", "supplier", "member", "product", "distributor", "exhibitor", "pdf_catalog"} <= kinds
    assert {"industrystock.com", "hannovermesse.de", "globalspec.com"} <= domains


def test_exhibitor_pool_covers_priority_hardware_trade_shows():
    names = {source.name for source in DIRECTORY_SOURCES if source.kind == "exhibitor"}

    assert {
        "Embedded World Exhibitors",
        "Electronica Exhibitors",
        "Productronica Exhibitors",
        "MEDICA Exhibitors",
        "COMPAMED Exhibitors",
        "Battery Show Europe Exhibitors",
        "Sensors Converge Exhibitors",
        "European Microwave Week Exhibitors",
        "Intersolar Exhibitors",
        "Railtex Exhibitors",
        "DSEI Exhibitors",
    } <= names


def test_company_result_filter_rejects_rankings_directories_and_generic_listing_titles():
    assert not is_likely_company_result("Top IoT companies in Argentina", "https://goodfirms.co/iot/argentina")
    assert not is_likely_company_result("Encontrá a tu proveedor", "https://iot.org.ar/proveedores")
    assert not is_likely_company_result("Distributors", "https://vendor.example/distributors")
    assert not is_likely_company_result(
        "Solar Inverter Manufacturers from Argentina",
        "https://enfsolar.com/directory/inverter",
    )


def test_company_result_filter_keeps_named_hardware_companies():
    assert is_likely_company_result("ISRA VISION", "https://isravision.com")
    assert is_likely_company_result("About Us - Machvision", "https://machvision.com.ar/about-us")


def test_company_result_filter_rejects_recruitment_news_and_hosting_platforms():
    assert not is_likely_company_result("Hardware Engineering Jobs", "https://glassdoor.com/jobs/hardware")
    assert not is_likely_company_result("ACME press release", "https://prnewswire.com/news/acme")
    assert not is_likely_company_result("Domain parking", "https://godaddy.com/forsale/acme")
