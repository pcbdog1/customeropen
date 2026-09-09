import json

from pcb_leads.keyword_library import (
    build_query_batch,
    DOMAIN_ENRICHMENT_INTENTS,
    domain_enrichment_queries,
    industry_names,
    learn_public_terms,
    load_keyword_library,
    primary_product,
    record_search_outcome,
)


def test_domain_enrichment_queries_cover_required_intents_independently():
    queries = domain_enrichment_queries("acme-controls.de")

    assert DOMAIN_ENRICHMENT_INTENTS == (
        "@domain",
        "email",
        "contact",
        "sales",
        "info",
        "Impressum",
        "Kontakt",
        "PDF",
    )
    assert len(queries) == len(DOMAIN_ENRICHMENT_INTENTS)
    assert all("acme-controls.de" in query for query in queries)
    assert len({query.casefold() for query in queries}) == len(queries)


def test_library_loads_unique_categories_products_jobs_and_procurement_terms():
    library = load_keyword_library()

    assert len(library.industries) >= 30
    assert len(library.job_titles) >= 20
    assert len(library.procurement_terms) >= 30
    assert "Neurotechnology and Brain Computer Interface" in industry_names()

    for values in (
        library.industries.keys(),
        (product for products in library.industries.values() for product in products),
        library.job_titles,
        library.procurement_terms,
    ):
        normalized = [value.casefold() for value in values]
        assert len(normalized) == len(set(normalized))


def test_query_batch_treats_industry_product_job_and_procurement_as_or_intents():
    queries = build_query_batch(
        "Germany",
        "Neurotechnology and Brain Computer Interface",
        max_queries=6,
        rotation=0,
    )

    assert 1 <= len(queries) <= 6
    assert all("Germany" in query for query in queries)
    assert any("brain computer interface" in query for query in queries)
    assert any("Hardware Engineer" in query for query in queries)
    assert any("PCB" in query or "PCBA" in query for query in queries)
    assert any("info@" in query and "sales@" in query for query in queries)
    assert not any("Hardware Engineer" in query and "PCB" in query for query in queries)
    assert not any("brain computer interface" in query.lower() and "Hardware Engineer" in query for query in queries)


def test_three_zero_outcomes_advance_expansion_level(tmp_path):
    state_path = tmp_path / "growth.json"

    first = record_search_outcome(0, state_path=state_path)
    second = record_search_outcome(0, state_path=state_path)
    third = record_search_outcome(0, state_path=state_path)

    assert first["consecutive_zero_result_runs"] == 1
    assert second["consecutive_zero_result_runs"] == 2
    assert third["consecutive_zero_result_runs"] == 0
    assert third["expansion_level"] == 1
    assert third["expansion_history"]


def test_success_resets_zero_outcome_streak(tmp_path):
    state_path = tmp_path / "growth.json"
    record_search_outcome(0, state_path=state_path)
    state = record_search_outcome(2, state_path=state_path)

    assert state["consecutive_zero_result_runs"] == 0


def test_primary_product_ignores_page_text_learned_for_attribution(tmp_path):
    library_path = tmp_path / "library.json"
    learned_path = tmp_path / "learned.json"
    library_path.write_text('{"industries": {}, "job_titles": [], "procurement_terms": []}')
    learned_path.write_text(
        '{"industries": {"Instrumentation": ["enquiry relates to TSI Instrument"]}}'
    )

    assert primary_product(
        "Instrumentation",
        rotation=297,
        path=library_path,
        learned_path=learned_path,
    ) == "Instrumentation"


def test_public_term_learning_is_attributed_and_deduplicated(tmp_path):
    learned_path = tmp_path / "learned.json"
    text = "We manufacture cryogenic quantum controller systems and hire a Quantum Hardware Engineer."

    added_first = learn_public_terms(
        text,
        industry="Research Quantum Cryogenic and High Voltage",
        source_url="https://example.com/products",
        learned_path=learned_path,
    )
    added_second = learn_public_terms(
        text,
        industry="Research Quantum Cryogenic and High Voltage",
        source_url="https://example.com/products",
        learned_path=learned_path,
    )

    saved = json.loads(learned_path.read_text())
    assert added_first
    assert added_second == []
    assert saved["evidence"]
    assert all(item["source_url"] == "https://example.com/products" for item in saved["evidence"])


def test_learned_terms_merge_back_into_the_next_library_load(tmp_path):
    learned_path = tmp_path / "learned.json"
    learn_public_terms(
        "Cryogenic quantum controller systems from our laboratory team",
        industry="Research Quantum Cryogenic and High Voltage",
        source_url="https://example.com/products",
        learned_path=learned_path,
    )

    library = load_keyword_library(learned_path=learned_path)
    products = library.industries["Research Quantum Cryogenic and High Voltage"]

    assert any("cryogenic quantum controller systems" in value.casefold() for value in products)
