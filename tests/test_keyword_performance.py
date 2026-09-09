import json

from keyword_performance import KeywordPerformanceStore


def test_three_zero_email_searches_pause_and_rank_keyword_last(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")

    for _ in range(3):
        store.record("Germany sensor sales@", "email-first", 5, 0, 0)

    ordered = store.ordered(
        [
            ("Germany sensor sales@", "email-first"),
            ("Germany sensor manufacturer", "manufacturer"),
        ]
    )

    assert ordered[-1] == ("Germany sensor sales@", "email-first")
    saved = json.loads((tmp_path / "performance.json").read_text())
    record = next(iter(saved["queries"].values()))
    assert record["paused"] is True
    assert record["consecutive_zero_email_count"] == 3


def test_email_yield_orders_queries_before_untested_and_zero_yield(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")
    store.record("high", "email-first", 5, 3, 2)
    store.record("low", "email-first", 8, 1, 1)
    store.record("zero", "email-first", 8, 0, 0)

    ordered = store.ordered(
        [
            ("zero", "email-first"),
            ("low", "email-first"),
            ("new", "email-first"),
            ("high", "email-first"),
        ]
    )

    assert [query for query, _ in ordered] == ["high", "low", "new", "zero"]


def test_record_accumulates_search_candidate_email_lead_and_send_counts(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")

    store.record("query", "directory", 6, 2, 1)
    store.record("query", "directory", 4, 1, 1, sent=1)

    top = store.top_queries(1)
    assert top[0]["search_count"] == 2
    assert top[0]["candidate_count"] == 10
    assert top[0]["email_count"] == 3
    assert top[0]["new_lead_count"] == 2
    assert top[0]["successful_send_count"] == 1


def test_record_adds_domain_and_hardware_counts_compatibly(tmp_path):
    path = tmp_path / "performance.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "queries": {
                    "directory::old": {
                        "query": "old",
                        "source_type": "directory",
                        "search_count": 2,
                        "candidate_count": 4,
                        "email_count": 1,
                        "new_lead_count": 1,
                        "successful_send_count": 0,
                    }
                },
            }
        )
    )
    store = KeywordPerformanceStore(path)

    store.record(
        "old",
        "directory",
        3,
        0,
        0,
        new_domains=2,
        qualified_hardware=1,
    )

    record = store.top_queries(1)[0]
    assert record["new_domain_count"] == 2
    assert record["qualified_hardware_count"] == 1
    assert record["search_count"] == 3
    assert record["consecutive_zero_email_count"] == 1


def test_omitted_exact_metrics_do_not_fall_back_to_candidate_or_lead_proxies(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")

    store.record("query", "directory", candidates=8, emails=2, new_leads=1)

    record = store.top_queries(1)[0]
    assert record["candidate_count"] == 8
    assert record["new_lead_count"] == 1
    assert record["new_domain_count"] == 0
    assert record["qualified_hardware_count"] == 0


def test_record_sent_does_not_increment_search_or_zero_email_streak(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")
    store.record("query", "email-first", 4, 2, 1)

    store.record_sent("query", "email-first", 2)

    top = store.top_queries(1)[0]
    assert top["search_count"] == 1
    assert top["successful_send_count"] == 2
    assert top["consecutive_zero_email_count"] == 0


def test_record_sent_updates_existing_query_when_source_label_differs(tmp_path):
    store = KeywordPerformanceStore(tmp_path / "performance.json")
    store.record("UK Sensor Impressum", "Exhibition/Directory", 4, 2, 1)

    store.record_sent("UK Sensor Impressum", "Email-First Web")

    saved = json.loads((tmp_path / "performance.json").read_text())
    assert len(saved["queries"]) == 1
    record = next(iter(saved["queries"].values()))
    assert record["successful_send_count"] == 1
